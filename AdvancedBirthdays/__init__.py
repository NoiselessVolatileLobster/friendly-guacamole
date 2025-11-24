from .birthdays import AdvancedBirthdays

async def setup(bot):
    await bot.add_cog(AdvancedBirthdays(bot))