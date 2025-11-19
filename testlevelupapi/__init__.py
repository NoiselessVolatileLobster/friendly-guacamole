from .TestLevelUpAPI import TestLevelUpAPI

# The `setup` function is the entry point for the bot to load the cog.
async def setup(bot):
    await bot.add_cog(TestLevelUpAPI(bot))