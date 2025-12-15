from .serverbadge import ServerBadge

async def setup(bot):
    await bot.add_cog(ServerBadge(bot))