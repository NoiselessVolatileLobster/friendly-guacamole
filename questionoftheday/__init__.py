from .questionoftheday import QuestionOfTheDay

def setup(bot):
    """Entry point for the RedBot Cog."""
    bot.add_cog(QuestionOfTheDay(bot))