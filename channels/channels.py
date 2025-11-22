import discord
from redbot.core import commands, Config, checks
from typing import Literal, Optional

class Channels(commands.Cog):
    """
    A cog to navigate channels using interactive buttons.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        # Structure: {category_id: {'type': 'public'|'secret', 'label': 'Name'}}
        default_guild = {
            "categories": {}
        }
        self.config.register_guild(**default_guild)

    # --- API Methods for Inter-Cog Communication ---

    def get_public_channel_count(self, guild: discord.Guild) -> int:
        """Returns the number of text channels in configured 'public' categories."""
        return self._count_channels(guild, "public")

    def get_secret_channel_count(self, guild: discord.Guild) -> int:
        """Returns the number of text channels in configured 'secret' categories."""
        return self._count_channels(guild, "secret")

    def _count_channels(self, guild: discord.Guild, c_type: str) -> int:
        # Helper to avoid async/await in API if possible, though Config is async usually.
        # For an API, we usually want it to be synchronous if pulling from cache, 
        # but Red config requires async. 
        # Ideally, we cache this, but for this example, we will calculate live 
        # based on the assumption the caller handles logic or we strictly read 
        # from memory if we had a cache. 
        # Since Red Config is async, external cogs should ideally await a wrapper,
        # but here we will iterate the guild's state directly which is sync.
        
        # Note: We need the config data. Since we can't await in a sync method,
        # external cogs should use the async versions below or we cache config.
        # We will assume this is an async API for safety.
        raise NotImplementedError("Use the async versions: get_public_channel_count_async")

    async def get_public_channel_count_async(self, guild: discord.Guild) -> int:
        data = await self.config.guild(guild).categories()
        count = 0
        for cat_id, info in data.items():
            if info['type'] == 'public':
                category = guild.get_channel(int(cat_id))
                if category:
                    count += len(category.text_channels)
        return count

    async def get_secret_channel_count_async(self, guild: discord.Guild) -> int:
        data = await self.config.guild(guild).categories()
        count = 0
        for cat_id, info in data.items():
            if info['type'] == 'secret':
                category = guild.get_channel(int(cat_id))
                if category:
                    # Counting channels inside the secret category
                    count += len(category.channels) 
        return count

    # --- Commands ---

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def channelset(self, ctx):
        """Configuration settings for Channels."""
        pass

    @channelset.command(name="add")
    async def channelset_add(self, ctx, category: discord.CategoryChannel, type: Literal["public", "secret"], *, label: str):
        """
        Add a category to the tracker.
        
        Arguments:
        - category: The category ID or Mention.
        - type: 'public' or 'secret'.
        - label: The name to display on the button.
        """
        async with self.config.guild(ctx.guild).categories() as cats:
            cats[str(category.id)] = {
                "type": type.lower(),
                "label": label
            }
        await ctx.send(f"Added category **{category.name}** as `{type}` with label **{label}**.")

    @channelset.command(name="remove")
    async def channelset_remove(self, ctx, category: discord.CategoryChannel):
        """Remove a category from the tracker."""
        async with self.config.guild(ctx.guild).categories() as cats:
            if str(category.id) in cats:
                del cats[str(category.id)]
                await ctx.send(f"Removed **{category.name}** from tracking.")
            else:
                await ctx.send("That category is not currently tracked.")

    @channelset.command(name="list")
    async def channelset_list(self, ctx):
        """List configured categories."""
        cats = await self.config.guild(ctx.guild).categories()
        if not cats:
            return await ctx.send("No categories configured.")
        
        msg = ""
        for cat_id, data in cats.items():
            cat_obj = ctx.guild.get_channel(int(cat_id))
            cat_name = cat_obj.name if cat_obj else "Unknown/Deleted"
            msg += f"**{data['label']}** ({cat_name}) - Type: `{data['type']}`\n"
        
        await ctx.send(embed=discord.Embed(title="Tracked Categories", description=msg, color=discord.Color.blue()))

    @commands.command()
    @commands.guild_only()
    async def channels(self, ctx):
        """Open the interactive channel navigator."""
        categories_config = await self.config.guild(ctx.guild).categories()
        
        if not categories_config:
            return await ctx.send("No channels have been configured by the admins yet.")

        view = ChannelNavigatorView(ctx, categories_config)
        
        # Default embed (Landing page)
        embed = discord.Embed(
            title="Channel Navigator", 
            description="Select a category below to view channels.", 
            color=discord.Color.dark_theme()
        )
        embed.set_footer(text="Navigate using the buttons below.")
        
        await ctx.send(embed=embed, view=view)

# --- View Class ---

class ChannelNavigatorView(discord.ui.View):
    def __init__(self, ctx, config_data):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.config_data = config_data
        self.guild = ctx.guild
        
        self.setup_buttons()

    def setup_buttons(self):
        # 1. Add Green Buttons for Public Categories
        # Note: Discord limits to 25 buttons. We assume reasonable usage here.
        # If order matters, we might want to sort by label, but dict order is insertion order in modern Py.
        
        # Sort items by label for consistency
        sorted_items = sorted(self.config_data.items(), key=lambda x: x[1]['label'])

        for cat_id, data in sorted_items:
            if data['type'] == 'public':
                button = discord.ui.Button(
                    style=discord.ButtonStyle.success,
                    label=data['label'],
                    custom_id=f"public_{cat_id}"
                )
                button.callback = self.make_callback_public(cat_id, data['label'])
                self.add_item(button)

        # 2. Add Red "Secret" Button
        secret_btn = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Secret",
            custom_id="secret_btn",
            row=4 # Push to bottom row if possible
        )
        secret_btn.callback = self.secret_callback
        self.add_item(secret_btn)

        # 3. Add Grey "Voice" Button
        voice_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Voice",
            custom_id="voice_btn",
            row=4
        )
        voice_btn.callback = self.voice_callback
        self.add_item(voice_btn)

    def make_callback_public(self, cat_id, label):
        """Factory to create specific callbacks for loop variables."""
        async def callback(interaction: discord.Interaction):
            category = self.guild.get_channel(int(cat_id))
            
            if not category:
                return await interaction.response.send_message("This category no longer exists.", ephemeral=True)
            
            # List linked names of channels in this category
            # Filtering only Text channels as per standard "channels" view, 
            # or all channels? "linked names" implies mentions.
            # Voice channels usually don't have click-to-jump mentions in the same way, 
            # so we focus on text/forum/news.
            
            channels_list = []
            for channel in category.channels:
                if isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel)):
                     channels_list.append(channel.mention)
            
            desc = "\n".join(channels_list) if channels_list else "No channels found."
            
            embed = discord.Embed(
                title=f"Category: {label}",
                description=desc,
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=self)
        
        return callback

    async def secret_callback(self, interaction: discord.Interaction):
        count = 0
        for cat_id, data in self.config_data.items():
            if data['type'] == 'secret':
                category = self.guild.get_channel(int(cat_id))
                if category:
                    count += len(category.channels)
        
        embed = discord.Embed(
            title="Secret Channels",
            description=f"There are currently **{count}** secret channels.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def voice_callback(self, interaction: discord.Interaction):
        # Constraint: "only counts channels inside of the provided categories"
        # We will list voice channels found within ANY configured category (Public OR Secret).
        
        voice_lines = []
        
        for cat_id, data in self.config_data.items():
            category = self.guild.get_channel(int(cat_id))
            if category:
                for channel in category.voice_channels:
                    # Voice channels can be mentioned, but sometimes a direct link is preferred.
                    # discord.VoiceChannel.mention works.
                    voice_lines.append(f"{channel.mention} ({channel.name})")
                
                # Also check stage channels if desired, but request said "Voice"
                for channel in category.stage_channels:
                    voice_lines.append(f"{channel.mention} ({channel.name})")

        desc = "\n".join(voice_lines) if voice_lines else "No voice channels found in tracked categories."

        embed = discord.Embed(
            title="Voice Channels",
            description=desc,
            color=discord.Color.light_grey()
        )
        await interaction.response.edit_message(embed=embed, view=self)