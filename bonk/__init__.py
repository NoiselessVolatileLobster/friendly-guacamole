from .bonk import Bonk

__red_end_user_data_statement__ = "This cog stores bonk and jail counts for users."

async def setup(bot):
    cog = Bonk(bot)
    await bot.add_cog(cog)