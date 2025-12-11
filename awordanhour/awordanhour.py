import discord
from redbot.core import commands, Config, checks

class AWordAnHour(commands.Cog):
    """
    A collaborative story-telling cog where users can only add one word at a time.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "channel_id": None,
            "current_sentence": []
        }
        self.config.register_guild(**default_guild)

    @commands.group(name="awah", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def awah(self, ctx):
        """Configuration commands for AWordAnHour."""
        await ctx.send_help()

    @awah.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where the game will be played."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        # Create the Welcome Embed
        embed = discord.Embed(
            title="A Word An Hour",
            description="Welcome to A Word An Hour! You can type *one* word every hour to write a collaborative story. \n At any point, react with the stop emoji 🛑. Once **2 people** react, the sentence will finish!",
            color=discord.Color.blue()
        )
        embed.set_footer(text="The Third Place")
        
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
            
        await channel.send(embed=embed)
        await ctx.send(f"AWordAnHour channel set to {channel.mention}.")

    @awah.command(name="reset")
    async def reset_sentence(self, ctx):
        """Manually reset the current sentence."""
        await self.config.guild(ctx.guild).current_sentence.set([])
        await ctx.send("The sentence has been reset.")

    @awah.command(name="view")
    async def view_sentence(self, ctx):
        """View the configured channel and current sentence in progress."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        words = await self.config.guild(ctx.guild).current_sentence()
        
        # Determine Channel Status
        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_status = f"**Configured Channel:** {channel.mention}"
            else:
                channel_status = "**Configured Channel:** ⚠️ Channel ID set, but channel not found (deleted?)."
        else:
            channel_status = "**Configured Channel:** ❌ No channel configured."

        # Determine Sentence Status
        if words:
            sentence_text = " ".join(words)
            sentence_status = f"**Current Sentence:**\n{sentence_text}"
        else:
            sentence_status = "**Current Sentence:** *Empty*"
            
        await ctx.send(f"{channel_status}\n\n{sentence_status}")

    async def finish_sentence(self, channel, guild):
        """Helper to finalize the sentence and post the embed."""
        words = await self.config.guild(guild).current_sentence()
        
        if not words:
            # If empty, we don't post a "finished" embed, just a small notice
            await channel.send("The sentence was empty, so we are just starting fresh!", delete_after=5)
            return

        text = " ".join(words)
        
        # Create the Finished Sentence Embed
        embed = discord.Embed(
            title="A Word An Hour",
            description=text,
            color=discord.Color.green()
        )
        embed.set_footer(text="The Third Place")
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        await channel.send(embed=embed)
        await self.config.guild(guild).current_sentence.set([])

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not message.guild:
            return

        channel_id = await self.config.guild(message.guild).channel_id()
        if message.channel.id != channel_id:
            return

        content = message.content.strip()

        # Check for Stop via Message (Still strictly enforced if someone types it manually)
        if content == "🛑":
            await self.finish_sentence(message.channel, message.guild)
            return

        # Check word count
        if len(content.split()) > 1:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, one word at a time please!", delete_after=3)
            except discord.Forbidden:
                pass 
            return

        # If we passed checks, add the word
        async with self.config.guild(message.guild).current_sentence() as s:
            s.append(content)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        # Ignore bot reactions
        if payload.member and payload.member.bot:
            return
        
        # Must be in a guild
        if not payload.guild_id:
            return

        # Check emoji first to save resources
        if str(payload.emoji) != "🛑":
            return

        # Get the guild object
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        # Check channel
        config_channel_id = await self.config.guild(guild).channel_id()
        if payload.channel_id != config_channel_id:
            return
        
        # Get channel object
        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        # Fetch the actual message to count reactions
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        # Find the specific reaction object for 🛑
        reaction = discord.utils.get(message.reactions, emoji="🛑")
        
        if not reaction:
            return

        # VOTE CHECK: Only trigger if the count is EXACTLY 2.
        if reaction.count == 2:
            await self.finish_sentence(channel, guild)