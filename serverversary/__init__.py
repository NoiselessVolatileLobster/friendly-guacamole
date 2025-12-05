from .serverversary import Serverversary

async def setup(bot):
    await bot.add_cog(Serverversary(bot))