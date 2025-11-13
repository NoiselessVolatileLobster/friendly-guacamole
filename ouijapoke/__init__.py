from .ouijapoke import OuijaPoke

async def setup(bot):
    await bot.add_cog(OuijaPoke(bot))