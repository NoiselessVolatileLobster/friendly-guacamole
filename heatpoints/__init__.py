from .heatpoints import HeatPoints

async def setup(bot):
    await bot.add_cog(HeatPoints(bot))