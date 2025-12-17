from .cotm import CraftOfTheMonth

async def setup(bot):
    cog = CraftOfTheMonth(bot)
    await bot.add_cog(cog)