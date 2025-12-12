from .snowball import Snowball

__red_end_user_data_statement__ = "This cog stores user statistics for the snowball game (health, inventory, stats)."

async def setup(bot):
    await bot.add_cog(Snowball(bot))