from .northernlights import NorthernLights

async def setup(bot):
    """
    The setup function required by Red to load the cog.
    It imports the NorthernLights class and adds it to the bot.
    """
    # Instantiate the cog class and register it with the bot
    await bot.add_cog(NorthernLights(bot))