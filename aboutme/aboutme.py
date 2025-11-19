import discord
from redbot.core import commands
from datetime import datetime, timezone

class AboutMe(commands.Cog):
    """A cog to show how long you have been in the server."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.guild_only()
    async def aboutme(self, ctx):
        """Check how long you have been in this server."""
        
        member = ctx.author
        
        # Ensure we have the joined_at data
        if member.joined_at is None:
            return await ctx.send("I couldn't determine when you joined this server.")

        # Calculate the time difference
        # We use timezone-aware UTC to match Discord's timestamps
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days = delta.days

        # Format the date (e.g., January 01, 2023)
        date_str = joined_at.strftime("%B %d, %Y")

        # Create the Embed
        embed = discord.Embed(
            title=ctx.guild.name,
            description=f"Joined on {date_str}.\nThat was **{days}** days ago!",
            color=await ctx.embed_color() # Uses the bot's main color
        )

        # Add the user's avatar as the thumbnail
        embed.set_thumbnail(url=member.display_avatar.url)

        await ctx.send(embed=embed)