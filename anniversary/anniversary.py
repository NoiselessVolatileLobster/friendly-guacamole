from __future__ import annotations

import asyncio

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.config import Config

from .abc import CompositeMetaClass
from .commands import AnniversaryAdminCommands, AnniversaryCommands
from .loop import AnniversaryLoop
from .vexutils import format_help, format_info, get_vex_logger
from .vexutils.loop import VexLoop

log = get_vex_logger(__name__)


class Anniversary(
    commands.Cog,
    AnniversaryLoop,
    AnniversaryCommands,
    AnniversaryAdminCommands,
    metaclass=CompositeMetaClass,
):
    """
    Anniversarys

    Set yours and get a message and role on your anniversary!
    """

    __version__ = "1.2.2"
    __author__ = "@vexingvexed"

    def __init__(self, bot: Red) -> None:
        self.bot = bot

        self.config: Config = Config.get_conf(self, 418078199982063626, force_registration=True)
        self.config.register_global(version=0)
        self.config.register_guild(
            time_utc_s=None,
            message_w_year=None,
            message_wo_year=None,
            channel_id=None,
            role_id=None,
            setup_state=0,  # 0 is not setup, 5 is everything setup. this is so it can be steadily
            # incremented with individual setup commands or with the interactive setup, then
            # easily checked
            require_role=False,
            allow_role_mention=False,
        )
        self.config.register_member(anniversary={"year": 1, "month": 1, "day": 1})

        self.loop_meta = VexLoop("Anniversary loop", 60 * 60)
        self.loop = self.bot.loop.create_task(self.anniversary_loop())
        self.role_manager = self.bot.loop.create_task(self.anniversary_role_manager())
        self.coro_queue = asyncio.Queue()

        self.ready = asyncio.Event()

        bot.add_dev_env_value("anniversary", lambda _: self)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """Thanks Sinbad."""
        return format_help(self, ctx)

    async def cog_unload(self):
        self.loop.cancel()
        self.role_manager.cancel()

        try:
            self.bot.remove_dev_env_value("anniversary")
        except KeyError:
            pass

    async def red_delete_data_for_user(self, **kwargs) -> None:
        # will delete for any requester
        target_u_id: int | None = kwargs.get("user_id")
        if target_u_id is None:
            log.info("Unable to delete user data for user with ID 0 because it's invalid.")
            return

        hit = False

        all_data = await self.config.all_members()
        for g_id, g_data in all_data.items():
            if target_u_id in g_data.keys():
                hit = True
                await self.config.member_from_ids(g_id, target_u_id).clear()
                log.debug(
                    "Deleted user data for user with ID %s in guild with ID %s.", target_u_id, g_id
                )

        if not hit:
            log.debug("No user data found for user with ID %s.", target_u_id)

    async def cog_load(self) -> None:
        version = await self.config.version()
        if version == 0:  # first load so no need to update
            await self.config.version.set(1)

        # no other versions exist yet

        self.ready.set()

        log.trace("anniversary ready")

    @commands.command(hidden=True, aliases=["anivinfo"])
    async def aninfo(self, ctx: commands.Context):
        await ctx.send(await format_info(ctx, self.qualified_name, self.__version__))

    async def check_if_setup(self, guild: discord.Guild) -> bool:
        state = await self.config.guild(guild).setup_state()
        log.trace("setup state: %s", state)
        return state == 5
