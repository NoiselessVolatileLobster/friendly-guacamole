from .hibernate import Hibernate

async def setup(bot):
    await bot.add_cog(Hibernate(bot))