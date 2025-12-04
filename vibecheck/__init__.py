"""VibeCheck - Give people good and bad vibes"""
import asyncio
from redbot.core.bot import Red

from .vibecheck import VibeCheck


async def setup(bot: Red):
    # Fix: Pass the 'bot' instance to the class constructor
    cog = VibeCheck(bot)
    if asyncio.iscoroutinefunction(bot.add_cog):
        await bot.add_cog(cog)
    else:
        bot.add_cog(cog)