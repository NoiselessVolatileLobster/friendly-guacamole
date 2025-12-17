from .ephemeral import Ephemeral

async def setup(bot):
    await bot.add_cog(Ephemeral(bot))