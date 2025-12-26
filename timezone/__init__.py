from .timezone import Timezone

async def setup(bot):
    await bot.add_cog(Timezone(bot))