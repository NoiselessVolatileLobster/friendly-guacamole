import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list, box
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
import random
import re
import logging
from typing import Union, List, Tuple, Dict, Optional

# Pydantic is used for structured configuration in modern Red cogs
try:
    from pydantic import BaseModel, Field
except ImportError:
    # Fallback if pydantic is not available
    class BaseModel:
        def model_dump(self):
            return self.__dict__
        def __init__(self, **data):
            for key, value in data.items():
                setattr(self, key, value)
                
    def Field(default, **kwargs):
        return default

log = logging.getLogger("red.ouijapoke")

# --- Configuration Schema (Settings) ---

class OuijaSettings(BaseModel):
    """Schema for guild configuration settings."""
    poke_days: int = Field(default=30, ge=1, description="Days a member must be inactive to be eligible for a poke.")
    summon_days: int = Field(default=60, ge=1, description="Days a member must be inactive to be eligible for a summon.")
    
    # WarnSystem Integration (Inactivity)
    warn_level_1_days: int = Field(default=0, ge=0, description="Days inactive to trigger Level 1 warning (0 to disable).")
    warn_level_3_days: int = Field(default=0, ge=0, description="Days inactive to trigger Level 3 warning (0 to disable).")

    # No Intro Settings
    nointro_days: int = Field(default=0, ge=0, description="Days since join to check for No Intro role.")
    nointro_role_id: Optional[int] = Field(default=None, description="Role ID to check for.")
    nointro_channel_id: Optional[int] = Field(default=None, description="Channel to send the No Intro ping.")
    nointro_message: str = Field(default="Hey {mention}, you've been here a while! Please head to the intro channel.", description="Message to send.")

    # Level 0 (Still At Zero) Settings
    level0_warn_days: int = Field(default=0, ge=0, description="Days since join to warn if still Level 0.")
    level0_channel_id: Optional[int] = Field(default=None, description="Channel to send the Level 0 warning.")
    level0_message: str = Field(default="{mention}, you are still Level 0! Participate to avoid removal.", description="Message to send.")
    
    level0_kick_days: int = Field(default=0, ge=0, description="Days since join to Kick (WarnSystem Lvl 3) if still Level 0.")
    level0_kick_reason: str = Field(default="Remained at Level 0 for too long.", description="Reason for the kick warning.")

    # Activity Threshold Settings
    required_messages: int = Field(default=1, ge=1, description="Number of messages required to count as active.")
    required_window_hours: float = Field(default=0, ge=0, description="Time window (in hours) for the message count.")
    min_message_length: int = Field(default=0, ge=0, description="Minimum characters in a message to count.")
    
    poke_message: str = Field(
        default="Hey {user_mention}, the Ouija Board feels your presence. Come say hello!",
        description="The message used when poking. Use {user_mention} for the user."
    )
    
    summon_message: str = Field(
        default="**{user_mention}**! The spirits demand your return! Do not resist the summoning ritual!",
        description="The message used when summoning. Use {user_mention} for the user."
    )
    
    poke_gifs: list[str] = Field(default=[], description="List of URLs for 'poke' GIFs.")
    summon_gifs: list[str] = Field(default=[], description="List of URLs for 'summon' GIFs.")
    
    # Auto Poke Settings
    auto_channel_id: Optional[int] = Field(default=None, description="Channel ID for automatic pokes/summons.")

# --- Cog Class ---

