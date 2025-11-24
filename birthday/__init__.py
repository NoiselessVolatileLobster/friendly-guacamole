from .birthday import Birthday

async def setup(bot):
    # This will load the cog as 'birthday' (the name of the directory/file)
    await bot.add_cog(Birthday(bot))