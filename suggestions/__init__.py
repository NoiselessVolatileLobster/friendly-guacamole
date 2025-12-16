from .suggestions import Suggestions

__red_end_user_data_statement__ = (
    "This cog stores user IDs, suggestions made, and voting history for statistical purposes."
)

async def setup(bot):
    await bot.add_cog(Suggestions(bot))