import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timedelta, timezone
import asyncio
import random
import re
from typing import Union, List, Tuple, Dict, Any

# Pydantic is used for structured configuration in modern Red cogs
try:
    from pydantic import BaseModel, Field, conint, conlist
except ImportError:
    BaseModel = object
    Field = lambda *args, **kwargs: None
    conint = lambda **kwargs: int
    conlist = lambda *args, **kwargs: list


# --- Configuration Schema (Settings) ---

class InactivityRolePair(BaseModel):
    """Schema for a single role and its required inactivity days."""
    role_id: int = Field(description="The ID of the role to assign.")
    days: conint(ge=1) = Field(description="Minimum days of inactivity required to assign this role.")

class OuijaSettings(BaseModel):
    """Schema for guild configuration settings."""
    poke_days: conint(ge=1) = Field(default=30, description="Days a member must be inactive to be eligible for a poke.")
    summon_days: conint(ge=1) = Field(default=60, description="Days a member must be inactive to be eligible for a summon.")
    
    # UPDATED: List of role/day pairings for automated assignment
    inactivity_roles: conlist(item_type=InactivityRolePair, min_length=0) = Field(
        default_factory=list,
        description="List of roles to assign based on inactivity duration."
    )
    
    # NEW: Flag to control auto-unassign behavior
    auto_unassign: bool = Field(
        default=True,
        description="If True, remove inactivity roles when a member becomes active."
    )

    # NEW: List of roles that are *ignored* by all tracking and assignment logic
    ignored_roles: conlist(item_type=int, min_length=0) = Field(
        default_factory=list,
        description="A list of role IDs to ignore for all tracking/poking/summoning logic."
    )

    # NEW: List of channel IDs where activity is ignored
    ignored_channels: conlist(item_type=int, min_length=0) = Field(
        default_factory=list,
        description="A list of channel IDs where messages are ignored for activity tracking."
    )

    # NEW: Target channel for automatic poke/summon messages
    target_channel_id: Union[int, None] = Field(
        default=None,
        description="The channel ID where the bot should send automated poke/summon messages."
    )

    # NEW: Custom message for 'poke'
    poke_message: str = Field(
        default="Psst... {mention}, it's been {days} days since you last showed your face. We miss you! Come say hello.",
        description="The message used when poking an inactive member. Supports {mention} and {days}."
    )

    # NEW: Custom message for 'summon'
    summon_message: str = Field(
        default="By the power of the Ouija board, we summon {mention}! You've been gone {days} days. Is there life out there?",
        description="The message used when summoning a highly inactive member. Supports {mention} and {days}."
    )
    
    # NEW: Flag to enable automatic poke/summon
    auto_poke_enabled: bool = Field(default=False, description="Whether the bot should automatically poke/summon.")
    
    # NEW: Interval (in hours) for the automatic check
    auto_check_interval_hours: conint(ge=1) = Field(default=24, description="How often (in hours) to run the automatic check for inactivity.")

    # NEW: Maximum pokes/summons per check
    max_auto_pokes: conint(ge=1) = Field(default=5, description="Maximum number of members to poke/summon in a single automatic check.")

    # NEW: Flag to track non-message activity (e.g., voice, reactions)
    track_non_message_activity: bool = Field(
        default=False,
        description="If True, activity tracking includes voice state changes and reactions."
    )
    
    # NEW: Flag to allow pokes/summons in the target channel
    allow_pokes_in_target_channel: bool = Field(
        default=True,
        description="If False, pokes/summons will only be sent to the member's DMs."
    )

    # NEW: Flag to enable a quiet mode for role assignment (no log messages)
    quiet_role_assignment: bool = Field(
        default=False,
        description="If True, the bot will not send log messages for role assignment/removal."
    )


# --- Cog Class ---

