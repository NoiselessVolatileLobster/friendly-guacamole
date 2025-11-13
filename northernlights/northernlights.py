import discord
import time
from redbot.core import commands
from redbot.core.utils.chat_formatting import humanize_number

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
        # Get the current Unix epoch time in seconds as an integer
        current_time = int(time.time())
        
        # Construct the final URL with the cache-busting parameter
        image_url = f"{self.BASE_URL}{current_time}"

        # Create the embed for a visually appealing message
        embed = discord.Embed(
            title=":sparkles: Northern Hemisphere Aurora Forecast (Ovation)",
            description="The map below shows the predicted location and intensity of the Aurora Borealis based on the latest data.",
            color=discord.Color.blue()
        )
        
        # Set the dynamic image URL
        embed.set_image(url=image_url)
        
        # Add a note about the source and the refresh time
        embed.set_footer(
            text=f"Source: NOAA | Refreshed: {humanize_number(current_time)}"
        )

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            # Fallback if the bot can't send embeds (rare, but good practice)
            await ctx.send(f"Couldn't send the embed, but here is the link: {image_url}")

def setup(bot):
    """Adds the NorthernLights cog to the bot."""
    bot.add_cog(NorthernLights(bot))