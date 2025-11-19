import discord
from redbot.core import commands
from redbot.core.bot import Red

# NOTE ON METACLASSES:
# In a real Red environment, if LevelUpShared is a required metaclass
# you would import and use it like this (assuming the LevelUp cog is installed):
# from levelup.shared import LevelUpShared
# class TestLevelUpAPI(commands.Cog, metaclass=LevelUpShared):
#
# Since we cannot access the file, we are using standard commands.Cog inheritance.

class TestLevelUpAPI(commands.Cog):
    """
    A utility cog to test interactions with the LevelUp cog's internal API.
    """

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    @commands.guild_only()
    async def testxp(self, ctx: commands.Context, member: discord.Member):
        """
        Adds 1000 XP to a specified member using the LevelUp API.
        
        Usage: [p]testxp <user_id_or_mention>
        """
        
        # 1. Retrieve the LevelUp cog instance
        levelup_cog = self.bot.get_cog("LevelUp")

        if levelup_cog is None:
            return await ctx.send(
                "The LevelUp cog is not loaded. Please ensure it is installed and loaded to run this test."
            )

        # Basic check to ensure the required method is available
        if not hasattr(levelup_cog, "add_xp"):
            return await ctx.send(
                "The 'LevelUp' cog is loaded but does not expose the required API method (`add_xp`)."
            )
        
        # We previously tried to call check_levelups, but it is a private helper 
        # that requires internal arguments (profile, conf), leading to the error.
        # We will now rely solely on add_xp, which should be the intended public API hook.

        await ctx.send(
            f"API Test: Attempting to add 1000 XP to **{member.display_name}** (`{member.id}`)."
        )

        try:
            # 2. Call the add_xp API method
            # The method signature is expected to be add_xp(member, amount)
            new_xp = await levelup_cog.add_xp(member, 1000)

            # NOTE: We removed the explicit call to levelup_cog.check_levelups(member)
            # as it caused an error due to missing required positional arguments.
            # We assume the add_xp method handles the level-up check internally.

            await ctx.send(
                f"✅ **Success!** Added 1000 XP to **{member.display_name}**. "
                f"New total XP (returned by API): `{new_xp}`. "
                f"If LevelUp handles checks internally, the member may have leveled up."
            )

        except Exception as e:
            # Catch any exceptions that might occur during the API calls (e.g., if the method signature changed)
            await ctx.send(f"❌ An error occurred during the LevelUp API interaction: ```\n{e}\n```")