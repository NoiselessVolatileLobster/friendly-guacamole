from .questionoftheday import QuestionOfTheDay

async def setup(bot):
    """
    Initializes and adds the QuestionOfTheDay cog to the bot.
    """
    # CRITICAL: We must await the addition of the cog.
    await bot.add_cog(QuestionOfTheDay(bot))