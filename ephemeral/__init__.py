from .ephemeral import Ephemeral

async def setup(bot):
    """Adds the Ephemeral cog to Red."""
    await bot.add_cog(Ephemeral(bot))