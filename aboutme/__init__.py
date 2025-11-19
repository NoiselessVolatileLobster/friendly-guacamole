from .aboutme import AboutMe

async def setup(bot):
    await bot.add_cog(AboutMe(bot))