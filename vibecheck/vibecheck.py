"""Module for the VibeCheck cog."""
import asyncio
import logging
from collections import namedtuple

import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.vibecheck")

__all__ = ["UNIQUE_ID", "VibeCheck"]

UNIQUE_ID = 0x9C02DCC7
MemberInfo = namedtuple("MemberInfo", "id name vibes")


class VibeCheck(getattr(commands, "Cog", object)):
    """Keep track of vibes for all users in the bot's scope.

    Emojis which affect vibes are customised by the owner.
    Upvotes add 1 vibe. Downvotes subtract 1 vibe.
    """

    def __init__(self):
        self.conf = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)
        self.conf.register_user(vibes=0)
        self.conf.register_guild(upvote=None, downvote=None)

    @commands.command()
    @commands.guild_only()
    async def upvote(self, ctx: commands.Context):
        """See this server's upvote emoji."""
        emoji = await self.conf.guild(ctx.guild).upvote()
        if isinstance(emoji, int):
            emoji = ctx.bot.get_emoji(emoji)
        if emoji is None:
            reply = (
                "The upvote emoji in this server is not set."
                " Use `{0}setupvote` to do so (requires `manage emojis`"
                " permission).".format(ctx.prefix)
            )
        else:
            reply = "The upvote emoji in this server is {!s}".format(emoji)
        await ctx.send(reply)

    @commands.command()
    @commands.guild_only()
    async def downvote(self, ctx: commands.Context):
        """See this server's downvote emoji."""
        emoji = await self.conf.guild(ctx.guild).downvote()
        if isinstance(emoji, int):
            emoji = ctx.bot.get_emoji(emoji)
        if emoji is None:
            reply = (
                "The downvote emoji in this server is not set. Admins use"
                " `{0}setdownvote` to do so (requires `manage emojis`"
                " permission).".format(ctx.prefix)
            )
        else:
            reply = "The downvote emoji in this server is {!s}".format(emoji)
        await ctx.send(reply)

    @commands.command()
    async def vibeboard(self, ctx: commands.Context, top: int = 10):
        """Prints out the Vibes leaderboard.

        Defaults to top 10. Use negative numbers to reverse the leaderboard.
        """
        reverse = True
        if top == 0:
            top = 10
        elif top < 0:
            reverse = False
            top = -top
        members_sorted = sorted(
            await self._get_all_members(ctx.bot), key=lambda x: x.vibes, reverse=reverse
        )
        if len(members_sorted) < top:
            top = len(members_sorted)
        topten = members_sorted[:top]
        highscore = ""
        place = 1
        for member in topten:
            highscore += str(place).ljust(len(str(top)) + 1)
            highscore += "{} | ".format(member.name).ljust(18 - len(str(member.vibes)))
            highscore += str(member.vibes) + "\n"
            place += 1
        if highscore != "":
            for page in pagify(highscore, shorten_by=12):
                await ctx.send(box(page, lang="py"))
        else:
            await ctx.send("No one has any vibes 🙁")

    @commands.command(name="vibes")
    @commands.guild_only()
    async def get_vibes(self, ctx: commands.Context, user: discord.Member = None):
        """Check a user's vibes.

        Leave [user] blank to see your own vibes.
        """
        if user is None:
            user = ctx.author
        vibes = await self.conf.user(user).vibes()
        await ctx.send("{0} vibe score is: {1}".format(user.display_name, vibes))

    @commands.command(name="setupvote")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_emojis=True)
    async def set_upvote(self, ctx: commands.Context):
        """Set the upvote emoji in this server.

        Only the first reaction from the command author will be added.
        """
        await self._interactive_emoji_setup(ctx, "upvote")

    @commands.command(name="setdownvote")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_emojis=True)
    async def set_downvote(self, ctx: commands.Context):
        """Add a downvote emoji by reacting to the bot's response.

        Only the first reaction from the command author will be added.
        """
        await self._interactive_emoji_setup(ctx, "downvote")

    async def _interactive_emoji_setup(self, ctx: commands.Context, type_: str):
        msg = await ctx.send("React to my message with the new {} emoji!".format(type_))
        try:
            reaction, _ = await ctx.bot.wait_for(
                "reaction_add",
                check=lambda r, u: u == ctx.author and r.message.id == msg.id,
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            await ctx.send("Setting the {} emoji was cancelled.".format(type_))
            return
        emoji = reaction.emoji
        if isinstance(emoji, discord.Emoji):
            save = emoji.id
        elif isinstance(emoji, discord.PartialEmoji):
            await ctx.send(
                "Setting the {} failed. This is a custom emoji"
                " which I cannot see.".format(type_)
            )
            return
        else:
            save = emoji
        value = getattr(self.conf.guild(ctx.guild), type_)
        await value.set(save)
        await ctx.send(
            "Done! The {} emoji in this server is now {!s}".format(type_, emoji)
        )

    @commands.command(name="resetvibes")
    @checks.is_owner()
    async def reset_vibes(self, ctx: commands.Context, user: discord.Member):
        """Resets a user's vibes."""
        log.debug("Resetting %s's vibes", str(user))
        # noinspection PyTypeChecker
        await self.conf.user(user).vibes.set(0)
        await ctx.send("{}'s vibes has been reset to 0.".format(user.name))


    @commands.command(name="goodvibes")
    async def good_vibes(self, ctx: commands.Context, user: discord.User, amount: int):
        """Give someone good vibes"""
        log.debug("{} got good vibes from {}!".format(user.name,ctx.author))

        if user and user.id == ctx.author.id:
            return await ctx.send(("You can't give good vibes to yourself!"), ephemeral=True)
        if user and user.bot:
            return await ctx.send(("Awe, I appreciate it, but you can't give ME good vibes!"), ephemeral=True)

        settings = self.conf.user(user)
        vibes = await settings.vibes()
        await settings.vibes.set(vibes + amount)
        await ctx.send("You sent good vibes to {}!".format(user.name))

    @commands.command(name="badvibes")
    async def bad_vibes(self, ctx: commands.Context, user: discord.User, amount: int):
        """Give someone bad vibes"""
        log.debug("{} got bad vibes from {}!".format(user.name,ctx.author))

        if user and user.id == ctx.author.id:
            return await ctx.send(("You can't give bad vibes to yourself!"), ephemeral=True)
        if user and user.bot:
            return await ctx.send(("Now listen here, you little shit. You can't give ME bad vibes"), ephemeral=True)

        settings = self.conf.user(user)
        vibes = await settings.vibes()
        await settings.vibes.set(vibes - amount)
        await ctx.send("You sent bad vibes to {}!".format(user.name))

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """Fires when the bot sees a reaction being added, and updates vibes.

        Ignores Private Channels and users reacting to their own message.
        """
        await self._check_reaction(reaction, user, added=True)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        """Fires when the bot sees a reaction being removed, and updates vibes.

        Ignores Private Channels and users reacting to their own message.
        """
        await self._check_reaction(reaction, user, added=False)

    async def _check_reaction(
        self, reaction: discord.Reaction, user: discord.User, *, added: bool
    ):
        message = reaction.message
        (author, channel, guild) = (message.author, message.channel, message.guild)
        if (
            author == user
            or user.bot
            or isinstance(channel, discord.abc.PrivateChannel)
        ):
            return
        emoji = reaction.emoji
        upvote = await self._is_upvote(guild, emoji)
        if upvote is not None:
            await self._add_vibes(author, 1 if upvote == added else -1)

    async def _add_vibes(self, user: discord.User, amount: int):
        settings = self.conf.user(user)
        vibes = await settings.vibes()
        await settings.vibes.set(vibes + amount)

    async def _get_emoji_id(self, guild: discord.Guild, *, upvote: bool):
        if upvote:
            emoji = await self.conf.guild(guild).upvote()
        else:
            emoji = await self.conf.guild(guild).downvote()
        return emoji

    async def _is_upvote(self, guild: discord.Guild, emoji):
        """Check if the given emoji is an upvote.

        Returns True if the emoji is the upvote emoji, False f it is the
        downvote emoji, and None otherwise.
        """
        upvote = await self.conf.guild(guild).upvote()
        downvote = await self.conf.guild(guild).downvote()
        if isinstance(upvote, int) and isinstance(emoji, discord.Emoji):
            if emoji.id == upvote:
                return True
            if emoji == downvote:
                return False
        if emoji == upvote:
            return True
        elif emoji == downvote:
            return False

    async def _get_all_members(self, bot):
        """Get a list of members which have vibes.

        Returns a list of named tuples with values for `name`, `id`, `vibes`.
        """
        ret = []
        for user_id, conf in (await self.conf.all_users()).items():
            vibes = conf.get("vibes")
            if not vibes:
                continue
            user = bot.get_user(user_id)
            if user is None:
                continue
            ret.append(MemberInfo(id=user_id, name=str(user), vibes=vibes))
        return ret
    
    # --- Public API Methods ---

    async def get_vibe_score(self, user_id: int) -> int:
        """
        Public API to get the vibe score of a user.
        
        This method is designed to be used by other cogs.

        Parameters
        ----------
        user_id : int
            The Discord ID of the user.

        Returns
        -------
        int
            The user's vibe score (defaults to 0 if not found).
        """
        return await self.conf.user_from_id(user_id).vibes()