class Ouijapoke(commands.Cog):
    """
    Track member activity and assign roles or poke/summon inactive users.
    """

    def __init__(self, bot):
        self.bot = bot
        # Initialize the config with the new schema definition
        # Use an empty dictionary as the default to allow Red's Config to migrate old data safely
        self.config = Config.get_conf(self, identifier=140120250425, force_registration=True)
        
        # Default settings are stored in a format compatible with Red's Config structure
        # We'll use the pydantic model for validation and internal representation,
        # but the actual config storage needs to reflect the field structure.
        self.default_guild_settings = {
            "poke_days": 30,
            "summon_days": 60,
            "inactivity_roles": [], # Stored as a list of dicts: [{"role_id": int, "days": int}]
            "auto_unassign": True,
            "ignored_roles": [],
            "ignored_channels": [],
            "target_channel_id": None,
            "poke_message": "Psst... {mention}, it's been {days} days since you last showed your face. We miss you! Come say hello.",
            "summon_message": "By the power of the Ouija board, we summon {mention}! You've been gone {days} days. Is there life out there?",
            "auto_poke_enabled": False,
            "auto_check_interval_hours": 24,
            "max_auto_pokes": 5,
            "track_non_message_activity": False,
            "allow_pokes_in_target_channel": True,
            "quiet_role_assignment": False,
            
            # Internal tracking data (not part of OuijaSettings Pydantic model)
            "last_seen": {},  # {user_id: timestamp_float}
            "last_poked": {}, # {user_id: timestamp_float}
            "last_summoned": {}, # {user_id: timestamp_float}
        }
        
        self.config.register_guild(**self.default_guild_settings)
        self.last_check_times = {} # {guild_id: timestamp_float}
        
        self.activity_cache = {} # {guild_id: {user_id: last_seen_dt}} for quick access
        
        # Task for periodic activity checks
        self.bg_task = self.bot.loop.create_task(self.auto_check_loop())


    def cog_unload(self):
        """Clean up the background task when the cog is unloaded."""
        if self.bg_task:
            self.bg_task.cancel()

    # --- Utility Methods ---

    def _get_guild_data_path(self, guild_id: int) -> str:
        """Helper to get the base path for a guild's artifact data."""
        appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';
        # We store data under /artifacts/{appId}/public/data/ouijapoke/{guildId}
        # This is a good place for multi-user/multi-admin cog data.
        return f"artifacts/{appId}/public/data/ouijapoke/{guild_id}"

    async def _get_guild_settings(self, guild: discord.Guild) -> OuijaSettings:
        """Retrieves and validates guild settings using the Pydantic schema."""
        raw_settings = await self.config.guild(guild).all()
        # Filter out internal tracking data for Pydantic validation if necessary
        # However, since we define default_guild_settings to match, this should be fine.
        
        # We only want the settings part for the Pydantic model
        settings_keys = OuijaSettings.__fields__.keys()
        settings_data = {k: v for k, v in raw_settings.items() if k in settings_keys}

        try:
            # Pydantic will validate types and apply defaults for missing fields
            return OuijaSettings(**settings_data)
        except Exception as e:
            print(f"Error validating OuijaSettings for guild {guild.id}: {e}")
            # Fallback to defaults or raise a more appropriate error
            return OuijaSettings() # Returns a model with all defaults
            
    async def _get_last_seen(self, guild: discord.Guild) -> Dict[int, float]:
        """Retrieves the raw last_seen dictionary."""
        return await self.config.guild(guild).last_seen()

    async def _set_last_seen(self, guild: discord.Guild, data: Dict[int, float]):
        """Sets the raw last_seen dictionary."""
        await self.config.guild(guild).last_seen.set(data)
        
    async def _get_last_poked(self, guild: discord.Guild) -> Dict[int, float]:
        """Retrieves the raw last_poked dictionary."""
        return await self.config.guild(guild).last_poked()

    async def _set_last_poked(self, guild: discord.Guild, data: Dict[int, float]):
        """Sets the raw last_poked dictionary."""
        await self.config.guild(guild).last_poked.set(data)

    async def _get_last_summoned(self, guild: discord.Guild) -> Dict[int, float]:
        """Retrieves the raw last_summoned dictionary."""
        return await self.config.guild(guild).last_summoned()

    async def _set_last_summoned(self, guild: discord.Guild, data: Dict[int, float]):
        """Sets the raw last_summoned dictionary."""
        await self.config.guild(guild).last_summoned.set(data)

    # --- Activity Tracking Listeners ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track activity based on messages sent."""
        if message.guild is None or message.author.bot:
            return

        settings = await self._get_guild_settings(message.guild)

        if message.channel.id in settings.ignored_channels:
            return

        # Check if the member has an ignored role
        member = message.author
        if isinstance(member, discord.Member):
            for role in member.roles:
                if role.id in settings.ignored_roles:
                    return

        timestamp = message.created_at.timestamp()
        
        # Update last_seen
        last_seen = await self._get_last_seen(message.guild)
        last_seen[member.id] = timestamp
        await self._set_last_seen(message.guild, last_seen)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]):
        """Track activity based on reactions added if enabled."""
        if isinstance(user, discord.User) or user.bot or reaction.message.guild is None:
            return

        guild = reaction.message.guild
        settings = await self._get_guild_settings(guild)
        
        if not settings.track_non_message_activity:
            return

        if reaction.message.channel.id in settings.ignored_channels:
            return

        # Check if the member has an ignored role
        for role in user.roles:
            if role.id in settings.ignored_roles:
                return

        # Use the reaction time as the timestamp
        timestamp = datetime.now(timezone.utc).timestamp()
        
        # Update last_seen
        last_seen = await self._get_last_seen(guild)
        last_seen[user.id] = timestamp
        await self._set_last_seen(guild, last_seen)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Track activity based on voice state changes if enabled."""
        if member.bot or member.guild is None:
            return

        guild = member.guild
        settings = await self._get_guild_settings(guild)
        
        if not settings.track_non_message_activity:
            return

        # If the user joins or leaves a voice channel
        if before.channel != after.channel:
            # Check if the member has an ignored role
            for role in member.roles:
                if role.id in settings.ignored_roles:
                    return

            # Use the current time as the timestamp
            timestamp = datetime.now(timezone.utc).timestamp()
            
            # Update last_seen
            last_seen = await self._get_last_seen(guild)
            last_seen[member.id] = timestamp
            await self._set_last_seen(guild, last_seen)


    # --- Inactivity Role and Notification Logic ---

    async def _get_inactivity_info(self, member: discord.Member, last_seen: Dict[int, float]) -> Tuple[int, timedelta]:
        """
        Calculates inactivity duration for a member.
        Returns: Tuple[user_id, timedelta]
        """
        user_id = member.id
        now = datetime.now(timezone.utc)
        
        last_seen_ts = last_seen.get(user_id)
        if last_seen_ts is None:
            # If never seen, assume activity started when they joined the guild
            last_seen_dt = member.joined_at.replace(tzinfo=timezone.utc)
        else:
            last_seen_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)

        inactivity_duration = now - last_seen_dt
        return user_id, inactivity_duration


    async def _process_inactivity_roles(self, guild: discord.Guild, settings: OuijaSettings, last_seen: Dict[int, float]):
        """Assigns and removes inactivity roles based on configured days."""
        if not settings.inactivity_roles and not settings.auto_unassign:
            return

        inactivity_roles = sorted(settings.inactivity_roles, key=lambda x: x.days, reverse=True)
        all_role_ids = {r.role_id for r in inactivity_roles}

        log_messages = []

        for member in guild.members:
            if member.bot:
                continue
            
            # Skip if member has an ignored role
            if any(role.id in settings.ignored_roles for role in member.roles):
                continue

            user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
            days_inactive = inactivity_duration.days

            # 1. Determine the highest applicable role
            target_role = None
            for role_pair in inactivity_roles:
                if days_inactive >= role_pair.days:
                    target_role = guild.get_role(role_pair.role_id)
                    if target_role:
                        break # Found the highest applicable role
            
            member_role_ids = {r.id for r in member.roles}
            current_inactivity_roles = member_role_ids.intersection(all_role_ids)

            # 2. Assign the target role (if needed)
            if target_role and target_role.id not in current_inactivity_roles:
                try:
                    await member.add_roles(target_role, reason=f"Inactivity: {days_inactive} days.")
                    if not settings.quiet_role_assignment:
                        log_messages.append(f"✅ Assigned **{target_role.name}** to {member.display_name} ({days_inactive} days inactive).")
                except discord.Forbidden:
                    log_messages.append(f"⚠️ Failed to assign **{target_role.name}** to {member.display_name} (Missing Permissions).")
            
            # 3. Remove other/outdated inactivity roles
            roles_to_remove = []
            
            # Check for roles that should be removed because they are lower than the new target
            for role_id in current_inactivity_roles:
                if target_role and role_id != target_role.id:
                    roles_to_remove.append(guild.get_role(role_id))

            # Check for roles that should be removed due to new activity (auto_unassign)
            if settings.auto_unassign and not target_role and current_inactivity_roles:
                 # Member is now active enough not to need any inactivity role
                for role_id in current_inactivity_roles:
                    roles_to_remove.append(guild.get_role(role_id))
            
            # Perform removal
            for role in [r for r in roles_to_remove if r is not None]:
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Activity detected or better inactivity role assigned.")
                        if not settings.quiet_role_assignment:
                            log_messages.append(f"❌ Removed **{role.name}** from {member.display_name}.")
                    except discord.Forbidden:
                        log_messages.append(f"⚠️ Failed to remove **{role.name}** from {member.display_name} (Missing Permissions).")

        # Log results to the console
        if log_messages:
            print(f"Ouijapoke Role Assignment Log for {guild.name}:")
            for msg in log_messages:
                print(msg)


    async def _send_notification(self, member: discord.Member, action_type: str, days_inactive: int, settings: OuijaSettings) -> bool:
        """Sends a poke or summon notification to a member."""
        message_template = settings.poke_message if action_type == "poke" else settings.summon_message
        
        # Format the message
        message = message_template.format(mention=member.mention, days=days_inactive)

        # 1. Try to DM
        try:
            await member.send(
                f"👻 **Ouijapoke Alert** from {member.guild.name}:\n"
                f"{message}"
            )
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            # If DM fails, try the target channel (if allowed)
            pass

        # 2. Try target channel
        if settings.target_channel_id and settings.allow_pokes_in_target_channel:
            target_channel = member.guild.get_channel(settings.target_channel_id)
            if target_channel:
                try:
                    await target_channel.send(message)
                    return True
                except discord.Forbidden:
                    print(f"Ouijapoke: Failed to send {action_type} message to target channel {target_channel.name} due to permissions.")
                except discord.HTTPException as e:
                    print(f"Ouijapoke: Failed to send {action_type} message to target channel: {e}")
        
        return False # Failed to send via DM or target channel

    async def _process_pokes_and_summons(self, guild: discord.Guild, settings: OuijaSettings, last_seen: Dict[int, float]):
        """Checks for members to poke or summon and notifies them."""
        
        if not settings.auto_poke_enabled or settings.max_auto_pokes <= 0:
            return

        now_ts = datetime.now(timezone.utc).timestamp()
        
        # Load existing tracking data
        last_poked = await self._get_last_poked(guild)
        last_summoned = await self._get_last_summoned(guild)

        poke_threshold = timedelta(days=settings.poke_days)
        summon_threshold = timedelta(days=settings.summon_days)

        members_to_poke = []
        members_to_summon = []

        for member in guild.members:
            if member.bot:
                continue
            
            # Skip if member has an ignored role
            if any(role.id in settings.ignored_roles for role in member.roles):
                continue

            user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
            
            # Check for summons (highest priority)
            if inactivity_duration >= summon_threshold:
                # Only summon if they haven't been summoned more recently than they were last seen
                last_summon_ts = last_summoned.get(user_id, 0)
                last_seen_ts = last_seen.get(user_id, 0)

                if last_summon_ts < last_seen_ts:
                    # They've been active since the last summon, so they are eligible for a new one
                    members_to_summon.append((member, inactivity_duration.days))

            # Check for pokes
            elif inactivity_duration >= poke_threshold:
                # Only poke if they haven't been poked or summoned more recently than they were last seen
                last_poke_ts = last_poked.get(user_id, 0)
                last_summon_ts = last_summoned.get(user_id, 0)
                last_notification_ts = max(last_poke_ts, last_summon_ts)
                last_seen_ts = last_seen.get(user_id, 0)

                if last_notification_ts < last_seen_ts:
                    # They've been active since the last poke/summon, so they are eligible for a new poke
                    members_to_poke.append((member, inactivity_duration.days))


        # Sort for more natural progression (most inactive first)
        members_to_summon.sort(key=lambda x: x[1], reverse=True)
        members_to_poke.sort(key=lambda x: x[1], reverse=True)
        
        total_pokes_done = 0

        # Process summons first
        for member, days_inactive in members_to_summon:
            if total_pokes_done >= settings.max_auto_pokes:
                break
                
            sent = await self._send_notification(member, "summon", days_inactive, settings)
            if sent:
                last_summoned[member.id] = now_ts
                total_pokes_done += 1
                # Remove from poke eligibility if they were just summoned
                if member.id in last_poked:
                    del last_poked[member.id]

        # Process pokes
        for member, days_inactive in members_to_poke:
            if total_pokes_done >= settings.max_auto_pokes:
                break
            
            # Re-check to ensure they weren't just summoned
            if member.id in last_summoned:
                continue

            sent = await self._send_notification(member, "poke", days_inactive, settings)
            if sent:
                last_poked[member.id] = now_ts
                total_pokes_done += 1

        # Save updated tracking data
        if total_pokes_done > 0:
            await self._set_last_poked(guild, last_poked)
            await self._set_last_summoned(guild, last_summoned)
            print(f"Ouijapoke: Completed auto-check for {guild.name}. Notified {total_pokes_done} members.")
        
    
    # --- Background Loop ---
    
    async def auto_check_loop(self):
        """The main background loop for periodic activity checks and actions."""
        # Wait until the bot is fully ready
        await self.bot.wait_until_ready() 
        
        while self.bot.is_ready():
            try:
                # Iterate over all guilds the bot is in
                for guild in self.bot.guilds:
                    settings = await self._get_guild_settings(guild)
                    
                    if not settings.auto_poke_enabled and not settings.inactivity_roles:
                        continue # Skip if no features are enabled
                        
                    interval_seconds = settings.auto_check_interval_hours * 3600
                    last_check = self.last_check_times.get(guild.id, 0)
                    now_ts = datetime.now(timezone.utc).timestamp()
                    
                    if now_ts - last_check >= interval_seconds:
                        
                        last_seen = await self._get_last_seen(guild)
                        
                        # 1. Process Roles (Assignment/Removal)
                        await self._process_inactivity_roles(guild, settings, last_seen)
                        
                        # 2. Process Pokes/Summons (Notifications)
                        if settings.auto_poke_enabled:
                            await self._process_pokes_and_summons(guild, settings, last_seen)
                            
                        # Update last check time
                        self.last_check_times[guild.id] = now_ts
                    
                # Wait for the shortest interval configured across all guilds, minimum 1 hour
                # For simplicity, we'll just wait a fixed amount of time (e.g., 1 hour or the shortest configured)
                # We'll use a fixed 30-minute sleep for this loop to be responsive but not spam config reads
                await asyncio.sleep(1800) 

            except asyncio.CancelledError:
                # The task was cancelled, break the loop
                break
            except Exception as e:
                # Catch any other exceptions and continue the loop after a delay
                print(f"An error occurred in the Ouijapoke background loop: {e}")
                await asyncio.sleep(60) # Wait a minute before retrying

    # --- Commands ---

    @commands.group(name="ouijaset")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def ouijaset(self, ctx: commands.Context):
        """Configuration for the Ouijapoke cog."""
        pass

    @ouijaset.command(name="show")
    async def ouijaset_show(self, ctx: commands.Context):
        """Show the current Ouijapoke settings for this guild."""
        settings = await self._get_guild_settings(ctx.guild)
        
        role_list = []
        for pair in settings.inactivity_roles:
            role = ctx.guild.get_role(pair.role_id)
            role_name = role.name if role else f"Unknown Role ({pair.role_id})"
            role_list.append(f"  - **{role_name}**: {pair.days} days")
            
        ignored_role_names = [r.name for r in [ctx.guild.get_role(rid) for rid in settings.ignored_roles] if r]
        ignored_channel_names = [c.name for c in [ctx.guild.get_channel(cid) for cid in settings.ignored_channels] if c]
        
        target_channel = ctx.guild.get_channel(settings.target_channel_id)
        target_channel_name = target_channel.name if target_channel else "Not Set"

        msg = (
            "__**Ouijapoke Current Settings**__\n\n"
            "**Inactivity Thresholds**\n"
            f"  - **Poke After**: `{settings.poke_days}` days\n"
            f"  - **Summon After**: `{settings.summon_days}` days\n\n"
            
            "**Automatic Role Assignment**\n"
            f"  - **Roles to Assign** (Highest Inactivity to Lowest):\n{'\n'.join(role_list) or '  - None'}\n"
            f"  - **Auto-Unassign Roles on Activity**: `{settings.auto_unassign}`\n"
            f"  - **Quiet Role Assignment (No Log Messages)**: `{settings.quiet_role_assignment}`\n\n"
            
            "**Automatic Notifications (Poking/Summoning)**\n"
            f"  - **Auto-Poke/Summon Enabled**: `{settings.auto_poke_enabled}`\n"
            f"  - **Check Interval**: `{settings.auto_check_interval_hours}` hours\n"
            f"  - **Max Notifications Per Check**: `{settings.max_auto_pokes}`\n"
            f"  - **Target Channel**: `#{target_channel_name}`\n"
            f"  - **Allow Notifications in Target Channel**: `{settings.allow_pokes_in_target_channel}`\n"
            f"  - **Poke Message**: `\"...{settings.poke_message[:50]}...\"`\n"
            f"  - **Summon Message**: `\"...{settings.summon_message[:50]}...\"`\n\n"
            
            "**Ignored Items & Activity Tracking**\n"
            f"  - **Ignored Roles**: {humanize_list(ignored_role_names) or 'None'}\n"
            f"  - **Ignored Channels**: {humanize_list(ignored_channel_names) or 'None'}\n"
            f"  - **Track Non-Message Activity (Voice/Reactions)**: `{settings.track_non_message_activity}`\n"
        )
        
        await ctx.send(msg)

    @ouijaset.command(name="poke_days")
    async def ouijaset_poke_days(self, ctx: commands.Context, days: int):
        """Set the minimum number of days a member must be inactive to be eligible for a poke."""
        if days < 1:
            return await ctx.send("The number of days must be at least 1.")
        await self.config.guild(ctx.guild).poke_days.set(days)
        await ctx.send(f"Minimum inactivity for **poking** set to **{days}** days.")

    @ouijaset.command(name="summon_days")
    async def ouijaset_summon_days(self, ctx: commands.Context, days: int):
        """Set the minimum number of days a member must be inactive to be eligible for a summon."""
        if days < 1:
            return await ctx.send("The number of days must be at least 1.")
        await self.config.guild(ctx.guild).summon_days.set(days)
        await ctx.send(f"Minimum inactivity for **summoning** set to **{days}** days.")

    # --- Role Assignment Commands ---
    
    @ouijaset.group(name="inactivityrole")
    async def ouijaset_inactivityrole(self, ctx: commands.Context):
        """Manage roles automatically assigned based on inactivity."""
        pass

    @ouijaset_inactivityrole.command(name="add")
    async def ouijaset_inactivityrole_add(self, ctx: commands.Context, role: discord.Role, days: int):
        """Add a role to be assigned after a specific number of inactive days."""
        if days < 1:
            return await ctx.send("The number of days must be at least 1.")
        
        current_roles = await self.config.guild(ctx.guild).inactivity_roles()
        
        # Check for duplicates or overwrites
        for item in current_roles:
            if item["role_id"] == role.id:
                item["days"] = days
                await self.config.guild(ctx.guild).inactivity_roles.set(current_roles)
                return await ctx.send(f"Role **{role.name}** already existed. Inactivity required updated to **{days}** days.")
        
        current_roles.append({"role_id": role.id, "days": days})
        await self.config.guild(ctx.guild).inactivity_roles.set(current_roles)
        await ctx.send(f"Role **{role.name}** will be assigned after **{days}** days of inactivity.")

    @ouijaset_inactivityrole.command(name="remove")
    async def ouijaset_inactivityrole_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the automatic inactivity assignment list."""
        current_roles = await self.config.guild(ctx.guild).inactivity_roles()
        
        new_roles = [item for item in current_roles if item["role_id"] != role.id]
        
        if len(new_roles) == len(current_roles):
            return await ctx.send(f"Role **{role.name}** was not found in the inactivity role list.")

        await self.config.guild(ctx.guild).inactivity_roles.set(new_roles)
        await ctx.send(f"Role **{role.name}** removed from the automatic inactivity assignment list.")

    @ouijaset_inactivityrole.command(name="unassign")
    async def ouijaset_inactivityrole_unassign(self, ctx: commands.Context, enable: bool):
        """Toggle whether inactivity roles are automatically removed when a member becomes active."""
        await self.config.guild(ctx.guild).auto_unassign.set(enable)
        state = "enabled" if enable else "disabled"
        await ctx.send(f"Automatic removal of inactivity roles is now **{state}**.")

    @ouijaset_inactivityrole.command(name="quiet")
    async def ouijaset_inactivityrole_quiet(self, ctx: commands.Context, enable: bool):
        """Toggle quiet mode for role assignment/removal (prevents console logging)."""
        await self.config.guild(ctx.guild).quiet_role_assignment.set(enable)
        state = "enabled" if enable else "disabled"
        await ctx.send(f"Quiet mode for role assignment/removal is now **{state}**. (Log messages will{' not' if enable else ''} be sent to the console.)")

    # --- Ignore Lists ---

    @ouijaset.group(name="ignore")
    async def ouijaset_ignore(self, ctx: commands.Context):
        """Manage ignored roles and channels for activity tracking."""
        pass

    @ouijaset_ignore.command(name="role")
    async def ouijaset_ignore_role(self, ctx: commands.Context, role: discord.Role):
        """Toggle a role to be ignored for all activity tracking and assignment."""
        ignored_roles = await self.config.guild(ctx.guild).ignored_roles()
        
        if role.id in ignored_roles:
            ignored_roles.remove(role.id)
            await self.config.guild(ctx.guild).ignored_roles.set(ignored_roles)
            await ctx.send(f"Role **{role.name}** is no longer ignored for activity tracking.")
        else:
            ignored_roles.append(role.id)
            await self.config.guild(ctx.guild).ignored_roles.set(ignored_roles)
            await ctx.send(f"Role **{role.name}** is now ignored for all activity tracking and assignment.")

    @ouijaset_ignore.command(name="channel")
    async def ouijaset_ignore_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Toggle a text channel to be ignored for message activity tracking."""
        ignored_channels = await self.config.guild(ctx.guild).ignored_channels()
        
        if channel.id in ignored_channels:
            ignored_channels.remove(channel.id)
            await self.config.guild(ctx.guild).ignored_channels.set(ignored_channels)
            await ctx.send(f"Channel **#{channel.name}** is no longer ignored for message activity tracking.")
        else:
            ignored_channels.append(channel.id)
            await self.config.guild(ctx.guild).ignored_channels.set(ignored_channels)
            await ctx.send(f"Channel **#{channel.name}** is now ignored for message activity tracking.")

    # --- Notification Settings ---

    @ouijaset.command(name="target_channel")
    async def ouijaset_target_channel(self, ctx: commands.Context, channel: Union[discord.TextChannel, None] = None):
        """
        Set the channel where automated pokes/summons are sent. 
        If no channel is provided, it clears the setting.
        """
        channel_id = channel.id if channel else None
        await self.config.guild(ctx.guild).target_channel_id.set(channel_id)
        if channel_id:
            await ctx.send(f"Target channel for automated notifications set to **#{channel.name}**.")
        else:
            await ctx.send("Target channel for automated notifications cleared. Notifications will only be sent via DM (if possible).")

    @ouijaset.command(name="allow_target_poke")
    async def ouijaset_allow_target_poke(self, ctx: commands.Context, enable: bool):
        """Toggle whether pokes/summons are allowed to be sent in the target channel if DM fails."""
        await self.config.guild(ctx.guild).allow_pokes_in_target_channel.set(enable)
        state = "enabled" if enable else "disabled"
        await ctx.send(f"Sending notifications to the target channel (if DM fails) is now **{state}**.")

    @ouijaset.command(name="poke_message")
    async def ouijaset_poke_message(self, ctx: commands.Context, *, message: str):
        """Set the custom message for 'poke'. Use {mention} and {days} placeholders."""
        if not all(placeholder in message for placeholder in ["{mention}", "{days}"]):
            return await ctx.send("Your message must include both `{mention}` and `{days}` placeholders.")
        await self.config.guild(ctx.guild).poke_message.set(message)
        await ctx.send("Custom **poke message** updated.")
        
    @ouijaset.command(name="summon_message")
    async def ouijaset_summon_message(self, ctx: commands.Context, *, message: str):
        """Set the custom message for 'summon'. Use {mention} and {days} placeholders."""
        if not all(placeholder in message for placeholder in ["{mention}", "{days}"]):
            return await ctx.send("Your message must include both `{mention}` and `{days}` placeholders.")
        await self.config.guild(ctx.guild).summon_message.set(message)
        await ctx.send("Custom **summon message** updated.")

    @ouijaset.command(name="auto_poke")
    async def ouijaset_auto_poke(self, ctx: commands.Context, enable: bool):
        """Toggle the automatic daily checking for inactive members to poke/summon."""
        await self.config.guild(ctx.guild).auto_poke_enabled.set(enable)
        state = "enabled" if enable else "disabled"
        await ctx.send(f"Automatic poking/summoning is now **{state}**.")

    @ouijaset.command(name="check_interval")
    async def ouijaset_check_interval(self, ctx: commands.Context, hours: int):
        """Set how often (in hours) the bot performs the automatic check (min 1)."""
        if hours < 1:
            return await ctx.send("The interval must be at least 1 hour.")
        await self.config.guild(ctx.guild).auto_check_interval_hours.set(hours)
        await ctx.send(f"Automatic check interval set to **{hours}** hours.")
        
    @ouijaset.command(name="max_auto_pokes")
    async def ouijaset_max_auto_pokes(self, ctx: commands.Context, count: int):
        """Set the maximum number of members to poke/summon in a single automatic check (min 1)."""
        if count < 1:
            return await ctx.send("The maximum count must be at least 1.")
        await self.config.guild(ctx.guild).max_auto_pokes.set(count)
        await ctx.send(f"Maximum automatic pokes/summons per check set to **{count}**.")
        
    @ouijaset.command(name="track_non_message")
    async def ouijaset_track_non_message(self, ctx: commands.Context, enable: bool):
        """Toggle tracking of non-message activity (voice state, reactions) for last_seen."""
        await self.config.guild(ctx.guild).track_non_message_activity.set(enable)
        state = "enabled" if enable else "disabled"
        await ctx.send(f"Tracking of non-message activity (voice, reactions) is now **{state}**.")


    # --- Manual Activity Commands ---

    @commands.command(name="lastseen")
    @commands.guild_only()
    async def lastseen(self, ctx: commands.Context, member: discord.Member = None):
        """Shows the last recorded activity of a member."""
        if member is None:
            member = ctx.author
            
        last_seen = await self._get_last_seen(ctx.guild)
        
        user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
        
        days_inactive = inactivity_duration.days
        
        # Determine the last time recorded
        last_seen_ts = last_seen.get(user_id)
        if last_seen_ts is None:
            last_dt = member.joined_at.replace(tzinfo=timezone.utc)
            last_time_str = f"Since they joined the server ({last_dt.strftime('%Y-%m-%d %H:%M UTC')})"
        else:
            last_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)
            last_time_str = last_dt.strftime('%Y-%m-%d %H:%M UTC')

        await ctx.send(
            f"**{member.display_name}** was last active at: **{last_time_str}** "
            f"({days_inactive} days inactive)."
        )

    @commands.command(name="poke")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def poke(self, ctx: commands.Context, member: discord.Member):
        """Manually poke an inactive member."""
        settings = await self._get_guild_settings(ctx.guild)
        last_seen = await self._get_last_seen(ctx.guild)
        
        if member.bot:
            return await ctx.send("I can't poke bots.")
            
        user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
        days_inactive = inactivity_duration.days

        if days_inactive < settings.poke_days:
            return await ctx.send(
                f"{member.display_name} is not inactive enough yet. They need to be inactive "
                f"for at least **{settings.poke_days}** days (currently {days_inactive})."
            )

        sent = await self._send_notification(member, "poke", days_inactive, settings)
        
        if sent:
            # Update last_poked time
            last_poked = await self._get_last_poked(ctx.guild)
            last_poked[member.id] = datetime.now(timezone.utc).timestamp()
            await self._set_last_poked(ctx.guild, last_poked)
            await ctx.send(f"Successfully poked {member.mention} (inactive for {days_inactive} days).")
        else:
            await ctx.send(
                f"Failed to poke {member.mention}. They may have DMs disabled and no target "
                f"channel is set or allowed."
            )

    @commands.command(name="summon")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def summon(self, ctx: commands.Context, member: discord.Member):
        """Manually summon a highly inactive member."""
        settings = await self._get_guild_settings(ctx.guild)
        last_seen = await self._get_last_seen(ctx.guild)
        
        if member.bot:
            return await ctx.send("I can't summon bots.")
            
        user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
        days_inactive = inactivity_duration.days

        if days_inactive < settings.summon_days:
            return await ctx.send(
                f"{member.display_name} is not inactive enough for a summon. They need to be inactive "
                f"for at least **{settings.summon_days}** days (currently {days_inactive})."
            )

        sent = await self._send_notification(member, "summon", days_inactive, settings)

        if sent:
            # Update last_summoned time
            last_summoned = await self._get_last_summoned(ctx.guild)
            last_summoned[member.id] = datetime.now(timezone.utc).timestamp()
            await self._set_last_summoned(ctx.guild, last_summoned)
            await ctx.send(f"Successfully summoned {member.mention} (inactive for {days_inactive} days).")
        else:
            await ctx.send(
                f"Failed to summon {member.mention}. They may have DMs disabled and no target "
                f"channel is set or allowed."
            )
            
    # --- Debugging/Owner Commands ---

    @ouijaset.command(name="forcedcheck")
    @checks.is_owner()
    async def ouijaset_forcedcheck(self, ctx: commands.Context):
        """[BOT OWNER ONLY] Immediately runs a full inactivity check."""
        await ctx.send("Initiating forced inactivity check. This may take a moment...")
        settings = await self._get_guild_settings(ctx.guild)
        last_seen = await self._get_last_seen(ctx.guild)
        
        # 1. Process Roles
        await self._process_inactivity_roles(ctx.guild, settings, last_seen)
        
        # 2. Process Pokes/Summons
        if settings.auto_poke_enabled:
            await self._process_pokes_and_summons(ctx.guild, settings, last_seen)
            
        self.last_check_times[ctx.guild.id] = datetime.now(timezone.utc).timestamp()
        
        await ctx.send("✅ Forced check complete. Check console for role assignment logs.")


    @ouijaset.command(name="resetactivity")
    @checks.is_owner() # Only bot owner should be able to run this destructive command
    async def ouijaset_resetactivity(self, ctx: commands.Context):
        """
        [BOT OWNER ONLY] Wipes all last activity, last poked, and last summoned records for this guild. 
        
        This will effectively start activity tracking from scratch.
        """
        
        await ctx.send(
            "⚠️ **WARNING!** This command will wipe **ALL** historical activity "
            "tracking data (`last_seen`, `last_poked`, `last_summoned`) for this guild. "
            "Are you sure you want to proceed? Type `yes` to confirm."
        )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'yes'

        try:
            await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return await ctx.send("Activity reset canceled.")
        
        # Perform the reset
        await self.config.guild(ctx.guild).last_seen.set({})
        await self.config.guild(ctx.guild).last_poked.set({})
        await self.config.guild(ctx.guild).last_summoned.set({})
        
        await ctx.send(
            "✅ **Activity tracking successfully reset.** "
            "All members are now considered 'new' and tracking will start with the next message they send."
        )

    # The problematic code was found near line 728 in the context of the error:
    # File "{HOME}/.local/share/Red-DiscordBot/data/robotito/cogs/CogManager/cogs/ouijapoke/ouijapoke.py", line 728
    # )
    # ^
    # SyntaxError: f-string expression part cannot include a backslash
    
    # Original line 728, which was the closing parenthesis of the f-string:
    # f"Failed to poke {member.mention}. They may have DMs disabled and no target \nchannel is set or allowed."

    # The fix is to use str.format() or simple concatenation to break the line, 
    # since Red's command responses often allow multi-line text.
    @commands.command(name="poke")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def poke(self, ctx: commands.Context, member: discord.Member):
        """Manually poke an inactive member."""
        settings = await self._get_guild_settings(ctx.guild)
        last_seen = await self._get_last_seen(ctx.guild)
        
        if member.bot:
            return await ctx.send("I can't poke bots.")
            
        user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
        days_inactive = inactivity_duration.days

        if days_inactive < settings.poke_days:
            return await ctx.send(
                f"{member.display_name} is not inactive enough yet. They need to be inactive "
                f"for at least **{settings.poke_days}** days (currently {days_inactive})."
            )

        sent = await self._send_notification(member, "poke", days_inactive, settings)
        
        if sent:
            # Update last_poked time
            last_poked = await self._get_last_poked(ctx.guild)
            last_poked[member.id] = datetime.now(timezone.utc).timestamp()
            await self._set_last_poked(ctx.guild, last_poked)
            await ctx.send(f"Successfully poked {member.mention} (inactive for {days_inactive} days).")
        else:
            # FIX: Used str.format() instead of f-string to avoid backslash error at the line break.
            await ctx.send(
                "Failed to poke {}. They may have DMs disabled and no target \n"
                "channel is set or allowed."
            .format(member.mention)) # Original f-string was f"Failed to poke {member.mention}. They may have DMs disabled and no target \nchannel is set or allowed."


    @commands.command(name="summon")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def summon(self, ctx: commands.Context, member: discord.Member):
        """Manually summon a highly inactive member."""
        settings = await self._get_guild_settings(ctx.guild)
        last_seen = await self._get_last_seen(ctx.guild)
        
        if member.bot:
            return await ctx.send("I can't summon bots.")
            
        user_id, inactivity_duration = await self._get_inactivity_info(member, last_seen)
        days_inactive = inactivity_duration.days

        if days_inactive < settings.summon_days:
            return await ctx.send(
                f"{member.display_name} is not inactive enough for a summon. They need to be inactive "
                f"for at least **{settings.summon_days}** days (currently {days_inactive})."
            )

        sent = await self._send_notification(member, "summon", days_inactive, settings)

        if sent:
            # Update last_summoned time
            last_summoned = await self._get_last_summoned(ctx.guild)
            last_summoned[member.id] = datetime.now(timezone.utc).timestamp()
            await self._set_last_summoned(ctx.guild, last_summoned)
            await ctx.send(f"Successfully summoned {member.mention} (inactive for {days_inactive} days).")
        else:
            # FIX: Used str.format() instead of f-string to avoid backslash error at the line break.
            await ctx.send(
                "Failed to summon {}. They may have DMs disabled and no target \n"
                "channel is set or allowed."
            .format(member.mention)) # Original f-string was f"Failed to summon {member.mention}. They may have DMs disabled and no target \nchannel is set or allowed."