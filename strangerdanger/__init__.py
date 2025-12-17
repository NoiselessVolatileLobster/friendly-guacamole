from .strangerdanger import StrangerDanger

async def setup(bot):
    await bot.add_cog(StrangerDanger(bot))