from .serverlore import ServerLore

async def setup(bot):
    await bot.add_cog(ServerLore(bot))