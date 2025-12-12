import discord
import time
import re
from redbot.core import commands, Config

class PizzaMention(commands.Cog):
    """
    Tracks how many days since a specific keyword was mentioned.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9812374123, force_registration=True)
        
        default_guild = {
            "last_mention": 0,
            "keyword": "pizza"
        }
        
        self.config.register_guild(**default_guild)

    @commands.command()
    @commands.admin_or_permissions(administrator=True)
    async def pizzastart(self, ctx: commands.Context, timestamp: str):
        """
        Set the last date and time the keyword was mentioned.
        
        Usage: [p]pizzastart <t:timestamp>
        Example: [p]pizzastart <t:1700000000>
        """
        # Regex to find digits inside <t: ... >
        match = re.search(r"<t:(\d+)", timestamp)
        
        if match:
            ts = int(match.group(1))
            await self.config.guild(ctx.guild).last_mention.set(ts)
            # :F formats it as a full date/time in Discord
            await ctx.send(f"Timer reset. Last mention set to: <t:{ts}:F>")
        else:
            await ctx.send("Invalid format. Please use a Discord timestamp (e.g., `<t:1733000000>`).")

    @commands.command()
    @commands.admin_or_permissions(administrator=True)
    async def pizzaword(self, ctx: commands.Context, word: str):
        """
        Set the keyword to track. Default is 'pizza'.
        """
        await self.config.guild(ctx.guild).keyword.set(word)
        await ctx.send(f"I am now tracking the word: **{word}**")

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Check if we have permission to send messages in this channel
        if not message.channel.permissions_for(message.guild.me).send_messages:
            return

        keyword = await self.config.guild(message.guild).keyword()
        
        # Case-insensitive check
        if keyword.lower() in message.content.lower():
            
            current_time = int(time.time())
            last_time = await self.config.guild(message.guild).last_mention()
            
            diff_seconds = current_time - last_time
            days = int(diff_seconds // 86400) 
            
            # Anti-spam: Only post if > 24 hours (86400 seconds)
            if diff_seconds > 86400:
                await message.channel.send(
                    f"We made it {days} days without talking about {keyword}."
                )
            
            # Reset timer regardless of whether we posted
            await self.config.guild(message.guild).last_mention.set(current_time)