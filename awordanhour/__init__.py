from .awordanhour import AWordAnHour

async def setup(bot):
    await bot.add_cog(AWordAnHour(bot))