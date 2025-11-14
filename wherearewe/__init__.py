from .wherearewe import WhereAreWe

async def setup(bot):
    """
    Sets up the WhereAreWe cog by importing the main class from wherearewe.py.
    """
    await bot.add_cog(WhereAreWe(bot))