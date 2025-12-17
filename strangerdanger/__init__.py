from .strangerdanger import StrangerDanger

async def setup(bot):
    bot.add_cog(StrangerDanger(bot))