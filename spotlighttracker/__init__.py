from .spotlighttracker import SpotlightTracker

__red_end_user_data_statement__ = "This cog stores user IDs to track voice participation statistics during D&D sessions."

async def setup(bot):
    await bot.add_cog(SpotlightTracker(bot))