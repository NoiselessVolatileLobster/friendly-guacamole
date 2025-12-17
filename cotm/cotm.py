import discord
import logging
from typing import Optional, Union

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.NoiselessVolatileLobster.craftofthemonth")

# --- Step 3: The Modal (Text Input Only) ---
class SignupModal(discord.ui.Modal):
    def __init__(self, cog, selected_month: str):
        # We set the title dynamically to show the month they picked
        super().__init__(title=f"Sign up for {selected_month}")
        self.cog = cog
        self.selected_month = selected_month

    craft = discord.ui.TextInput(
        label="Craft Description",
        placeholder="What will you be teaching?",
        min_length=3,
        max_length=50,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        
        async with self.cog.config.guild(guild).signups() as signups:
            signups[str(user.id)] = {
                "month": self.selected_month,
                "craft": self.craft.value,
                "user_name": user.display_name,
                "user_id": user.id
            }

        await interaction.response.send_message(
            f"✅ You have successfully signed up to teach **{self.craft.value}** in **{self.selected_month}**!",
            ephemeral=True
        )

# --- Step 2: The Dropdown Menu ---
class MonthSelect(discord.ui.Select):
    def __init__(self, cog):
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        options = [discord.SelectOption(label=m, value=m) for m in months]
        super().__init__(placeholder="Select a month...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        # Once they pick a month, we immediately show the Modal
        selected_month = self.values[0]
        await interaction.response.send_modal(SignupModal(self.cog, selected_month))

class MonthSelectView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=60) # Short timeout since this is just a quick menu
        self.add_item(MonthSelect(cog))

# --- Step 1: The Main Button ---
class SignupView(discord.ui.View):
    def __init__(self, cog):
        # Persistent view needs timeout=None
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="📅 Sign Up", style=discord.ButtonStyle.primary, custom_id="cotm:signup_button")
    async def signup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        required_level = await self.cog.config.guild(guild).instructor_level_req()
        
        # Await the level check (Fix for TypeError)
        user_level = await self.cog.get_user_level(member)
        
        if user_level < required_level:
            await interaction.response.send_message(
                f"⛔ You need to be level **{required_level}** to sign up as an instructor. (You are level {user_level})",
                ephemeral=True
            )
            return

        # Send the Month Select View first (Ephemeral)
        view = MonthSelectView(self.cog)
        await interaction.response.send_message(
            "Please select which month you would like to sign up for:", 
            view=view, 
            ephemeral=True
        )

# --- Main Cog ---
class CraftOfTheMonth(commands.Cog):
    """
    Manage Craft of the Month signups and instructor lists.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987123654, force_registration=True)
        
        default_guild = {
            "channel_id": None,
            "user_level_req": 0,
            "instructor_level_req": 0,
            "signups": {}
        }
        self.config.register_guild(**default_guild)
        
        # Re-register the persistent view on bot restart
        self.bot.add_view(SignupView(self))

    async def get_user_level(self, member: discord.Member) -> int:
        """
        Attempts to get the user's level from Vrt's LevelUp cog.
        Returns 0 if cog not found or user has no data.
        """
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        
        try:
            # Await the external call
            return await levelup.get_level(member)
        except AttributeError:
            return 0
            
        return 0

    @commands.group(name="cotm")
    @commands.guild_only()
    async def cotm(self, ctx):
        """Craft of the Month commands."""
        pass

    @cotm.command(name="list")
    async def cotm_list(self, ctx):
        """Show the current list of instructor signups."""
        required_level = await self.config.guild(ctx.guild).user_level_req()
        
        # Await the level check
        user_level = await self.get_user_level(ctx.author)
        
        if user_level < required_level:
            await ctx.send(f"⛔ You need to be level **{required_level}** to view this list.")
            return

        signups = await self.config.guild(ctx.guild).signups()
        
        if not signups:
            await ctx.send("No instructors have signed up yet.")
            return

        headers = ["Month", "Craft", "Instructor"]
        data = []
        
        months_order = {
            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
        }
        
        sorted_items = sorted(
            signups.items(), 
            key=lambda x: months_order.get(x[1]['month'], 99)
        )

        for uid, info in sorted_items:
            data.append([info['month'], info['craft'], info['user_name']])

        col_widths = [len(h) for h in headers]
        for row in data:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(cell)))

        def make_row(row_data):
            return " | ".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(row_data))

        separator = "-+-".join("-" * w for w in col_widths)
        
        table_str = f"{make_row(headers)}\n{separator}\n"
        for row in data:
            table_str += f"{make_row(row)}\n"

        embed = discord.Embed(
            title="Craft of the Month: Signups",
            description=box(table_str, lang="prolog"),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.group(name="cotmset")
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def cotmset(self, ctx):
        """Configuration commands for CraftOfTheMonth."""
        pass

    @cotmset.command(name="channel")
    async def cotmset_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where the signup sheet is located."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"✅ Signup channel set to {channel.mention}.")

    @cotmset.command(name="userlevel")
    async def cotmset_userlevel(self, ctx, level: int):
        """Set the level requirement to view the user list."""
        if level < 0:
            return await ctx.send("Level cannot be negative.")
        await self.config.guild(ctx.guild).user_level_req.set(level)
        await ctx.send(f"✅ Level required to view list set to: **{level}**")

    @cotmset.command(name="instructorlevel")
    async def cotmset_instructorlevel(self, ctx, level: int):
        """Set the level requirement to sign up as an instructor."""
        if level < 0:
            return await ctx.send("Level cannot be negative.")
        await self.config.guild(ctx.guild).instructor_level_req.set(level)
        await ctx.send(f"✅ Level required to be an instructor set to: **{level}**")

    @cotmset.command(name="post")
    async def cotmset_post(self, ctx):
        """Post the instructor signup sheet."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            await ctx.send("❌ Please set a channel first using `[p]cotmset channel`.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured channel no longer exists.")
            return

        embed = discord.Embed(
            title="Craft Of The Month",
            description="Please click the button below if you'd like to lead a crafting session.\nSee the pinned message for more information.",
            color=discord.Color.green()
        )
        embed.set_footer(text=ctx.guild.name)

        view = SignupView(self)
        await channel.send(embed=embed, view=view)
        await ctx.send(f"✅ Signup sheet posted in {channel.mention}.")

    @cotmset.command(name="clear")
    async def cotmset_clear(self, ctx):
        """Clear all current signups."""
        await self.config.guild(ctx.guild).signups.set({})
        await ctx.send("✅ All signups have been cleared.")

    @cotmset.command(name="view")
    async def cotmset_view(self, ctx):
        """View all configured settings."""
        conf = await self.config.guild(ctx.guild).all()
        
        channel_mention = f"<#{conf['channel_id']}>" if conf['channel_id'] else "Not Set"
        
        settings = [
            ["Setting", "Value"],
            ["Channel", channel_mention],
            ["User Level Req", str(conf['user_level_req'])],
            ["Instructor Level Req", str(conf['instructor_level_req'])],
            ["Total Signups", str(len(conf['signups']))]
        ]
        
        col_widths = [len(r[0]) for r in settings]
        col_widths[0] = max(col_widths[0], 20) 
        
        desc = ""
        for row in settings:
            desc += f"**{row[0]}:** {row[1]}\n"

        embed = discord.Embed(
            title="CraftOfTheMonth Settings",
            description=desc,
            color=discord.Color.light_grey()
        )
        await ctx.send(embed=embed)