class OuijaPoke(commands.Cog):
    """Tracks user activity and allows 'poking' or 'summoning' inactive members with a spooky twist."""

    def __init__(self, bot):
        self.bot = bot
        # Config setup:
        self.config = Config.get_conf(self, identifier=148000552390, force_registration=True)
        self.config.register_guild(
            last_seen={}, # {user_id: "ISO_DATETIME_STRING"}
            last_poked={}, # {user_id: "ISO_DATETIME_STRING"}
            last_summoned={}, # {user_id: "ISO_DATETIME_STRING"}
            warned_users={}, # {user_id: {"level1": ts, "level3": ts, "nointro": ts, "level0_warn": ts, "level0_kick": ts}}
            excluded_roles=[], # [role_id, ...] -> "Hibernating Roles"
            excluded_channels=[], # [channel_id, ...]
            ouija_settings=OuijaSettings().model_dump(),
            next_auto_event=None, # ISO_DATETIME_STRING for the next scheduled auto run
        )
        # In-memory tracker for voice channel connections
        self.voice_connect_times = {} # {member_id: datetime_object}
        
        # In-memory cache for message bursts: {user_id: [timestamp1, timestamp2, ...]}
        self.recent_activity_cache: Dict[int, List[datetime]] = {}
        
        # Start the loop
        self.auto_poke_loop.start()

    def cog_unload(self):
        self.auto_poke_loop.cancel()

    # --- Utility Methods ---

    async def _get_settings(self, guild: discord.Guild) -> OuijaSettings:
        """Retrieves and parses the guild settings."""
        settings_data = await self.config.guild(guild).ouija_settings()
        # Handle backward compatibility/missing fields by letting pydantic fill defaults
        return OuijaSettings(**settings_data)

    async def _set_settings(self, guild: discord.Guild, settings: OuijaSettings):
        """Saves the updated guild settings."""
        await self.config.guild(guild).ouija_settings.set(settings.model_dump())
    
    async def _update_last_seen(self, guild: discord.Guild, user_id: int):
        """Updates the last_seen time and clears Inactivity warning flags for a user."""
        user_id_str = str(user_id)
        current_time_utc = datetime.now(timezone.utc).isoformat()
        
        async with self.config.guild(guild).all() as data:
            data["last_seen"][user_id_str] = current_time_utc
            
            # If they were warned for inactivity, clear those specific flags now that they are active.
            # NOTE: We do NOT clear "nointro" or "level0" flags here, as those are state-based, not just activity-based.
            if user_id_str in data["warned_users"]:
                user_warnings = data["warned_users"][user_id_str]
                if "level1" in user_warnings: del user_warnings["level1"]
                if "level3" in user_warnings: del user_warnings["level3"]
                data["warned_users"][user_id_str] = user_warnings
        
    def _is_valid_gif_url(self, url: str) -> bool:
        """Simple check if the URL looks like a GIF link or page."""
        return re.match(r'^https?://[^\s/$.?#].[^\s]*\.(gif|webp|mp4|mov)(\?.*)?$', url, re.IGNORECASE) is not None or "tenor.com" in url or "giphy.com" in url

    def _get_inactivity_cutoff(self, days: int) -> datetime:
        """Calculates the ISO datetime cutoff point for inactivity."""
        return datetime.now(timezone.utc) - timedelta(days=days)

    def _is_excluded(self, member: discord.Member, excluded_roles: List[int]) -> bool:
        """Checks if the member has any role that is in the hibernating (excluded) list."""
        if not excluded_roles:
            return False
        
        member_role_ids = {role.id for role in member.roles}
        excluded_role_ids = set(excluded_roles)
        
        return bool(member_role_ids.intersection(excluded_role_ids))
    
    def _get_excluded_role_names(self, member: discord.Member, excluded_roles: List[int]) -> List[str]:
        """Returns the names of the roles that are causing the hibernation."""
        excluded_names = []
        excluded_role_ids = set(excluded_roles)
        for role in member.roles:
            if role.id in excluded_role_ids:
                excluded_names.append(role.name)
        return excluded_names

    async def _get_eligible_members(self, guild: discord.Guild, days_inactive: int, last_action_key: str) -> Tuple[List[discord.Member], List[discord.Member]]:
        """
        Gets a list of members eligible for action, prioritized by whether they have been acted upon.
        Returns: (priority_1_members, priority_2_members)
        """
        cutoff_dt = self._get_inactivity_cutoff(days_inactive)
        
        data = await self.config.guild(guild).all()
        last_seen_data = data["last_seen"]
        last_action_data = data[last_action_key]
        excluded_roles = data["excluded_roles"]
        
        priority_1: List[discord.Member] = []
        priority_2: List[Tuple[discord.Member, datetime]] = []
        
        for user_id_str, last_seen_dt_str in last_seen_data.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            if member is None or member.bot or self._is_excluded(member, excluded_roles):
                continue

            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if last_seen_dt < cutoff_dt:
                last_action_dt_str = last_action_data.get(user_id_str)
                
                if last_action_dt_str is None:
                    priority_1.append(member)
                else:
                    try:
                        last_action_dt = datetime.fromisoformat(last_action_dt_str).replace(tzinfo=timezone.utc)
                        priority_2.append((member, last_action_dt))
                    except ValueError:
                        priority_1.append(member)

        priority_2_members = [
            member for member, dt in sorted(priority_2, key=lambda x: x[1])
        ]
        
        return priority_1, priority_2_members
    
    async def _filter_spam_protected(self, guild: discord.Guild, members: List[discord.Member]) -> List[discord.Member]:
        """
        Filters out members who have been poked OR summoned in the last 14 days.
        """
        data = await self.config.guild(guild).all()
        last_poked = data.get("last_poked", {})
        last_summoned = data.get("last_summoned", {})
        
        safe_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        filtered_members = []
        
        for member in members:
            uid = str(member.id)
            poked_ts = last_poked.get(uid)
            summoned_ts = last_summoned.get(uid)
            
            recent_activity = False
            
            if poked_ts:
                try:
                    dt = datetime.fromisoformat(poked_ts).replace(tzinfo=timezone.utc)
                    if dt > safe_cutoff: recent_activity = True
                except ValueError: pass
            
            if not recent_activity and summoned_ts:
                try:
                    dt = datetime.fromisoformat(summoned_ts).replace(tzinfo=timezone.utc)
                    if dt > safe_cutoff: recent_activity = True
                except ValueError: pass
                
            if not recent_activity:
                filtered_members.append(member)
                
        return filtered_members
    
    async def _set_last_action_time(self, guild: discord.Guild, user_id: int, key: str):
        """Updates the last_poked or last_summoned time for a user."""
        user_id_str = str(user_id)
        current_time_utc = datetime.now(timezone.utc).isoformat()
        
        data = await self.config.guild(guild).get_attr(key)()
        data[user_id_str] = current_time_utc
        await self.config.guild(guild).get_attr(key).set(data)

    def _format_date_diff(self, dt_str: Union[str, None]) -> str:
        """Helper function for formatting ISO dates into 'X days ago' or 'Never'."""
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
                diff = datetime.now(timezone.utc) - dt
                return f"{diff.days} days ago"
            except ValueError:
                return "Invalid Date"
        return "Never"
        
    async def _schedule_next_auto_event(self, guild: discord.Guild):
        """Schedules the next auto event for ~24 hours from now with randomness."""
        # 24 hours +/- up to 2 hours of variance for "random time" feel
        base_time = datetime.now(timezone.utc) + timedelta(hours=24)
        variance = random.randint(-7200, 7200) # +/- 2 hours in seconds
        next_run = base_time + timedelta(seconds=variance)
        
        await self.config.guild(guild).next_auto_event.set(next_run.isoformat())
        return next_run

    # --- Automated Task Loop ---

    @tasks.loop(minutes=5)
    async def auto_poke_loop(self):
        """Background loop to handle automatic pokes, summons, and automated policing."""
        for guild in self.bot.guilds:
            try:
                # 1. Check if configured
                settings_data = await self.config.guild(guild).ouija_settings()
                settings = OuijaSettings(**settings_data)
                
                # Run Automated Checks (Warnings, No Intro, Level 0)
                await self._process_automated_checks(guild, settings)

                # 2. Check Auto Poke Schedule
                next_run_str = await self.config.guild(guild).next_auto_event()
                now = datetime.now(timezone.utc)
                
                should_run = False
                
                if not next_run_str:
                    # First time init: Schedule for random time in next 24h
                    await self._schedule_next_auto_event(guild)
                    continue
                else:
                    try:
                        next_run_dt = datetime.fromisoformat(next_run_str).replace(tzinfo=timezone.utc)
                        if now >= next_run_dt:
                            should_run = True
                    except ValueError:
                        await self._schedule_next_auto_event(guild)
                        continue
                
                if should_run:
                    # Execute logic
                    if settings.auto_channel_id:
                        channel = guild.get_channel(settings.auto_channel_id)
                        if channel and channel.permissions_for(guild.me).send_messages:
                            await self._run_daily_lottery(guild, channel, settings)
                    
                    # Schedule next run regardless of success to prevent loop spam
                    await self._schedule_next_auto_event(guild)
            except Exception as e:
                log.error(f"Error in auto_poke_loop for guild {guild.id}: {e}", exc_info=True)

    async def _process_automated_checks(self, guild: discord.Guild, settings: OuijaSettings):
        """Checks for inactive users, No Intro violations, and Level 0 lurkers."""
        
        warn_cog = self.bot.get_cog("WarnSystem")
        levelup_cog = self.bot.get_cog("LevelUp")
        
        data = await self.config.guild(guild).all()
        last_seen_data = data["last_seen"]
        warned_users = data["warned_users"]
        excluded_roles = data["excluded_roles"]
        
        now = datetime.now(timezone.utc)
        
        # Pre-fetch role object for No Intro
        nointro_role = guild.get_role(settings.nointro_role_id) if settings.nointro_role_id else None
        nointro_channel = guild.get_channel(settings.nointro_channel_id) if settings.nointro_channel_id else None
        level0_channel = guild.get_channel(settings.level0_channel_id) if settings.level0_channel_id else None

        # Iterate over MEMBERS in the guild to cover "No Intro" and "Level 0" logic (which uses join date)
        # We also check "last_seen" for inactivity logic.
        
        for member in guild.members:
            if member.bot or self._is_excluded(member, excluded_roles):
                continue
            
            user_id_str = str(member.id)
            user_warnings = warned_users.get(user_id_str, {})
            has_changes = False

            # --- A. NO INTRO CHECK ---
            if settings.nointro_days > 0 and nointro_role and nointro_channel:
                if nointro_role in member.roles:
                    days_joined = (now - member.joined_at.replace(tzinfo=timezone.utc)).days
                    if days_joined >= settings.nointro_days:
                        if "nointro" not in user_warnings:
                            # Trigger No Intro Message
                            try:
                                msg = settings.nointro_message.replace("{mention}", member.mention)
                                await nointro_channel.send(msg)
                                user_warnings["nointro"] = now.isoformat()
                                has_changes = True
                            except discord.Forbidden:
                                pass

            # --- B. LEVEL 0 CHECKS ---
            if levelup_cog:
                # Check levels
                level = levelup_cog.get_level(member)
                if level == 0:
                    days_joined = (now - member.joined_at.replace(tzinfo=timezone.utc)).days
                    
                    # 1. Message Warning
                    if settings.level0_warn_days > 0 and level0_channel and days_joined >= settings.level0_warn_days:
                        if "level0_warn" not in user_warnings:
                            try:
                                msg = settings.level0_message.replace("{mention}", member.mention)
                                await level0_channel.send(msg)
                                user_warnings["level0_warn"] = now.isoformat()
                                has_changes = True
                            except discord.Forbidden:
                                pass
                    
                    # 2. Kick Warning (WarnSystem Level 3)
                    if settings.level0_kick_days > 0 and warn_cog and days_joined >= settings.level0_kick_days:
                        if "level0_kick" not in user_warnings:
                            try:
                                await warn_cog.api.warn(
                                    member=member,
                                    author=guild.me,
                                    reason=settings.level0_kick_reason,
                                    level=3
                                )
                                user_warnings["level0_kick"] = now.isoformat()
                                has_changes = True
                                log.info(f"OuijaPoke: Level 0 Kick warning for {member} in {guild.name}")
                            except Exception as e:
                                log.error(f"Failed Level 0 kick for {member}: {e}")

            # --- C. INACTIVITY CHECKS (Uses Last Seen) ---
            # Only run if inactivity warnings are enabled
            if settings.warn_level_1_days > 0 or settings.warn_level_3_days > 0:
                last_seen_dt_str = last_seen_data.get(user_id_str)
                if last_seen_dt_str:
                    try:
                        last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
                        days_inactive = (now - last_seen_dt).days
                        
                        # Level 3 (Kick)
                        if settings.warn_level_3_days > 0 and warn_cog and days_inactive >= settings.warn_level_3_days:
                            if "level3" not in user_warnings:
                                try:
                                    reason = f"Inactive for over {days_inactive} days (Threshold: {settings.warn_level_3_days})."
                                    await warn_cog.api.warn(member=member, author=guild.me, reason=reason, level=3)
                                    user_warnings["level3"] = now.isoformat()
                                    has_changes = True
                                except Exception as e:
                                    log.error(f"Failed L3 Inactivity Warn for {member}: {e}")

                        # Level 1 (Warn)
                        if settings.warn_level_1_days > 0 and warn_cog and days_inactive >= settings.warn_level_1_days:
                            if "level1" not in user_warnings:
                                try:
                                    reason = f"Inactive for over {days_inactive} days (Threshold: {settings.warn_level_1_days})."
                                    await warn_cog.api.warn(member=member, author=guild.me, reason=reason, level=1)
                                    user_warnings["level1"] = now.isoformat()
                                    has_changes = True
                                except Exception as e:
                                    log.error(f"Failed L1 Inactivity Warn for {member}: {e}")

                    except ValueError:
                        pass
            
            if has_changes:
                warned_users[user_id_str] = user_warnings
        
        # Bulk save warned_users once per guild loop to reduce IO
        await self.config.guild(guild).warned_users.set(warned_users)

    async def _run_daily_lottery(self, guild: discord.Guild, channel: discord.TextChannel, settings: OuijaSettings) -> str:
        """Runs the 10/10/80 probability logic. Returns a status string."""
        roll = random.random() # 0.0 to 1.0
        
        # 10% Chance Summon (0.0 <= roll < 0.1)
        if roll < 0.10:
            p1, p2 = await self._get_eligible_members(guild, settings.summon_days, "last_summoned")
            candidates = p1 + p2
            # Spam filter: Remove anyone poked/summoned in last 14 days
            candidates = await self._filter_spam_protected(guild, candidates)
            
            if candidates:
                target = random.choice(candidates)
                await self._set_last_action_time(guild, target.id, "last_summoned")
                await self._send_activity_message_channel(channel, target, settings.summon_message, settings.summon_gifs)
                log.info(f"OuijaPoke: Automatically summoned {target} in {guild.name}")
                return f"🎲 Roll: {roll:.3f} (< 0.10) -> **SUMMONED** {target.display_name} in {channel.mention}."
            else:
                return f"🎲 Roll: {roll:.3f} (< 0.10) -> Summon triggered, but **NO ELIGIBLE CANDIDATES** found."

        # 10% Chance Poke (0.1 <= roll < 0.2)
        elif roll < 0.20:
            p1, p2 = await self._get_eligible_members(guild, settings.poke_days, "last_poked")
            candidates = p1 + p2
            # Spam filter: Remove anyone poked/summoned in last 14 days
            candidates = await self._filter_spam_protected(guild, candidates)
            
            if candidates:
                target = random.choice(candidates)
                await self._set_last_action_time(guild, target.id, "last_poked")
                await self._send_activity_message_channel(channel, target, settings.poke_message, settings.poke_gifs)
                log.info(f"OuijaPoke: Automatically poked {target} in {guild.name}")
                return f"🎲 Roll: {roll:.3f} (< 0.20) -> **POKED** {target.display_name} in {channel.mention}."
            else:
                return f"🎲 Roll: {roll:.3f} (< 0.20) -> Poke triggered, but **NO ELIGIBLE CANDIDATES** found."

        # 80% Chance Nothing (0.2 <= roll <= 1.0)
        else:
            return f"🎲 Roll: {roll:.3f} (>= 0.20) -> **The spirits are quiet.** (No action taken)."

    async def _send_activity_message_channel(self, channel: discord.TextChannel, member: discord.Member, message_text: str, gif_list: list[str]):
        """Sends the message text and the GIF URL as two separate messages to a specific channel."""
        final_message = message_text.replace("{user_mention}", member.mention)
        try:
            await channel.send(content=final_message)
            if gif_list:
                gif_url = random.choice(gif_list)
                await channel.send(content=gif_url)
        except discord.Forbidden:
            log.warning(f"OuijaPoke: Missing permissions to send message in {channel.name}")

    @auto_poke_loop.before_loop
    async def before_auto_poke_loop(self):
        await self.bot.wait_until_ready()

    # --- PUBLIC API FOR EXTERNAL COGS ---
    
    async def get_member_activity_state(self, member: discord.Member) -> Dict[str, Union[str, bool, int, None]]:
        """
        Public API method to retrieve the status of a specific member.
        """
        if member.bot:
            return {"status": "unknown", "is_hibernating": True, "days_inactive": None, "last_seen": None}

        data = await self.config.guild(member.guild).all()
        settings = OuijaSettings(**data["ouija_settings"])
        
        # 1. Check Hibernation (Exclusion)
        is_hibernating = self._is_excluded(member, data["excluded_roles"])
        
        # 2. Get Timing Data
        last_seen_str = data["last_seen"].get(str(member.id))
        
        days_inactive = None
        last_seen_dt = None
        status = "unknown"

        if last_seen_str:
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
                days_inactive = (datetime.now(timezone.utc) - last_seen_dt).days
                
                # 3. Determine Status
                poke_cutoff = self._get_inactivity_cutoff(settings.poke_days)
                summon_cutoff = self._get_inactivity_cutoff(settings.summon_days)
                
                if last_seen_dt >= poke_cutoff:
                    status = "active"
                elif last_seen_dt >= summon_cutoff:
                    status = "poke_eligible"
                else:
                    status = "summon_eligible"
                    
            except ValueError:
                status = "unknown"
        
        return {
            "status": status,
            "is_hibernating": is_hibernating,
            "days_inactive": days_inactive,
            "last_seen": last_seen_dt
        }

    # --- End Public API ---

    async def _get_all_eligible_member_data(self, ctx: commands.Context) -> List[dict]:
        """Retrieves comprehensive data for all members who meet EITHER the poke or summon inactivity criteria."""
        guild = ctx.guild
        data = await self.config.guild(guild).all()
        
        settings = OuijaSettings(**data["ouija_settings"])
        last_seen_data = data["last_seen"]
        last_poked_data = data["last_poked"]
        last_summoned_data = data["last_summoned"]
        excluded_roles = data["excluded_roles"]
        
        poke_cutoff = self._get_inactivity_cutoff(settings.poke_days)
        summon_cutoff = self._get_inactivity_cutoff(settings.summon_days)
        
        eligible_list = []
        
        for user_id_str, last_seen_dt_str in last_seen_data.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            if member is None or member.bot or self._is_excluded(member, excluded_roles):
                continue
            
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            is_poke_eligible = last_seen_dt < poke_cutoff
            is_summon_eligible = last_seen_dt < summon_cutoff
            
            if is_poke_eligible or is_summon_eligible:
                last_poked_str = last_poked_data.get(user_id_str)
                last_summoned_str = last_summoned_data.get(user_id_str)
                
                last_seen_diff = (datetime.now(timezone.utc) - last_seen_dt).days
                
                eligible_list.append({
                    "member": member,
                    "last_seen_days": last_seen_diff,
                    "last_poked": self._format_date_diff(last_poked_str),
                    "last_summoned": self._format_date_diff(last_summoned_str),
                    "eligible_for": ("Poke" if is_poke_eligible else "") + (" & Summon" if is_poke_eligible and is_summon_eligible else "Summon" if is_summon_eligible else "")
                })

        eligible_list.sort(key=lambda x: x['last_seen_days'], reverse=True)
        return eligible_list
    
    async def _get_excluded_eligible_members(self, ctx: commands.Context) -> List[dict]:
        """Retrieves data for members who are eligible by activity but excluded by role (Hibernating)."""
        guild = ctx.guild
        data = await self.config.guild(guild).all()
        
        settings = OuijaSettings(**data["ouija_settings"])
        last_seen_data = data["last_seen"]
        excluded_roles = data["excluded_roles"]
        
        poke_cutoff = self._get_inactivity_cutoff(settings.poke_days)
        summon_cutoff = self._get_inactivity_cutoff(settings.summon_days)
        
        excluded_eligible_list = []
        
        for user_id_str, last_seen_dt_str in last_seen_data.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            if member is None or member.bot:
                continue
            
            if not self._is_excluded(member, excluded_roles):
                continue
            
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            is_poke_eligible = last_seen_dt < poke_cutoff
            is_summon_eligible = last_seen_dt < summon_cutoff
            
            if is_poke_eligible or is_summon_eligible:
                last_seen_diff = (datetime.now(timezone.utc) - last_seen_dt).days
                excluded_names = self._get_excluded_role_names(member, excluded_roles)
                
                excluded_eligible_list.append({
                    "member": member,
                    "last_seen_days": last_seen_diff,
                    "eligible_for": ("Poke" if is_poke_eligible else "") + (" & Summon" if is_poke_eligible and is_summon_eligible else "Summon" if is_summon_eligible else ""),
                    "excluded_by": humanize_list([f"@{name}" for name in excluded_names])
                })

        excluded_eligible_list.sort(key=lambda x: x['last_seen_days'], reverse=True)
        return excluded_eligible_list

    async def _send_activity_message(self, ctx: commands.Context, member: discord.Member, message_text: str, gif_list: list[str]):
        """Sends the message text and the GIF URL as two separate messages."""
        final_message = message_text.replace("{user_mention}", member.mention)
        await ctx.send(content=final_message)
        if gif_list:
            gif_url = random.choice(gif_list)
            await ctx.send(content=gif_url)

    # --- Listeners (Event Handlers) ---
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Updates activity based on configured thresholds (messages/time/length/channel)."""
        if message.guild is None or message.author.bot or message.webhook_id:
            return
        
        # Ignore valid commands
        ctx = await self.bot.get_context(message)
        if ctx.command:
            return
        
        guild = message.guild
        user_id = message.author.id

        # 1. Check Excluded Channels
        excluded_channels = await self.config.guild(guild).excluded_channels()
        if message.channel.id in excluded_channels:
            return

        # 2. Fetch Settings
        settings_data = await self.config.guild(guild).ouija_settings()
        settings = OuijaSettings(**settings_data)

        # 3. Check Message Length
        if settings.min_message_length > 0 and len(message.content) < settings.min_message_length:
            return

        # 4. Check Burst Activity (X messages in Y hours)
        should_update = False
        
        if settings.required_messages <= 1 or settings.required_window_hours <= 0:
            should_update = True
        else:
            now = datetime.now(timezone.utc)
            if user_id not in self.recent_activity_cache:
                self.recent_activity_cache[user_id] = []
            
            self.recent_activity_cache[user_id].append(now)
            window_delta = timedelta(hours=settings.required_window_hours)
            min_time = now - window_delta
            
            self.recent_activity_cache[user_id] = [
                t for t in self.recent_activity_cache[user_id] if t > min_time
            ]
            
            if len(self.recent_activity_cache[user_id]) >= settings.required_messages:
                should_update = True

        # 5. Update if criteria met
        if should_update:
            await self._update_last_seen(guild, user_id)
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Sets the last_seen time for a new member to now."""
        if member.bot:
            return
        
        data = await self.config.guild(member.guild).last_seen()
        if str(member.id) not in data:
            await self._update_last_seen(member.guild, member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Tracks voice channel connection duration."""
        if member.bot:
            return
        
        excluded_channels = await self.config.guild(member.guild).excluded_channels()
        member_id = member.id
        
        if after.channel is not None and before.channel != after.channel:
            if after.channel.id in excluded_channels:
                return
            
            if not after.self_mute and not after.self_deaf and not after.mute and not after.deaf:
                self.voice_connect_times[member_id] = datetime.now(timezone.utc)
        
        if before.channel is not None and after.channel is None:
            if member_id in self.voice_connect_times:
                join_time = self.voice_connect_times.pop(member_id)
                duration = datetime.now(timezone.utc) - join_time
                
                if duration >= timedelta(minutes=5):
                    await self._update_last_seen(member.guild, member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Resets inactivity timer if a member loses a hibernating (excluded) role.
        """
        if before.bot: 
            return
        
        # Only proceed if roles were removed
        if len(before.roles) <= len(after.roles):
            return

        excluded_role_ids = await self.config.guild(after.guild).excluded_roles()
        if not excluded_role_ids:
            return
        
        # Calculate removed roles
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        removed_ids = before_ids - after_ids
        
        # Check intersection
        if not removed_ids.isdisjoint(set(excluded_role_ids)):
            # A hibernating role was removed.
            # We reset their timer to ensure they start at 0.
            await self._update_last_seen(after.guild, after.id)
            log.info(f"OuijaPoke: Reset inactivity for {after} (Hibernating role removed).")

    # --- User Commands ---

    @commands.group(invoke_without_command=True, aliases=["ouija"])
    async def ouijapoke(self, ctx: commands.Context):
        """Commands for OuijaPoke."""
        await ctx.send_help(ctx.command)
    
    @commands.command(name="poke")
    async def poke(self, ctx: commands.Context):
        """Pokes a random inactive member."""
        try:
            await self.ouijapoke_random(ctx)
        finally:
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete()
            else:
                await ctx.send("I need the `Manage Messages` permission to delete your command message.", delete_after=10)

    @commands.command(name="summon")
    async def summon(self, ctx: commands.Context):
        """Summons a random inactive member."""
        try:
            await self.ouijasummon_random(ctx)
        finally:
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete()
            else:
                await ctx.send("I need the `Manage Messages` permission to delete your command message.", delete_after=10)

    @ouijapoke.command(name="check")
    async def ouijapoke_check(self, ctx: commands.Context):
        """Shows your own inactivity status."""
        user_id = str(ctx.author.id)
        data = await self.config.guild(ctx.guild).last_seen()
        last_seen_dt_str = data.get(user_id)

        try:
            if not last_seen_dt_str:
                return await ctx.send("I haven't recorded any activity for you yet! Say something now!")

            last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            days = (now_dt - last_seen_dt).days
            
            message = (
                f"The Ouija Planchette last saw you move **{days} days** ago. "
                f"(On {last_seen_dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"
            )
            await ctx.send(message)
        finally:
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete()

    @ouijapoke.command(name="poke") 
    async def ouijapoke_random(self, ctx: commands.Context):
        """Pokes a random eligible member."""
        async with ctx.typing():
            settings = await self._get_settings(ctx.guild)
            p1_members, p2_members = await self._get_eligible_members(ctx.guild, settings.poke_days, "last_poked")
            member_to_poke = random.choice(p1_members) if p1_members else (random.choice(p2_members) if p2_members else None)
            
            if member_to_poke is None:
                return await ctx.send(f"No one is eligible to be poked (needs >{settings.poke_days} days of inactivity).")

            await self._set_last_action_time(ctx.guild, member_to_poke.id, "last_poked")
            await self._send_activity_message(ctx, member_to_poke, settings.poke_message, settings.poke_gifs)
    
    @ouijapoke.command(name="summon")
    async def ouijasummon_random(self, ctx: commands.Context):
        """Summons a random eligible member."""
        async with ctx.typing():
            settings = await self._get_settings(ctx.guild)
            p1_members, p2_members = await self._get_eligible_members(ctx.guild, settings.summon_days, "last_summoned")
            member_to_summon = random.choice(p1_members) if p1_members else (random.choice(p2_members) if p2_members else None)
            
            if member_to_summon is None:
                return await ctx.send(f"No one is eligible to be summoned (needs >{settings.summon_days} days of inactivity).")

            await self._set_last_action_time(ctx.guild, member_to_summon.id, "last_summoned")
            await self._send_activity_message(ctx, member_to_summon, settings.summon_message, settings.summon_gifs)


    # --- Admin Commands (Settings) ---

    @commands.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def ouijaset(self, ctx: commands.Context):
        """Manages the OuijaPoke settings."""
        await ctx.send_help(ctx.command)

    @ouijaset.command(name="view")
    async def ouijaset_view(self, ctx: commands.Context):
        """Displays the full settings page for the guild."""
        settings = await self._get_settings(ctx.guild)
        data = await self.config.guild(ctx.guild).all()
        next_auto = await self.config.guild(ctx.guild).next_auto_event()
        
        embed = discord.Embed(
            title="🔮 OuijaPoke Configuration",
            description="Current settings for this guild.",
            color=discord.Color.purple()
        )
        
        # 1. Inactivity Thresholds
        embed.add_field(
            name="🕒 Inactivity Thresholds",
            value=(
                f"👉 **Poke:** > {settings.poke_days} days inactive\n"
                f"👻 **Summon:** > {settings.summon_days} days inactive"
            ),
            inline=False
        )

        # 2. WarnSystem Integration
        warn_status = "Not Loaded"
        if self.bot.get_cog("WarnSystem"):
            warn_status = "Loaded & Ready"
        
        warn_l1 = f"{settings.warn_level_1_days} days" if settings.warn_level_1_days > 0 else "Disabled"
        warn_l3 = f"{settings.warn_level_3_days} days" if settings.warn_level_3_days > 0 else "Disabled"

        embed.add_field(
            name="⚠️ Inactivity Warnings",
            value=(
                f"**WarnSystem Status:** {warn_status}\n"
                f"**Level 1 Warn:** > {warn_l1} inactive\n"
                f"**Level 3 Warn (Kick):** > {warn_l3} inactive"
            ),
            inline=False
        )
        
        # 3. New Automated Policing
        nointro_chan = f"<#{settings.nointro_channel_id}>" if settings.nointro_channel_id else "Not Set"
        nointro_role = f"<@&{settings.nointro_role_id}>" if settings.nointro_role_id else "Not Set"
        
        level0_chan = f"<#{settings.level0_channel_id}>" if settings.level0_channel_id else "Not Set"
        level0_warn = f"> {settings.level0_warn_days} days" if settings.level0_warn_days > 0 else "Disabled"
        level0_kick = f"> {settings.level0_kick_days} days" if settings.level0_kick_days > 0 else "Disabled"

        embed.add_field(
            name="👮 Automated Policing",
            value=(
                f"**No Intro:** Check {nointro_role} after {settings.nointro_days} days -> Ping in {nointro_chan}\n"
                f"**Still Level 0 (Warn):** {level0_warn} -> Ping in {level0_chan}\n"
                f"**Still Level 0 (Kick):** {level0_kick}"
            ),
            inline=False
        )

        # 4. Activity Definition
        burst_desc = "Every message counts"
        if settings.required_messages > 1 and settings.required_window_hours > 0:
            burst_desc = f"**{settings.required_messages}** msgs in **{settings.required_window_hours}** hrs"
        
        embed.add_field(
            name="🏃 Activity Logic",
            value=(
                f"**Definition:** {burst_desc}\n"
                f"**Min Char Length:** {settings.min_message_length} chars"
            ),
            inline=False
        )

        # 5. Auto Poke Settings
        if next_auto:
            try:
                dt = datetime.fromisoformat(next_auto).replace(tzinfo=timezone.utc)
                diff = dt - datetime.now(timezone.utc)
                hours_left = int(diff.total_seconds() // 3600)
                mins_left = int((diff.total_seconds() % 3600) // 60)
                next_run_str = f"In {hours_left}h {mins_left}m"
            except:
                next_run_str = "Error parsing time"
        else:
            next_run_str = "Not Scheduled (Needs Init)"

        auto_chan_mention = f"<#{settings.auto_channel_id}>" if settings.auto_channel_id else "Not Set"

        embed.add_field(
            name="🤖 Automatic Actions (Daily)",
            value=(
                f"**Auto Channel:** {auto_chan_mention}\n"
                f"**Next Run:** {next_run_str}\n"
                f"**Odds:** 10% Poke / 10% Summon / 80% Idle"
            ),
            inline=False
        )
        
        # 6. Exclusions & Hibernation
        excl_roles = []
        for rid in data["excluded_roles"]:
            role = ctx.guild.get_role(rid)
            if role: excl_roles.append(role.mention)
            
        excl_chans = []
        for cid in data["excluded_channels"]:
            # formatted as channel mention
            excl_chans.append(f"<#{cid}>")

        embed.add_field(
            name="🚫 Channel Exclusions & 💤 Hibernation",
            value=(
                f"**Hibernating Roles:** {humanize_list(excl_roles) if excl_roles else 'None'}\n"
                f"**Excluded Channels:** {humanize_list(excl_chans) if excl_chans else 'None'}"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    # --- Configuration Commands ---

    @ouijaset.command(name="autochannel")
    async def ouijaset_autochannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """
        Sets the channel for automatic daily pokes and summons.
        Leave blank to disable automatic actions.
        """
        settings = await self._get_settings(ctx.guild)
        if channel:
            settings.auto_channel_id = channel.id
            await ctx.send(f"Automatic actions will now appear in {channel.mention}.")
        else:
            settings.auto_channel_id = None
            await ctx.send("Automatic actions disabled.")
        
        await self._set_settings(ctx.guild, settings)

    @ouijaset.command(name="forcerun")
    async def ouijaset_forcerun(self, ctx: commands.Context):
        """
        [Debug] Forces the automatic daily routine to run immediately.
        
        Note: This ignores the schedule but still respects the probability (10/10/80) and spam filters.
        It will reschedule the next run after completion.
        """
        settings = await self._get_settings(ctx.guild)
        if not settings.auto_channel_id:
            return await ctx.send("No auto channel set. Run `[p]ouijaset autochannel` first.")
        
        channel = ctx.guild.get_channel(settings.auto_channel_id)
        if not channel:
            return await ctx.send("The configured auto channel no longer exists.")
            
        result = await self._run_daily_lottery(ctx.guild, channel, settings)
        await ctx.send(result)
        await self._schedule_next_auto_event(ctx.guild)

    @ouijaset.command(name="pokedays")
    async def ouijaset_pokedays(self, ctx: commands.Context, days: int):
        """Sets days inactive for a 'poke'."""
        if days < 1: return await ctx.send("Days must be >= 1.")
        settings = await self._get_settings(ctx.guild)
        settings.poke_days = days
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Poke eligibility set to **{days}** days.")

    @ouijaset.command(name="summondays")
    async def ouijaset_summondays(self, ctx: commands.Context, days: int):
        """Sets days inactive for a 'summon'."""
        if days < 1: return await ctx.send("Days must be >= 1.")
        settings = await self._get_settings(ctx.guild)
        settings.summon_days = days
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Summon eligibility set to **{days}** days.")

    @ouijaset.command(name="activitythreshold")
    async def ouijaset_activitythreshold(self, ctx: commands.Context, messages: int, hours: float):
        """
        Sets the threshold for a user to be considered "active".
        
        Example: `[p]ouijaset activitythreshold 5 1`
        (User must send 5 messages within 1 hour to update their last seen time).
        
        Set messages to 1 to disable the burst requirement (default).
        """
        if messages < 1 or hours < 0:
            return await ctx.send("Messages must be >= 1 and hours must be >= 0.")
        
        settings = await self._get_settings(ctx.guild)
        settings.required_messages = messages
        settings.required_window_hours = hours
        await self._set_settings(ctx.guild, settings)
        
        await ctx.send(f"Activity threshold updated: Users must send **{messages} messages** within **{hours} hours** to be seen.")

    @ouijaset.command(name="minlength")
    async def ouijaset_minlength(self, ctx: commands.Context, length: int):
        """Sets the minimum character length for a message to count towards activity."""
        if length < 0: return await ctx.send("Length must be >= 0.")
        settings = await self._get_settings(ctx.guild)
        settings.min_message_length = length
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Minimum message length set to **{length}** characters.")

    # --- WarnSystem Integration Settings (Inactivity) ---

    @ouijaset.group(name="warnlevel", aliases=["warn"], invoke_without_command=True)
    async def ouijaset_warnlevel(self, ctx: commands.Context):
        """Manages WarnSystem integration for inactive users."""
        await ctx.send_help(ctx.command)

    @ouijaset_warnlevel.command(name="level1")
    async def ouijaset_warnlevel_1(self, ctx: commands.Context, days: int):
        """
        Sets days inactive to trigger a Level 1 warning via WarnSystem.
        Set to 0 to disable.
        """
        if days < 0: return await ctx.send("Days must be >= 0.")
        settings = await self._get_settings(ctx.guild)
        settings.warn_level_1_days = days
        await self._set_settings(ctx.guild, settings)
        
        if days == 0:
            await ctx.send("Level 1 inactivity warnings disabled.")
        else:
            await ctx.send(f"Users inactive for >**{days}** days will receive a Level 1 warning.")

    @ouijaset_warnlevel.command(name="level3")
    async def ouijaset_warnlevel_3(self, ctx: commands.Context, days: int):
        """
        Sets days inactive to trigger a Level 3 warning (Kick) via WarnSystem.
        Set to 0 to disable.
        """
        if days < 0: return await ctx.send("Days must be >= 0.")
        settings = await self._get_settings(ctx.guild)
        settings.warn_level_3_days = days
        await self._set_settings(ctx.guild, settings)
        
        if days == 0:
            await ctx.send("Level 3 inactivity warnings disabled.")
        else:
            await ctx.send(f"Users inactive for >**{days}** days will receive a Level 3 warning (Kick, if WarnSystem is configured).")

    # --- No Intro Settings ---
    
    @ouijaset.group(name="nointro", invoke_without_command=True)
    async def ouijaset_nointro(self, ctx: commands.Context):
        """Manages the 'No Intro' policing."""
        await ctx.send_help(ctx.command)
        
    @ouijaset_nointro.command(name="setup")
    async def nointro_setup(self, ctx: commands.Context, role: discord.Role, days: int, channel: discord.TextChannel, *, message: str):
        """
        Fully configures the No Intro check.
        
        Args:
            role: The 'No Intro' role to check for.
            days: Days since joining before alerting.
            channel: The channel to ping the user in.
            message: The message to send. Must include {mention}.
        """
        if "{mention}" not in message:
            return await ctx.send("Message must contain `{mention}` to ping the user.")
        
        settings = await self._get_settings(ctx.guild)
        settings.nointro_role_id = role.id
        settings.nointro_days = days
        settings.nointro_channel_id = channel.id
        settings.nointro_message = message
        
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"✅ No Intro Check configured! Users with **@{role.name}** for >**{days}** days will be pinged in {channel.mention}.")

    @ouijaset_nointro.command(name="disable")
    async def nointro_disable(self, ctx: commands.Context):
        """Disables the No Intro check."""
        settings = await self._get_settings(ctx.guild)
        settings.nointro_days = 0
        await self._set_settings(ctx.guild, settings)
        await ctx.send("No Intro check disabled.")

    # --- Level 0 Settings ---
    
    @ouijaset.group(name="levelzero", aliases=["stillzero"], invoke_without_command=True)
    async def ouijaset_levelzero(self, ctx: commands.Context):
        """Manages 'Still at Level 0' policing."""
        await ctx.send_help(ctx.command)

    @ouijaset_levelzero.command(name="warn")
    async def levelzero_warn(self, ctx: commands.Context, days: int, channel: discord.TextChannel, *, message: str):
        """
        Configures the warning message for users still at Level 0.
        
        Args:
            days: Days since joining to trigger the warning.
            channel: Channel to send the message in.
            message: Message to send. Must include {mention}.
        """
        if "{mention}" not in message:
            return await ctx.send("Message must contain `{mention}`.")
        
        settings = await self._get_settings(ctx.guild)
        settings.level0_warn_days = days
        settings.level0_channel_id = channel.id
        settings.level0_message = message
        
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"✅ Users still at Level 0 after **{days}** days will be pinged in {channel.mention}.")

    @ouijaset_levelzero.command(name="kick")
    async def levelzero_kick(self, ctx: commands.Context, days: int, *, reason: str = "Remained at Level 0 for too long."):
        """
        Configures the Auto-Kick (WarnSystem Level 3) for users still at Level 0.
        
        Args:
            days: Days since joining to trigger the kick. Set to 0 to disable.
            reason: Reason logged in WarnSystem.
        """
        settings = await self._get_settings(ctx.guild)
        settings.level0_kick_days = days
        settings.level0_kick_reason = reason
        
        await self._set_settings(ctx.guild, settings)
        if days > 0:
            await ctx.send(f"✅ Users still at Level 0 after **{days}** days will receive a **Level 3 Warning (Kick)**.")
        else:
            await ctx.send("Level 0 Kick disabled.")

    # --- Excluded Channels ---

    @ouijaset.group(name="excludechannel", aliases=["exclchannel"], invoke_without_command=True)
    async def ouijaset_excludechannel(self, ctx: commands.Context):
        """Manages channels where messages are ignored."""
        channels = await self.config.guild(ctx.guild).excluded_channels()
        if not channels:
            return await ctx.send("No channels are currently excluded.")
        
        channel_mentions = [f"<#{c}>" for c in channels]
        await ctx.send(f"**Excluded Channels:**\n{humanize_list(channel_mentions)}")

    @ouijaset_excludechannel.command(name="add")
    async def excludechannel_add(self, ctx: commands.Context, channel: discord.TextChannel):
        """Adds a channel to the exclusion list."""
        async with self.config.guild(ctx.guild).excluded_channels() as channels:
            if channel.id in channels:
                return await ctx.send("Channel is already excluded.")
            channels.append(channel.id)
        await ctx.send(f"Channel {channel.mention} added to exclusions.")

    @ouijaset_excludechannel.command(name="remove")
    async def excludechannel_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Removes a channel from the exclusion list."""
        async with self.config.guild(ctx.guild).excluded_channels() as channels:
            if channel.id not in channels:
                return await ctx.send("Channel is not excluded.")
            channels.remove(channel.id)
        await ctx.send(f"Channel {channel.mention} removed from exclusions.")

    # --- Hibernating (Excluded) Roles Management ---

    @ouijaset.group(name="hibernatingroles", aliases=["hibernate", "hibernating", "excludedroles", "exclrole"], invoke_without_command=True)
    async def ouijaset_hibernatingroles(self, ctx: commands.Context):
        """
        Manages roles whose members are permanently in hibernation (excluded from being poked/summoned).
        """
        excluded_roles = await self.config.guild(ctx.guild).excluded_roles()
        
        if not excluded_roles:
            return await ctx.send("No roles are currently set as Hibernating.")
        
        role_names = []
        for role_id in excluded_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                role_names.append(role.name)
                
        await ctx.send(
            f"The following roles are marked as **Hibernating** (members are ineligible):\n"
            f"{humanize_list(role_names)}"
        )

    @ouijaset_hibernatingroles.command(name="add")
    async def hibernatingroles_add(self, ctx: commands.Context, role: discord.Role):
        """Adds a role to the Hibernating list."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded_roles:
            if role.id in excluded_roles:
                return await ctx.send(f"The role **{role.name}** is already set to Hibernate.")
            excluded_roles.append(role.id)
        
        await ctx.send(f"Added role **{role.name}** to the Hibernating list. Members with this role will no longer be poked or summoned.")

    @ouijaset_hibernatingroles.command(name="remove")
    async def hibernatingroles_remove(self, ctx: commands.Context, role: discord.Role):
        """Removes a role from the Hibernating list."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded_roles:
            if role.id not in excluded_roles:
                return await ctx.send(f"The role **{role.name}** was not found in the Hibernating list.")
            excluded_roles.remove(role.id)
            
        await ctx.send(f"Removed role **{role.name}** from the Hibernating list. Members with this role may now be poked or summoned if they meet the inactivity criteria.")

    # --- Eligible Members Display ---

    @ouijaset.command(name="eligible")
    async def ouijaset_eligible(self, ctx: commands.Context):
        """Displays a list of all members currently eligible for being poked/summoned OR excluded (hibernating)."""
        
        settings = await self._get_settings(ctx.guild)

        async with ctx.typing():
            eligible_members = await self._get_all_eligible_member_data(ctx)
            excluded_eligible_members = await self._get_excluded_eligible_members(ctx)

        # 1. Handle main eligible list
        if eligible_members:
            # Prepare content for display
            entries = []
            for i, member_data in enumerate(eligible_members):
                entry = (
                    f"**{i+1}. {member_data['member'].display_name}** (`{member_data['member'].id}`)\n"
                    f"  ➡️ Last Active: **{member_data['last_seen_days']} days ago**\n"
                    f"  👀 Last Poked: {member_data['last_poked']}\n"
                    f"  👻 Last Summoned: {member_data['last_summoned']}\n"
                    f"  ✅ Eligible For: {member_data['eligible_for']}"
                )
                entries.append(entry)

            # Use basic page separation for clarity
            pages = []
            MAX_CHARS = 1000
            current_page = ""
            
            for entry in entries:
                if len(current_page) + len(entry) + 2 > MAX_CHARS:
                    pages.append(current_page)
                    current_page = entry + "\n"
                else:
                    current_page += entry + "\n"
            if current_page:
                pages.append(current_page)
            
            # Send the pages
            for page_num, content in enumerate(pages):
                embed = discord.Embed(
                    title=f"👻 Active Eligible Members ({len(eligible_members)} Total)",
                    description=f"Members below are eligible for action (Sorted by inactivity):\n\n{content}",
                    color=discord.Color.dark_purple()
                )
                embed.set_footer(text=f"Page {page_num + 1}/{len(pages)} (Eligible) | Poke Days: {settings.poke_days}, Summon Days: {settings.summon_days}")
                await ctx.send(embed=embed)
        else:
            await ctx.send("🎉 **No members are currently eligible** for poking or summoning based on activity alone.")

        # 2. Handle hibernating (excluded) members list
        if excluded_eligible_members:
            excluded_entries = []
            for i, member_data in enumerate(excluded_eligible_members):
                entry = (
                    f"**{i+1}. {member_data['member'].display_name}** (`{member_data['member'].id}`)\n"
                    f"  ➡️ Last Active: **{member_data['last_seen_days']} days ago**\n"
                    f"  🚫 Excluded By: **{member_data['excluded_by']}**\n"
                    f"  ⚠️ *Would be Eligible For: {member_data['eligible_for']}*"
                )
                excluded_entries.append(entry)

            # Use basic page separation for clarity
            excluded_pages = []
            MAX_CHARS = 1000
            current_page = ""
            
            for entry in excluded_entries:
                if len(current_page) + len(entry) + 2 > MAX_CHARS:
                    excluded_pages.append(current_page)
                    current_page = entry + "\n"
                else:
                    current_page += entry + "\n"
            if current_page:
                excluded_pages.append(current_page)
            
            # Send the excluded pages
            for page_num, content in enumerate(excluded_pages):
                embed = discord.Embed(
                    title=f"💤 Hibernating Eligible Members ({len(excluded_eligible_members)} Total)",
                    description=f"Members below are inactive enough, but **HIBERNATING** due to role:\n\n{content}",
                    color=discord.Color.orange()
                )
                embed.set_footer(text=f"Page {page_num + 1}/{len(excluded_pages)} (Hibernating) | Total Hibernating: {len(excluded_eligible_members)}")
                await ctx.send(embed=embed)
        elif eligible_members:
             # Only send this message if we sent the first embed, to keep the output clean
             await ctx.send("✅ No members are currently hibernating who would otherwise be eligible for action.")

    # --- Status Listing (New Feature) ---

    @ouijaset.command(name="status")
    async def ouijaset_status(self, ctx: commands.Context):
        """
        Lists all members with their current activity status.
        ✅ = Active
        👉 = Eligible for Poke
        👻 = Eligible for Summon
        ❓ = Unknown data
        💤 = Hibernating (Excluded by Role)
        """
        async with ctx.typing():
            settings = await self._get_settings(ctx.guild)
            data = await self.config.guild(ctx.guild).all()
            last_seen_data = data["last_seen"]
            excluded_roles = data["excluded_roles"]
            
            poke_days = settings.poke_days
            summon_days = settings.summon_days
            
            poke_cutoff = self._get_inactivity_cutoff(poke_days)
            summon_cutoff = self._get_inactivity_cutoff(summon_days)
            
            status_entries = []
            
            # We want to list ALL members, not just those with data
            for member in ctx.guild.members:
                if member.bot:
                    continue
                
                last_seen_str = last_seen_data.get(str(member.id))
                
                # Determine exclusion FIRST to override icons
                is_hibernating = self._is_excluded(member, excluded_roles)
                
                icon = "❓" # Default to Unknown Data
                days_ago_str = "Never"
                sort_val = float('inf') # Infinity for sorting 'Never' at the end of their group
                
                if last_seen_str:
                    try:
                        last_seen_dt = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
                        diff_days = (datetime.now(timezone.utc) - last_seen_dt).days
                        days_ago_str = f"{diff_days} days ago"
                        sort_val = diff_days
                        
                        if not is_hibernating:
                            # Determine normal status based on thresholds
                            if last_seen_dt >= poke_cutoff:
                                icon = "✅" # Active
                            elif last_seen_dt >= summon_cutoff:
                                icon = "👉" # Inactive enough for poke, but not summon
                            elif last_seen_dt < summon_cutoff:
                                icon = "👻" # Inactive enough for summon 
                            else:
                                icon = "❓" # Unknown data
                            
                    except ValueError:
                        pass
                
                if is_hibernating:
                    icon = "💤"
                
                # Primary Sort Key: 0 for included, 1 for excluded (Hibernating goes to bottom)
                primary_sort = 1 if is_hibernating else 0
                
                line = f"{icon} **{member.display_name}** ({member.id}) | {days_ago_str}"
                
                # Store tuple: ((primary_group, days_inactive), display_line)
                status_entries.append(((primary_sort, sort_val), line))

            if not status_entries:
                return await ctx.send("No non-bot members found.")
            
            # Sort by primary group (Included < Hibernating), then by days inactive
            status_entries.sort(key=lambda x: x[0])
            
            # Extract lines
            lines = [entry[1] for entry in status_entries]
            
            # Pagination
            pages = []
            current_page = []
            char_count = 0
            
            for line in lines:
                if char_count + len(line) + 1 > 1000: # Safety limit for embed description
                    pages.append("\n".join(current_page))
                    current_page = [line]
                    char_count = len(line)
                else:
                    current_page.append(line)
                    char_count += len(line) + 1
            
            if current_page:
                pages.append("\n".join(current_page))
                
            for i, page_content in enumerate(pages):
                embed = discord.Embed(
                    title=f"Member Activity Status (Sorted by Activity)",
                    description=page_content,
                    color=discord.Color.gold()
                )
                embed.set_footer(text=f"Page {i+1}/{len(pages)} | ✅ Active | 👉 Poke | 👻 Summon | 💤 Hibernating")
                await ctx.send(embed=embed)

    # --- Message Settings ---
    
    @ouijaset.command(name="pokemessage")
    async def ouijaset_pokemessage(self, ctx: commands.Context, *, message: str):
        """Sets the message used when a user is poked."""
        if "{user_mention}" not in message:
            return await ctx.send("Message must contain `{user_mention}`.")
        settings = await self._get_settings(ctx.guild)
        settings.poke_message = message
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Poke message updated.")
        
    @ouijaset.command(name="summonmessage")
    async def ouijaset_summonmessage(self, ctx: commands.Context, *, message: str):
        """Sets the message used when a user is summoned."""
        if "{user_mention}" not in message:
            return await ctx.send("Message must contain `{user_mention}`.")
        settings = await self._get_settings(ctx.guild)
        settings.summon_message = message
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Summon message updated.")

    # --- GIF Management (Shortened for brevity, logic unchanged) ---
    @ouijaset.group(name="pokegifs", invoke_without_command=True)
    async def ouijaset_pokegifs(self, ctx: commands.Context):
        """Manages poke GIFs."""
        settings = await self._get_settings(ctx.guild)
        await ctx.send(f"**Poke GIFs:** {len(settings.poke_gifs)} stored." if settings.poke_gifs else "No Poke GIFs.")

    @ouijaset_pokegifs.command(name="add")
    async def pokegifs_add(self, ctx: commands.Context, url: str):
        if not self._is_valid_gif_url(url): return await ctx.send("Invalid URL.")
        settings = await self._get_settings(ctx.guild)
        if url in settings.poke_gifs: return await ctx.send("Exists.")
        settings.poke_gifs.append(url)
        await self._set_settings(ctx.guild, settings)
        await ctx.send("Added.")

    @ouijaset_pokegifs.command(name="remove")
    async def pokegifs_remove(self, ctx: commands.Context, url: str):
        settings = await self._get_settings(ctx.guild)
        try: settings.poke_gifs.remove(url)
        except: return await ctx.send("Not found.")
        await self._set_settings(ctx.guild, settings)
        await ctx.send("Removed.")

    @ouijaset.group(name="summongifs", invoke_without_command=True)
    async def ouijaset_summongifs(self, ctx: commands.Context):
        """Manages summon GIFs."""
        settings = await self._get_settings(ctx.guild)
        await ctx.send(f"**Summon GIFs:** {len(settings.summon_gifs)} stored." if settings.summon_gifs else "No Summon GIFs.")

    @ouijaset_summongifs.command(name="add")
    async def summongifs_add(self, ctx: commands.Context, url: str):
        if not self._is_valid_gif_url(url): return await ctx.send("Invalid URL.")
        settings = await self._get_settings(ctx.guild)
        if url in settings.summon_gifs: return await ctx.send("Exists.")
        settings.summon_gifs.append(url)
        await self._set_settings(ctx.guild, settings)
        await ctx.send("Added.")

    @ouijaset_summongifs.command(name="remove")
    async def summongifs_remove(self, ctx: commands.Context, url: str):
        settings = await self._get_settings(ctx.guild)
        try: settings.summon_gifs.remove(url)
        except: return await ctx.send("Not found.")
        await self._set_settings(ctx.guild, settings)
        await ctx.send("Removed.")

    # --- Override/Reset ---

    @ouijaset.command(name="override")
    async def ouijaset_override(self, ctx: commands.Context, role: discord.Role, days_ago: int):
        """Overrides the last active date for all members of a given role."""
        if days_ago < 0: return await ctx.send("Days must be >= 0.")
        async with ctx.typing():
            target_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
            data = await self.config.guild(ctx.guild).last_seen()
            for member in role.members:
                if not member.bot: data[str(member.id)] = target_dt.isoformat()
            await self.config.guild(ctx.guild).last_seen.set(data)
        await ctx.send(f"Set **{len(role.members)}** members to **{days_ago} days ago**.")
        # Trigger warn check next loop

    @ouijaset.command(name="markactive")
    async def ouijaset_markactive(self, ctx: commands.Context, member: discord.Member):
        """
        Manually marks a user as active right now.
        
        This resets their inactivity timer and removes any warnings flags.
        """
        await self._update_last_seen(ctx.guild, member.id)
        await ctx.send(f"✅ **{member.display_name}** has been marked as active. Their timer and warnings are reset.")

    @ouijaset.command(name="resetactivity")
    @checks.is_owner()
    async def ouijaset_resetactivity(self, ctx: commands.Context):
        """[OWNER] Wipes all activity data."""
        await ctx.send("Are you sure? Type `yes`.")
        try:
            if (await self.bot.wait_for('message', check=lambda m: m.author==ctx.author and m.content.lower()=='yes', timeout=30)):
                await self.config.guild(ctx.guild).last_seen.set({})
                await self.config.guild(ctx.guild).last_poked.set({})
                await self.config.guild(ctx.guild).last_summoned.set({})
                await self.config.guild(ctx.guild).warned_users.set({})
                await ctx.send("Data reset.")
        except TimeoutError:
            await ctx.send("Cancelled.")