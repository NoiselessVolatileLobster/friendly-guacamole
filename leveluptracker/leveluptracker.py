import discord
import logging
from datetime import datetime, timezone
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
            "levels": {}  # Format: {"level_int": timestamp_float}
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    async def red_delete_data_for_user(self, *, requester, user_id):
        """Handle data deletion request."""
        await self.config.user_from_id(user_id).clear()

    # --------------------------------------------------------------------------
    # Helper: Table Formatting (Matching your preference)
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
                if len(str(cell)) > col_widths[i]:
                    col_widths[i] = len(str(cell))

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
            # Attempt to use the method provided in the prompt
            return cog.get_level(member)
        except AttributeError:
            # Fallback if the specific method structure differs in current version
            # Most LevelUp versions store data in config
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
        """
        Run initialization logic when bot connects.
        This handles the 'first install' requirement.
        """
        await self.bot.wait_until_red_ready()
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).initialized():
                await self._initialize_guild(guild)

    async def _initialize_guild(self, guild: discord.Guild):
        """Snapshot current state for all members."""
        log.info(f"Initializing LevelUpTracker for guild: {guild.name}")
        
        async with self.config.guild(guild).members() as members_data:
            # We don't iterate member_data here because it's empty on first run
            # We iterate actual guild members
            for member in guild.members:
                if member.bot:
                    continue
                
                # Set Join Date
                join_ts = member.joined_at.timestamp() if member.joined_at else datetime.now(timezone.utc).timestamp()
                
                # Set Current Level
                current_level = await self._get_current_level(member)
                
                # Save to Config
                await self.config.member(member).join_timestamp.set(join_ts)
                if current_level > 0:
                    await self.config.member(member).levels.set_raw(str(current_level), value=datetime.now(timezone.utc).timestamp())
        
        await self.config.guild(guild).initialized.set(True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        # Record join timestamp
        ts = datetime.now(timezone.utc).timestamp()
        await self.config.member(member).join_timestamp.set(ts)

    @commands.Cog.listener()
    async def on_member_levelup(
        self,
        guild: discord.Guild,
        member: discord.Member,
        message: Optional[str],
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, discord.ForumChannel],
        nev_level: int, # Using the variable name provided in prompt
    ):
        """
        Listener to see when people level up.
        """
        if member.bot:
            return
            
        now_ts = datetime.now(timezone.utc).timestamp()
        
        # Store the timestamp for this specific level
        # Keys must be strings in JSON
        await self.config.member(member).levels.set_raw(str(nev_level), value=now_ts)

    # --------------------------------------------------------------------------
    # Commands
    # --------------------------------------------------------------------------
    @commands.group(name="leveluptrackerset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def leveluptrackerset(self, ctx):
        """Configuration commands for LevelUp Tracker."""
        pass

    @leveluptrackerset.command(name="view")
    async def leveluptrackerset_view(self, ctx):
        """
        View current settings and status.
        """
        is_init = await self.config.guild(ctx.guild).initialized()
        
        headers = ["Setting", "Value"]
        rows = [
            ["Initialized", str(is_init)],
            ["VertyCo LevelUp Loaded", str(self.bot.get_cog("LevelUp") is not None)]
        ]
        
        table = self._make_table(headers, rows)
        await ctx.send(box(table, lang="txt"))

    @leveluptrackerset.command(name="reindex")
    async def leveluptrackerset_reindex(self, ctx):
        """
        Manually trigger the initialization check.
        Useful if the cog was loaded before LevelUp was ready.
        """
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
        
        if not join_ts:
            return await ctx.send(f"I don't have join date tracking for {member.display_name} yet.")
        
        if not levels:
            return await ctx.send(f"{member.display_name} hasn't leveled up since I started tracking.")

        # Sort levels by int key
        sorted_levels = sorted([(int(k), v) for k, v in levels.items()], key=lambda x: x[0])
        
        headers = ["Level", "Date Reached", "Time Since Join", "Time Since Prev"]
        rows = []
        
        join_dt = datetime.fromtimestamp(join_ts, timezone.utc)
        prev_ts = join_ts
        
        for lvl, ts in sorted_levels:
            current_dt = datetime.fromtimestamp(ts, timezone.utc)
            
            # Time since join
            total_delta = current_dt - join_dt
            total_str = humanize_timedelta(timedelta=total_delta) or "0s"
            
            # Time since previous recorded event
            step_delta = current_dt - datetime.fromtimestamp(prev_ts, timezone.utc)
            step_str = humanize_timedelta(timedelta=step_delta) or "0s"
            
            date_str = current_dt.strftime("%Y-%m-%d")
            
            rows.append([f"Level {lvl}", date_str, total_str, step_str])
            prev_ts = ts # Update previous for next iteration

        table = self._make_table(headers, rows)
        await ctx.send(f"**Level History for {member.display_name}**\n" + box(table, lang="txt"))

    @commands.command()
    @commands.guild_only()
    async def levelaverages(self, ctx):
        """
        See the average time it takes members to reach specific levels.
        """
        # Dictionary to hold list of timedeltas for each level
        # { level_int: [seconds_float, seconds_float] }
        level_times = {}
        
        members_data = await self.config.guild(ctx.guild).members()
        
        for user_id, data in members_data.items():
            join_ts = data.get("join_timestamp")
            levels = data.get("levels", {})
            
            if not join_ts or not levels:
                continue
                
            for lvl_str, reached_ts in levels.items():
                lvl = int(lvl_str)
                time_to_reach = reached_ts - join_ts
                
                # Only count valid positive times
                if time_to_reach > 0:
                    if lvl not in level_times:
                        level_times[lvl] = []
                    level_times[lvl].append(time_to_reach)

        if not level_times:
            return await ctx.send("Not enough data to calculate averages yet.")

        headers = ["Level", "Avg Time from Join", "Sample Size"]
        rows = []

        for lvl in sorted(level_times.keys()):
            times = level_times[lvl]
            avg_seconds = sum(times) / len(times)
            
            # Create a timedelta for formatting
            from datetime import timedelta
            avg_delta = timedelta(seconds=avg_seconds)
            time_str = humanize_timedelta(timedelta=avg_delta) or "0s"
            
            rows.append([lvl, time_str, len(times)])

        table = self._make_table(headers, rows)
        await ctx.send("**Average Time to Reach Levels**\n" + box(table, lang="txt"))