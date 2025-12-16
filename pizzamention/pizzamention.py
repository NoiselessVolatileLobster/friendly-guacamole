import discord
import time
import re
from redbot.core import commands, Config

class PizzaMention(commands.Cog):
    """
    Tracks how many days, hours, and minutes since a specific keyword was mentioned.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9812374123, force_registration=True)
        
        default_guild = {
            "last_mention": 0,
            "keyword": "pizza"
        }
        
        self.config.register_guild(**default_guild)

    @commands.group(name="pizzamentionset", aliases=["pizzaset"])
    @commands.admin_or_permissions(administrator=True)
    async def pizzamentionset(self, ctx: commands.Context):
        """
        Configuration settings for PizzaMention.
        """
        pass

    @pizzamentionset.command(name="start")
    async def pizzamentionset_start(self, ctx: commands.Context, timestamp: str):
        """
        Set the last date and time the keyword was mentioned.
        
        Usage: [p]pizzamentionset start <t:timestamp>
        Example: [p]pizzamentionset start <t:1700000000>
        """
        match = re.search(r"<t:(\d+)", timestamp)
        
        if match:
            ts = int(match.group(1))
            await self.config.guild(ctx.guild).last_mention.set(ts)
            await ctx.send(f"Timer reset. Last mention set to: <t:{ts}:F>")
        else:
            await ctx.send("Invalid format. Please use a Discord timestamp (e.g., `<t:1733000000>`).")

    @pizzamentionset.command(name="word")
    async def pizzamentionset_word(self, ctx: commands.Context, word: str):
        """
        Set the keyword to track. Default is 'pizza'.
        """
        await self.config.guild(ctx.guild).keyword.set(word)
        await ctx.send(f"I am now tracking the word: **{word}**")

    @pizzamentionset.command(name="view")
    async def pizzamentionset_view(self, ctx: commands.Context):
        """
        View the current PizzaMention settings.
        """
        data = await self.config.guild(ctx.guild).all()
        last_mention = data["last_mention"]
        keyword = data["keyword"]
        
        # Calculate current duration for the view command
        current_time = int(time.time())
        if last_mention == 0:
            time_str = "Never (or not set)"
        else:
            diff = current_time - last_mention
            d, h, m = self._calculate_time(diff)
            time_str = f"{d} days, {h} hours, {m} minutes"

        msg = (
            f"**PizzaMention Settings**\n"
            f"**Keyword:** `{keyword}`\n"
            f"**Last Mention:** <t:{last_mention}:F>\n"
            f"**Current Streak:** {time_str}"
        )
        await ctx.send(msg)

    @pizzamentionset.command(name="test")
    async def pizzamentionset_test(self, ctx: commands.Context):
        """
        Test the ANSI alert message in the current channel.
        
        This will generate the alert based on the current timer WITHOUT resetting it.
        """
        keyword = await self.config.guild(ctx.guild).keyword()
        last_mention = await self.config.guild(ctx.guild).last_mention()
        current_time = int(time.time())

        # Handle case where it was never set
        if last_mention == 0:
            last_mention = current_time

        diff_seconds = current_time - last_mention
        days, hours, minutes = self._calculate_time(diff_seconds)
        time_display = f"{days} days, {hours} hours, {minutes} minutes"

        ansi_msg = (
            f"```ansi\n"
            f"This server made it [2;31m[{time_display}][0m without talking about {keyword}\n"
            f"```"
        )
        
        await ctx.send(ansi_msg)

    def _calculate_time(self, seconds):
        """Helper to return days, hours, minutes."""
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        return int(days), int(hours), int(minutes)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not message.channel.permissions_for(message.guild.me).send_messages:
            return

        keyword = await self.config.guild(message.guild).keyword()
        
        if keyword.lower() in message.content.lower():
            
            current_time = int(time.time())
            last_time = await self.config.guild(message.guild).last_mention()
            
            # Default to current time if never set
            if last_time == 0:
                last_time = current_time

            diff_seconds = current_time - last_time
            
            # Anti-spam: Only post if > 24 hours (86400 seconds)
            if diff_seconds > 86400:
                
                days, hours, minutes = self._calculate_time(diff_seconds)
                
                # Construct the time string
                time_display = f"{days} days, {hours} hours, {minutes} minutes"
                
                # ANSI Formatting
                ansi_msg = (
                    f"```ansi\n"
                    f"This server made it [2;31m[{time_display}][0m without talking about {keyword}\n"
                    f"```"
                )
                
                await message.channel.send(ansi_msg)
            
            await self.config.guild(message.guild).last_mention.set(current_time)