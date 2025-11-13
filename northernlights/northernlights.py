import discord
import time
import datetime
from redbot.core import commands

class NorthernLights(commands.Cog):
    """
    Posts the latest Northern Hemisphere Ovation Aurora Forecast,
    using current Unix time to ensure the image is not cached.
    """

    # Base URL for the NOAA Space Weather Prediction Center Ovation map
    BASE_URL = "https://services.swpc.noaa.gov/images/animations/ovation/north/latest.jpg?time="

    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["aurora"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def northernlights(self, ctx: commands.Context):
        """
        Displays the latest forecast image for the Northern Lights (Aurora Borealis).
        """
        # Get the current Unix epoch time in seconds as an integer. This is used for cache-busting the image URL.
        current_time = int(time.time())
        
        # Construct the final URL with the cache-busting parameter
        image_url = f"{self.BASE_URL}{current_time}"

        # Convert the Unix timestamp to a human-readable UTC time string for the footer
        # We use UTC to avoid confusing time zone issues on the server.
        refresh_time_utc = datetime.datetime.utcfromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S UTC')

        # Create the embed for a visually appealing message
        embed = discord.Embed(
            title=":sparkles: Northern Hemisphere Aurora Forecast",
            description="The map below shows the predicted location and intensity of the Aurora Borealis based on the latest data.",
            color=discord.Color.blue()
        )
        
        # Set the dynamic image URL
        embed.set_image(url=image_url)
        
        # Add a note about the source and the refresh time, using the formatted time string
        embed.set_footer(
            text=f"Source: NOAA | Refreshed: {refresh_time_utc}"
        )

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            # Fallback if the bot can't send embeds (rare, but good practice)
            await ctx.send(f"Couldn't send the embed, but here is the link: {image_url}")

def setup(bot):
    """Adds the NorthernLights cog to the bot."""
    bot.add_cog(NorthernLights(bot))