from .about import About

async def setup(bot):
    await bot.add_cog(About(bot))