import discord
import logging
import inspect
from datetime import datetime, timezone, timedelta
from typing import Optional, Union

from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_timedelta

log = logging.getLogger("red.leveluptracker")

class LevelUpTracker(commands.Cog):
    """
    Track how long it takes users to level up using VertyCo's LevelUp cog.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987123654, force_registration=True)

        # Default configuration
        default_guild = {
            "initialized": False
        }
        default_member = {
            "join_timestamp": None,
            "initial_level": None,  # None = unknown, 0 = new user, >0 = legacy user
            "levels": {}            # Format: {"level_int": timestamp_float}
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    async def red_delete_data_for_user(self, *, requester, user_id):
        """Handle data deletion request."""
        await self.config.user_from_id(user_id).clear()

    # --------------------------------------------------------------------------
    # Helper: Table Formatting
    # --------------------------------------------------------------------------
    def _make_table(self, headers: list, rows: list) -> str:
        """
        Creates a formatted table resembling the preferred style.
        """
        if not rows:
            return "No data available."

        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                cell_str = str(cell)
                if len(cell_str) > col_widths[i]:
                    col_widths[i] = len(cell_str)

        # Build separator
        separator = "+" + "+".join(["-" * (w + 2) for w in col_widths]) + "+"

        # Build Header
        header_line = "|"
        for i, h in enumerate(headers):
            header_line += f" {h:<{col_widths[i]}} |"

        # Build Rows
        body = []
        for row in rows:
            line = "|"
            for i, cell in enumerate(row):
                line += f" {str(cell):<{col_widths[i]}} |"
            body.append(line)

        return f"{separator}\n{header_line}\n{separator}\n" + "\n".join(body) + f"\n{separator}"

    # --------------------------------------------------------------------------
    # Helper: Integration
    # --------------------------------------------------------------------------
    async def _get_current_level(self, member: discord.Member) -> int:
        """Safely fetch level from VertyCo's LevelUp cog."""
        cog = self.bot.get_cog("LevelUp")
        if not cog:
            return 0
        try:
            # Helper to handle both async and sync returns from 3rd party cogs
            val = cog.get_level(member)
            if inspect.isawaitable(val):
                return await val
            return val
        except AttributeError:
            try:
                return await cog.config.member(member).level()
            except Exception:
                return 0
        except Exception as e:
            log.error(f"Failed to fetch level for {member}: {e}")
            return 0

    # --------------------------------------------------------------------------
    # Events & Initialization
    # --------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_connect(self):
        """Run initialization logic when bot connects."""
        await self.bot.wait_until_red_ready()
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).initialized():
                await self._initialize_guild(guild)

    async def _initialize_guild(self, guild: discord.Guild):
        """Snapshot current state for all members."""
        log.info(f"Initializing LevelUpTracker for guild: {guild.name}")
        
        for member in guild.members:
            if member.bot:
                continue
            
            # Set Join Date
            join_ts = member.joined_at.timestamp() if member.joined_at else datetime.now(timezone.utc).timestamp()
            
            # Set Current Level (Snapshot)
            current_level = await self._get_current_level(member)
            now_ts = datetime.now(timezone.utc).timestamp()
            
            member_conf = self.config.member(member)
            await member_conf.join_timestamp.set(join_ts)
            
            # Record their starting point
            await member_conf.initial_level.set(current_level)
            
            # If they are already leveled, snapshot that level as 'reached now'
            if current_level > 0:
                 await member_conf.levels.set_raw(str(current_level), value=now_ts)
        
        await self.config.guild(guild).initialized.set(True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        ts = datetime.now(timezone.utc).timestamp()
        
        member_conf = self.config.member(member)
        await member_conf.join_timestamp.set(ts)
        # New members always start at 0
        await member_conf.initial_level.set(0)

    @commands.Cog.listener()
    async def on_member_levelup(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message: Optional[str],
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, discord.ForumChannel],
        new_level: int, 
    ):
        if member.bot:
            return
            
        now_ts = datetime.now(timezone.utc).timestamp()
        
        # Ensure we have an initial level set if this is the first interaction
        if await self.config.member(member).initial_level() is None:
            # If we missed the join/init, assume previous level was the start
            await self.config.member(member).initial_level.set(max(0, new_level - 1))

        await self.config.member(member).levels.set_raw(str(new_level), value=now_ts)

    # --------------------------------------------------------------------------
    # Admin Commands
    # --------------------------------------------------------------------------
    @commands.group(name="leveluptrackerset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def leveluptrackerset(self, ctx):
        """Configuration commands for LevelUp Tracker."""
        pass

    @leveluptrackerset.command(name="view")
    async def leveluptrackerset_view(self, ctx):
        """View current settings and status."""
        is_init = await self.config.guild(ctx.guild).initialized()
        vertyco_loaded = self.bot.get_cog("LevelUp") is not None
        
        headers = ["Setting", "Value"]
        rows = [
            ["Initialized", str(is_init)],
            ["VertyCo LevelUp Loaded", str(vertyco_loaded)]
        ]
        
        table = self._make_table(headers, rows)
        await ctx.send(box(table, lang="txt"))

    @leveluptrackerset.command(name="reindex")
    async def leveluptrackerset_reindex(self, ctx):
        """Manually trigger the initialization check."""
        await ctx.send("Starting manual re-index of members...")
        await self._initialize_guild(ctx.guild)
        await ctx.send("Re-index complete.")

    # --------------------------------------------------------------------------
    # Public Stats Commands
    # --------------------------------------------------------------------------
    @commands.command()
    @commands.guild_only()
    async def levelhistory(self, ctx, member: discord.Member = None):
        """
        See how long it took a member to reach their levels.
        """
        member = member or ctx.author
        data = await self.config.member(member).all()
        
        join_ts = data.get("join_timestamp")
        levels = data.get("levels", {})
        initial_level = data.get("initial_level")
        
        if initial_level is None:
             initial_level = 0
        
        if not join_ts:
            if member.joined_at:
                join_ts = member.joined_at.timestamp()
            else:
                return await ctx.send(f"I don't have tracking data for {member.display_name} yet.")

        # Header info
        info_text = f"**Level History for {member.display_name}**\n"
        if initial_level > 0:
            info_text += f"User started tracking at **Level {initial_level}** (Legacy User).\n"
        else:
            info_text += "User tracked from join (New User).\n"

        if not levels and initial_level == 0:
            return await ctx.send(f"{member.display_name} hasn't leveled up since I started tracking.")

        sorted_levels = sorted([(int(k), v) for k, v in levels.items()], key=lambda x: x[0])
        
        headers = ["Level", "Date Reached", "Time from Start", "Time from Prev"]
        rows = []
        
        join_dt = datetime.fromtimestamp(join_ts, timezone.utc)
        prev_ts = join_ts
        if initial_level > 0:
            if str(initial_level) in levels:
                prev_ts = levels[str(initial_level)]

        for lvl, ts in sorted_levels:
            if lvl < initial_level:
                continue
                
            current_dt = datetime.fromtimestamp(ts, timezone.utc)
            date_str = current_dt.strftime("%Y-%m-%d")

            # 1. Time from Start
            if lvl == initial_level:
                rows.append([f"Lvl {lvl} (Start)", date_str, "-", "-"])
                prev_ts = ts
                continue

            if initial_level == 0:
                total_delta = current_dt - join_dt
                total_str = humanize_timedelta(timedelta=total_delta) or "0s"
            else:
                start_ts = levels.get(str(initial_level), join_ts)
                total_delta = current_dt - datetime.fromtimestamp(start_ts, timezone.utc)
                total_str = (humanize_timedelta(timedelta=total_delta) or "0s") + "*"

            # 2. Time from Previous
            step_delta = current_dt - datetime.fromtimestamp(prev_ts, timezone.utc)
            step_str = humanize_timedelta(timedelta=step_delta) or "0s"

            rows.append([f"Level {lvl}", date_str, total_str, step_str])
            prev_ts = ts 

        table = self._make_table(headers, rows)
        if initial_level > 0:
            info_text += "*Time from Start counts from when the bot first saw this user at their initial level.\n"
            
        await ctx.send(info_text + box(table, lang="txt"))

    @commands.command()
    @commands.guild_only()
    async def levelaverages(self, ctx):
        """
        Average time for NEW users to reach levels (from Join).
        Excludes users who were already leveled when tracking started.
        """
        level_times = {} 
        
        all_members = await self.config.all_members(ctx.guild)
        
        skipped_legacy = 0
        included_users = 0

        for user_id, data in all_members.items():
            join_ts = data.get("join_timestamp")
            levels = data.get("levels", {})
            initial_level = data.get("initial_level")

            # STRICT FILTER: Only include users who started at Level 0
            if initial_level is not None and initial_level > 0:
                skipped_legacy += 1
                continue
            
            if not join_ts or not levels:
                continue
            
            included_users += 1
                
            for lvl_str, reached_ts in levels.items():
                lvl = int(lvl_str)
                time_to_reach = reached_ts - join_ts
                
                if time_to_reach > 0:
                    if lvl not in level_times:
                        level_times[lvl] = []
                    level_times[lvl].append(time_to_reach)

        if not level_times:
            msg = "Not enough data from **New Users** to calculate averages yet."
            if skipped_legacy > 0:
                msg += f"\n(Skipped {skipped_legacy} legacy users who started > Level 0)."
            return await ctx.send(msg)

        headers = ["Level", "Avg Time (From Join)", "Sample Size"]
        rows = []

        for lvl in sorted(level_times.keys()):
            times = level_times[lvl]
            avg_seconds = sum(times) / len(times)
            
            avg_delta = timedelta(seconds=avg_seconds)
            time_str = humanize_timedelta(timedelta=avg_delta) or "0s"
            
            rows.append([lvl, time_str, len(times)])

        table = self._make_table(headers, rows)
        await ctx.send(f"**Average Leveling Speed (New Users Only)**\nBased on {included_users} new members.\n" + box(table, lang="txt"))