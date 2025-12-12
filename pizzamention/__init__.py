from .pizzamention import PizzaMention

async def setup(bot):
    await bot.add_cog(PizzaMention(bot))