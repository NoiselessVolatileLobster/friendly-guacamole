from .holiday import HolidayGifts

async def setup(bot):
    await bot.add_cog(HolidayGifts(bot))