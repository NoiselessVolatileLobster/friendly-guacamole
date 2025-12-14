from .bang import Bang

__red_end_user_data_statement__ = "This cog stores user scores and game configuration persistently."

async def setup(bot):
    await bot.add_cog(Bang(bot))