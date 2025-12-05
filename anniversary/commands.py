from __future__ import annotations

import asyncio
import datetime
from collections import defaultdict
from typing import TYPE_CHECKING, Literal, Union

import discord
from redbot.core import Config, commands
from redbot.core.commands import CheckFailure
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import box, pagify, warning
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from rich.table import Table  # type:ignore

from .abc import MixinMeta
from .components.setup import SetupView
from .consts import MAX_BDAY_MSG_LEN, MIN_BDAY_YEAR
from .converters import AnniversaryConverter, TimeConverter
from .utils import channel_perm_check, format_aniv_message, role_perm_check
from .vexutils import get_vex_logger, no_colour_rich_markup
from .vexutils.button_pred import wait_for_yes_no

log = get_vex_logger(__name__)


class AnniversaryCommands(MixinMeta):
    async def setup_check(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            raise CheckFailure("This command can only be used in a server.")
            # this should have been caught by guild only check, but this keeps type checker happy
            # and idk what order decos run in so

        if not await self.check_if_setup(ctx.guild):
            await ctx.send(
                "This command is not available until the cog has been setup. "
                f"Get an admin to use `{ctx.clean_prefix}anset interactive` to get started."
            )
            raise CheckFailure("cog needs setup")

    @commands.guild_only()  # type:ignore
    @commands.before_invoke(setup_check)  # type:ignore
    @commands.hybrid_group(aliases=["aniv"])
    async def anniversary(self, ctx: commands.Context):
        """Set and manage your anniversary."""

    @anniversary.command(aliases=["add"])
    async def set(self, ctx: commands.Context, *, anniversary: AnniversaryConverter):
        """
        Set your Server anniversary.

        You can optionally add in the year, if you are happy to share this.

        If you use a date in the format xx/xx/xx or xx-xx-xx MM-DD-YYYY is assumed.

        **Examples:**
        - `[p]aniv set 24th September`
        - `[p]aniv set 24th Sept 2002`
        - `[p]aniv set 9/24/2002`
        - `[p]aniv set 9-24-2002`
        - `[p]aniv set 9-24`
        """
        # guild only check in group
        if TYPE_CHECKING:
            assert isinstance(ctx.author, discord.Member)

        # year as 1 means year not specified

        if anniversary.year != 1 and anniversary.year < MIN_BDAY_YEAR:
            await ctx.send(f"I'm sorry, but I can't set your anniversary to before {MIN_BDAY_YEAR}.")
            return

        if anniversary > datetime.datetime.utcnow():
            await ctx.send("You can't join the server in the future!")
            return

        async with self.config.member(ctx.author).anniversary() as aniv:
            aniv["year"] = anniversary.year if anniversary.year != 1 else None
            aniv["month"] = anniversary.month
            aniv["day"] = anniversary.day

        if anniversary.year == 1:
            str_aniv = anniversary.strftime("%B %d")
        else:
            str_aniv = anniversary.strftime("%B %d, %Y")

        await ctx.send(f"Your anniversary has been set as {str_aniv}.")

    @anniversary.command(aliases=["delete", "del"])
    async def remove(self, ctx: commands.Context):
        """Remove your anniversary."""
        # guild only check in group
        if TYPE_CHECKING:
            assert isinstance(ctx.author, discord.Member)
            assert ctx.guild is not None

        m = await ctx.send("Are you sure?")
        start_adding_reactions(m, ReactionPredicate.YES_OR_NO_EMOJIS)
        check = ReactionPredicate.yes_or_no(m, ctx.author)  # type:ignore

        try:
            await self.bot.wait_for("reaction_add", check=check, timeout=60)
        except asyncio.TimeoutError:
            for reaction in ReactionPredicate.YES_OR_NO_EMOJIS:
                await m.remove_reaction(reaction, ctx.guild.me)
            return

        if check.result is False:
            await ctx.send("Cancelled.")
            return

        await self.config.member(ctx.author).anniversary.set({})
        await ctx.send("Your anniversary has been removed.")

    @anniversary.command()
    async def upcoming(self, ctx: commands.Context, days: int = 7):
        """View upcoming Server Anniversaries, defaults to 7 days.

        **Examples:**
        - `[p]anniversary upcoming` - default of 7 days
        - `[p]anniversary upcoming 14` - 14 days
        """
        # guild only check in group
        if TYPE_CHECKING:
            assert isinstance(ctx.author, discord.Member)
            assert ctx.guild is not None

        if days < 1 or days > 365:
            await ctx.send("You must enter a number of days greater than 0 and smaller than 365.")
            return

        today_dt = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        all_anniversarys: dict[int, dict[str, dict]] = await self.config.all_members(ctx.guild)

        log.trace("raw data for all anivs: %s", all_anniversarys)

        parsed_anivs: dict[int, list[str]] = defaultdict(list)
        number_day_mapping: dict[int, str] = {}

        async for member_id, member_data in AsyncIter(all_anniversarys.items(), steps=50):
            if not member_data["anniversary"]:  # anniversary removed but user remains in config
                continue
            member = ctx.guild.get_member(member_id)
            if not isinstance(member, discord.Member):
                continue

            anniversary_dt = datetime.datetime(
                year=member_data["anniversary"]["year"] or 1,
                month=member_data["anniversary"]["month"],
                day=member_data["anniversary"]["day"],
            )

            if today_dt.month == anniversary_dt.month and today_dt.day == anniversary_dt.day:
                parsed_anivs[0].append(
                    member.mention
                    + (
                        ""
                        if anniversary_dt.year == 1
                        else f" turns {today_dt.year - anniversary_dt.year}"
                    )
                )
                number_day_mapping[0] = "Today"
                continue

            this_year_aniv = anniversary_dt.replace(year=today_dt.year)
            next_year_aniv = anniversary_dt.replace(year=today_dt.year + 1)
            next_anniversary_dt = this_year_aniv if this_year_aniv > today_dt else next_year_aniv

            diff = next_anniversary_dt - today_dt
            if diff.days > days:
                continue

            next_aniv_year = (
                today_dt.year if today_dt.year == next_anniversary_dt.year else today_dt.year + 1
            )

            parsed_anivs[diff.days].append(
                member.mention
                + (
                    ""
                    if anniversary_dt.year == 1
                    else (f" will turn {next_aniv_year - anniversary_dt.year}")
                )
            )
            number_day_mapping[diff.days] = next_anniversary_dt.strftime("%B %d")

        log.trace("anivs parsed: %s", parsed_anivs)

        if len(parsed_anivs) == 0:
            await ctx.send(f"No upcoming anniversaries in the next {days} days.")
            return

        sorted_parsed_anivs = sorted(parsed_anivs.items(), key=lambda x: x[0])

        embed = discord.Embed(title="Upcoming Anniversaries", colour=await ctx.embed_colour())

        if len(sorted_parsed_anivs) > 25:
            embed.description = "Too many days to display. I've had to stop at 25."
            sorted_parsed_anivs = sorted_parsed_anivs[:25]

        for day, members in sorted_parsed_anivs:
            embed.add_field(name=number_day_mapping.get(day), value="\n".join(members))

        await ctx.send(embed=embed)


class AnniversaryAdminCommands(MixinMeta):
    @commands.guild_only()
    @commands.is_owner()
    @commands.group(hidden=True)
    async def anniversarydebug(self, ctx: commands.Context):
        """Anniversary debug commands."""

    @anniversarydebug.command(name="upcoming")
    async def debug_upcoming(self, ctx: commands.Context):
        await ctx.send_interactive(pagify(str(await self.config.all_members(ctx.guild))), "py")

    @commands.group()
    @commands.guild_only()  # type:ignore
    @commands.admin_or_permissions(manage_guild=True)
    async def anset(self, ctx: commands.Context):
        """
        Anniversary management commands for admins.

        Looking to set your own anniversary? Use `[p]anniversary set`.
        """

    @commands.bot_has_permissions(manage_roles=True)
    @anset.command()
    async def interactive(self, ctx: commands.Context):
        """Start interactive setup"""
        # guild only check in group
        if TYPE_CHECKING:
            assert isinstance(ctx.author, discord.Member)

        await ctx.send("Click below to start.", view=SetupView(ctx.author, self.bot, self.config))

    @anset.command()
    async def settings(self, ctx: commands.Context):
        """View your current settings"""
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None
            assert isinstance(ctx.me, discord.Member)

        table = Table("Name", "Value", title="Settings for this server")

        async with self.config.guild(ctx.guild).all() as conf:
            log.trace("raw config: %s", conf)

            channel = ctx.guild.get_channel(conf["channel_id"])
            table.add_row("Channel", channel.name if channel else "Channel deleted")

            role = ctx.guild.get_role(conf["role_id"])
            table.add_row("Role", role.name if role else "Role deleted")

            if conf["time_utc_s"] is None:
                time = "invalid"
            else:
                time = datetime.datetime.utcfromtimestamp(conf["time_utc_s"]).strftime("%H:%M UTC")
                table.add_row("Time", time)

            table.add_row("Allow role mentions", str(conf["allow_role_mention"]))

            req_role = ctx.guild.get_role(conf["require_role"])
            if req_role:
                table.add_row(
                    "Required role",
                    req_role.name
                    + ". Only users with this role can set their anniversary and have it announced.",
                )
            else:
                table.add_row(
                    "Required role",
                    "Not set. All users can set their anniversary and have it announced.",
                )

            message_w_year = conf["message_w_year"] or "No message set"
            message_wo_year = conf["message_wo_year"] or "No message set"

        warnings = "\n"
        if (error := role is None) or (error := role_perm_check(ctx.me, role)):
            if isinstance(error, bool):
                error = "Role deleted."
            warnings += warning(error + " This may result in repeated notifications.\n")
        if (error := channel is None) or (error := channel_perm_check(ctx.me, channel)):
            if isinstance(error, bool):
                error = "Channel deleted."
            warnings += warning(error + " You won't get anniversary notifications.\n")

        final_table = no_colour_rich_markup(table)
        message = (
            final_table
            + "\nMessage with year:\n"
            + box(message_w_year)
            + "\nMessage without year:\n"
            + box(message_wo_year)
            + warnings
        )
        await ctx.send(message)

    @anset.command()
    async def time(self, ctx: commands.Context, *, time: TimeConverter):
        """
        Set the time of day for the anniversary message.

        Minutes are ignored.

        **Examples:**
        - `[p]anset time 7:00` - set the time to 7:00AM UTC
        - `[p]anset time 12AM` - set the time to midnight UTC
        - `[p]anset time 3PM` - set the time to 3:00PM UTC
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None

        midnight = datetime.datetime.utcnow().replace(
            year=1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )

        time_utc_s = int((time - midnight).total_seconds())

        async with self.config.guild(ctx.guild).all() as conf:
            old = conf["time_utc_s"]
            conf["time_utc_s"] = time_utc_s

            if old is None:
                conf["setup_state"] += 1

        m = (
            "Time set! I'll send the anniversary message and update the anniversary role at"
            f" {time.strftime('%H:%M')} UTC."
        )

        if old is not None:
            old_dt = datetime.datetime.utcfromtimestamp(old)
            if time > old_dt and time > datetime.datetime.utcnow():
                m += (
                    "\n\nThe time you set is after the time I currently send the anniversary message,"
                    " so the anniversary message will be sent for a second time."
                )

        await ctx.send(m)

    @anset.command()
    async def msgwithoutyear(self, ctx: commands.Context, *, message: str):
        """
        Set the message to send when the user did not provide a year.

        If you would like to mention a role, you will need to run `[p]anset rolemention true`.

        **Placeholders:**
        - `{name}` - the user's name
        - `{mention}` - an @ mention of the user

            All the placeholders are optional.

        **Examples:**
        - `[p]anset msgwithoutyear Happy anniversary {mention}!`
        - `[p]anset msgwithoutyear {mention}'s anniversary is today! Happy anniversary {name}.`
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None
            assert isinstance(ctx.author, discord.Member)

        if len(message) > MAX_BDAY_MSG_LEN:
            await ctx.send(
                f"That message is too long! It needs to be under {MAX_BDAY_MSG_LEN} characters."
            )

        try:
            format_aniv_message(message, ctx.author, 1)
        except KeyError as e:
            await ctx.send(
                f"You have a placeholder `{{{e.args[0]}}}` that is invalid. You can only include"
                " `{name}` and `{mention}` for the message without a year."
            )
            return

        async with self.config.guild(ctx.guild).all() as conf:
            if conf["message_wo_year"] is None:
                conf["setup_state"] += 1

            conf["message_wo_year"] = message

        await ctx.send("Message set. Here's how it will look:")
        await ctx.send(
            format_aniv_message(message, ctx.author),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @anset.command()
    async def msgwithyear(self, ctx: commands.Context, *, message: str):
        """
        Set the message to send when the user did provide a year.

        If you would like to mention a role, you will need to run `[p]anset rolemention true`

        **Placeholders:**
        - `{name}` - the user's name
        - `{mention}` - an @ mention of the user
        - `{new_age}` - the user's new age

            All the placeholders are optional.

        **Examples:**
        - `[p]anset msgwithyear {mention} has turned {new_age}, happy anniversary!`
        - `[p]anset msgwithyear {name} is {new_age} today! Happy anniversary {mention}!`
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None
            assert isinstance(ctx.author, discord.Member)

        if len(message) > MAX_BDAY_MSG_LEN:
            await ctx.send(
                f"That message is too long! It needs to be under {MAX_BDAY_MSG_LEN} characters."
            )

        try:
            format_aniv_message(message, ctx.author, 1)
        except KeyError as e:
            await ctx.send(
                f"You have a placeholder `{{{e.args[0]}}}` that is invalid. You can only include"
                " `{name}`, `{mention}` and `{new_age}` for the message with a year."
            )
            return

        async with self.config.guild(ctx.guild).all() as conf:
            if conf["message_w_year"] is None:
                conf["setup_state"] += 1

            conf["message_w_year"] = message

        await ctx.send("Message set. Here's how it will look, if you're turning 20:")
        await ctx.send(
            format_aniv_message(message, ctx.author, 20),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @anset.command()
    async def channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel where the anniversary message will be sent.

        **Example:**
        - `[p]anset channel #anniversarys` - set the channel to #anniversarys
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None
            assert isinstance(ctx.me, discord.Member)

        if channel.permissions_for(ctx.me).send_messages is False:
            await ctx.send(
                "I can't do that because I don't have permissions to send messages in"
                f" {channel.mention}."
            )
            return

        async with self.config.guild(ctx.guild).all() as conf:
            if conf["channel_id"] is None:
                conf["setup_state"] += 1

            conf["channel_id"] = channel.id

        await ctx.send(f"Channel set to {channel.mention}.")

    @commands.bot_has_permissions(manage_roles=True)
    @anset.command()
    async def role(self, ctx: commands.Context, *, role: discord.Role):
        """
        Set the role that will be given to the user on their anniversary.

        You can give the exact name or a mention.

        **Example:**
        - `[p]anset role @Anniversary` - set the role to @Anniversary
        - `[p]anset role Anniversary` - set the role to @Anniversary without a mention
        - `[p]anset role 418058139913063657` - set the role with an ID
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None
            assert isinstance(ctx.me, discord.Member)

        # no need to check hierarchy for author, since command is locked to admins
        if ctx.me.top_role < role:
            await ctx.send(f"I can't use {role.name} because it is higher than my highest role.")
            return

        async with self.config.guild(ctx.guild).all() as conf:
            if conf["role_id"] is None:
                conf["setup_state"] += 1

            conf["role_id"] = role.id

        await ctx.send(f"Role set to {role.name}.")

    @anset.command()
    async def forceset(
        self, ctx: commands.Context, user: discord.Member, *, anniversary: AnniversaryConverter
    ):
        """
        Force-set a specific user's anniversary.

        You can @ mention any user or type out their exact name. If you're typing out a name with
        spaces, make sure to put quotes around it (`"`).

        **Examples:**
        - `[p]anset set @User 1-1-2000` - set the anniversary of `@User` to 1/1/2000
        - `[p]anset set User 1/1` - set the anniversary of `@User` to 1/1/2000
        - `[p]anset set "User with spaces" 1-1` - set the anniversary of `@User with spaces`
            to 1/1
        - `[p]anset set 354125157387344896 1/1/2000` - set the anniversary of `354125157387344896`
            to 1/1/2000
        """
        if anniversary.year != 1 and anniversary.year < MIN_BDAY_YEAR:
            await ctx.send(f"I'm sorry, but I can't set a anniversary to before {MIN_BDAY_YEAR}.")
            return

        if anniversary > datetime.datetime.utcnow():
            await ctx.send("You can't be born in the future!")
            return

        async with self.config.member(user).anniversary() as aniv:
            aniv["year"] = anniversary.year if anniversary.year != 1 else None
            aniv["month"] = anniversary.month
            aniv["day"] = anniversary.day

        if anniversary.year == 1:
            str_aniv = anniversary.strftime("%B %d")
        else:
            str_aniv = anniversary.strftime("%B %d, %Y")

        await ctx.send(f"{user.name}'s anniversary has been set as {str_aniv}.")

    @anset.command()
    async def forceremove(self, ctx: commands.Context, user: discord.Member):
        """Force-remove a user's anniversary."""
        # guild only check in group
        if TYPE_CHECKING:
            assert isinstance(user, discord.Member)
            assert ctx.guild is not None

        m = await ctx.send(f"Are you sure? `{user.name}`'s anniversary will be removed.")
        start_adding_reactions(m, ReactionPredicate.YES_OR_NO_EMOJIS)
        check = ReactionPredicate.yes_or_no(m, ctx.author)  # type:ignore

        try:
            await self.bot.wait_for("reaction_add", check=check, timeout=60)
        except asyncio.TimeoutError:
            for reaction in ReactionPredicate.YES_OR_NO_EMOJIS:
                await m.remove_reaction(reaction, ctx.guild.me)
            return

        if check.result is False:
            await ctx.send("Cancelled.")
            return

        await self.config.member(user).anniversary.set({})
        await ctx.send(f"{user.name}'s anniversary has been removed.")

    @commands.is_owner()
    @anset.command()
    async def zemigrate(self, ctx: commands.Context):
        """
        Import data from ZeCogs'/flare's fork of Anniversarys cog
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None

        if await self.config.guild(ctx.guild).setup_state() != 0:
            m = await ctx.send(
                "You have already started setting the cog up. Are you sure? This will overwrite"
                " your old data for all guilds."
            )

            start_adding_reactions(m, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(m, ctx.author)  # type:ignore
            try:
                await self.bot.wait_for("reaction_add", check=pred, timeout=60)
            except asyncio.TimeoutError:
                await ctx.send("Timeout. Cancelling.")
                return

            if pred.result is False:
                await ctx.send("Cancelling.")
                return

        aniv_conf = Config.get_conf(
            None,
            int(
                "402907344791714442305425963449545260864366380186701260757993729164269683092560089"
                "8581468610241444437790345710548026575313281401238342705437492295956906331"
            ),
            cog_name="Anniversary",
        )

        for guild_id, guild_data in (await aniv_conf.all_guilds()).items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            new_data = {
                "channel_id": guild_data.get("channel", None),
                "role_id": guild_data.get("role", None),
                "message_w_year": "{mention} is now **{new_age} years old**. :tada:",
                "message_wo_year": "It's {mention}'s anniversary today! :tada:",
                "time_utc_s": 0,  # UTC midnight
                "setup_state": 5,
            }
            await self.config.guild(guild).set_raw(value=new_data)

        aniv_conf.init_custom("GUILD_DATE", 2)
        all_member_data = await aniv_conf.custom("GUILD_DATE").all()  # type:ignore
        if "backup" in all_member_data:
            del all_member_data["backup"]

        for guild_id, guild_data in all_member_data.items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            for day, users in guild_data.items():
                for user_id, year in users.items():
                    dt = datetime.datetime.fromordinal(int(day))

                    if year is None or year < MIN_BDAY_YEAR:
                        year = 1

                    try:
                        dt = dt.replace(year=year)
                    except (OverflowError, ValueError):  # flare's users are crazy
                        dt = dt.replace(year=1)

                    new_data = {
                        "year": dt.year if dt.year != 1 else None,
                        "month": dt.month,
                        "day": dt.day,
                    }
                    await self.config.member_from_ids(guild_id, user_id).anniversary.set(new_data)

        await ctx.send(
            "All set. You can now configure the messages and time to send with other commands"
            " under `[p]anset`, if you would like to change it from ZeLarp's. This is per-guild."
        )

    @anset.command()
    async def rolemention(self, ctx: commands.Context, value: bool):
        """
        Choose whether or not to allow role mentions in anniversary messages.

        By default role mentions are suppressed.

        To allow role mentions in the anniversary message, run `[p]anset rolemention true`.
        Disable them with `[p]anset rolemention true`
        """
        await self.config.guild(ctx.guild).allow_role_mention.set(value)
        if value:
            await ctx.send("Role mentions have been enabled.")
        else:
            await ctx.send("Role mentions have been disabled.")

    @anset.command()
    async def requiredrole(self, ctx: commands.Context, *, role: Union[discord.Role, None] = None):
        """
        Set a role that users must have to set their anniversary.

        If users don't have this role then they can't set their
        anniversary and they won't get a role or message on their anniversary.

        If they set their anniversary and then lose the role, their anniversary
        will be stored but will be ignored until they regain the role.

        You can purge anniversarys of users who no longer have the role
        with `[p]anset requiredrolepurge`.

        If no role is provided, the requirement is removed.

        View the current role with `[p]anset settings`.

        **Example:**
        - `[p]anset requiredrole @Subscribers` - set the required role to @Subscribers
        - `[p]anset requiredrole Subscribers` - set the required role to @Subscribers
        - `[p]anset requiredrole` - remove the required role
        """
        if role is None:
            current_role = await self.config.guild(ctx.guild).require_role()
            if current_role:
                await self.config.guild(ctx.guild).require_role.clear()
                await ctx.send(
                    "The required role has been removed. Anniversarys can be set by anyone and will "
                    "always be announced."
                )
            else:
                await ctx.send(
                    "No role is current set. Anniversarys can be set by anyone and will always be "
                    "announced."
                )
                await ctx.send_help()
        else:
            await self.config.guild(ctx.guild).require_role.set(role.id)
            await ctx.send(
                f"The required role has been set to {role.name}. Users without this role no longer"
                " have their anniversary announced."
            )

    @anset.command(name="requiredrolepurge")
    async def requiredrole_purge(self, ctx: commands.Context):
        """Remove anniversarys from the database for users who no longer have the required role.

        If you have a required role set, this will remove anniversarys for users who no longer have it

        Uses without the role are temporarily ignored until they regain the role.

        This command allows you to presently remove their anniversary data from the database.
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None

        required_role: int | Literal[False] = await self.config.guild(ctx.guild).require_role()
        if not required_role:
            await ctx.send(
                "You don't have a required role set. This command is only useful if you have a"
                " required role set."
            )
            return

        role = ctx.guild.get_role(required_role)
        if role is None:
            await ctx.send(
                "The required role has been deleted. This command is only useful if you have a"
                " required role set."
            )
            return

        all_members = await self.config.all_members(ctx.guild)
        purged = 0
        for member_id, member_data in all_members.items():
            member = ctx.guild.get_member(member_id)
            if member is None:
                continue

            if role not in member.roles:
                await self.config.member_from_ids(ctx.guild.id, member_id).anniversary.clear()
                purged += 1

        await ctx.send(f"Purged {purged} users from the database.")

    @anset.command()
    async def stop(self, ctx: commands.Context):
        """
        Stop the cog from sending anniversary messages and giving roles in the server.
        """
        # group has guild check
        if TYPE_CHECKING:
            assert ctx.guild is not None

        confirm = await wait_for_yes_no(
            ctx, "Are you sure you want to stop sending updates and giving roles?"
        )
        if confirm is False:
            await ctx.send("Okay, nothing's changed.")
            return

        await self.config.guild(ctx.guild).clear()

        confirm = await wait_for_yes_no(
            ctx,
            "I've deleted your configuration. Would you also like to delete the data about when"
            " users anniversarys are?",
        )
        if confirm is False:
            await ctx.send("I'll keep that.")
            return

        await self.config.clear_all_members(ctx.guild)
        await ctx.send("Deleted.")
