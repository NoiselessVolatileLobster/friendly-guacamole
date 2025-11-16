import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timedelta, timezone
import random
import re
from typing import Union, List, Tuple

# Pydantic is used for structured configuration in modern Red cogs
try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object
    Field = lambda *args, **kwargs: None

# --- Configuration Schema (Settings) ---

class OuijaSettings(BaseModel):
    """Schema for guild configuration settings."""
    poke_days: int = Field(default=30, ge=1, description="Days a member must be inactive to be eligible for a poke.")
    summon_days: int = Field(default=60, ge=1, description="Days a member must be inactive to be eligible for a summon.")
    
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
            excluded_roles=[], # [role_id, ...]
            ouija_settings=OuijaSettings().model_dump()
        )
        # In-memory tracker for voice channel connections
        self.voice_connect_times = {} # {member_id: datetime_object}

    # --- Utility Methods ---

    async def _get_settings(self, guild: discord.Guild) -> OuijaSettings:
        """Retrieves and parses the guild settings."""
        settings_data = await self.config.guild(guild).ouija_settings()
        return OuijaSettings(**settings_data)

    async def _set_settings(self, guild: discord.Guild, settings: OuijaSettings):
        """Saves the updated guild settings."""
        await self.config.guild(guild).ouija_settings.set(settings.model_dump())
    
    async def _update_last_seen(self, guild: discord.Guild, user_id: int):
        """
        Updates the last_seen time for a user in the guild config.
        """
        user_id_str = str(user_id)
        current_time_utc = datetime.now(timezone.utc).isoformat()
        
        data = await self.config.guild(guild).last_seen()
        data[user_id_str] = current_time_utc
        await self.config.guild(guild).last_seen.set(data)
        
    def _is_valid_gif_url(self, url: str) -> bool:
        """Simple check if the URL looks like a GIF link or page."""
        return re.match(r'^https?://[^\s/$.?#].[^\s]*\.(gif|webp|mp4|mov)(\?.*)?$', url, re.IGNORECASE) is not None or "tenor.com" in url or "giphy.com" in url

    def _get_inactivity_cutoff(self, days: int) -> datetime:
        """Calculates the ISO datetime cutoff point for inactivity."""
        return datetime.now(timezone.utc) - timedelta(days=days)

    def _is_excluded(self, member: discord.Member, excluded_roles: List[int]) -> bool:
        """Checks if the member has any role that is in the excluded list."""
        if not excluded_roles:
            return False
        
        member_role_ids = {role.id for role in member.roles}
        excluded_role_ids = set(excluded_roles)
        
        return bool(member_role_ids.intersection(excluded_role_ids))
    
    def _get_excluded_role_names(self, member: discord.Member, excluded_roles: List[int]) -> List[str]:
        """Returns the names of the roles that are causing the exclusion."""
        excluded_names = []
        excluded_role_ids = set(excluded_roles)
        for role in member.roles:
            if role.id in excluded_role_ids:
                excluded_names.append(role.name)
        return excluded_names

    async def _get_eligible_members(self, ctx: commands.Context, days_inactive: int, last_action_key: str) -> Tuple[List[discord.Member], List[discord.Member]]:
        """
        Gets a list of members eligible for action, prioritized by whether they have been acted upon.

        Returns: (priority_1_members, priority_2_members)
        """
        guild = ctx.guild
        cutoff_dt = self._get_inactivity_cutoff(days_inactive)
        
        # Fetch all tracking data
        data = await self.config.guild(guild).all()
        last_seen_data = data["last_seen"]
        last_action_data = data[last_action_key] # either 'last_poked' or 'last_summoned'
        excluded_roles = data["excluded_roles"]
        
        priority_1: List[discord.Member] = [] # Never poked/summoned
        priority_2: List[Tuple[discord.Member, datetime]] = [] # Poked/summoned least recently
        
        for user_id_str, last_seen_dt_str in last_seen_data.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            # Skip if member doesn't exist, is a bot, or is excluded by role
            if member is None or member.bot or self._is_excluded(member, excluded_roles):
                continue

            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            # Check if inactive enough
            if last_seen_dt < cutoff_dt:
                # Member is eligible due to inactivity
                
                # Check if member has been poked/summoned before
                last_action_dt_str = last_action_data.get(user_id_str)
                
                if last_action_dt_str is None:
                    # Priority 1: Never been acted upon
                    priority_1.append(member)
                else:
                    # Priority 2: Has been acted upon, track the date
                    try:
                        last_action_dt = datetime.fromisoformat(last_action_dt_str).replace(tzinfo=timezone.utc)
                        priority_2.append((member, last_action_dt))
                    except ValueError:
                        # Should not happen, but if date is invalid, treat as never acted upon
                        priority_1.append(member)

        # Sort Priority 2 by oldest action date first (least recently acted upon)
        # We only need the member object for the final list
        priority_2_members = [
            member for member, dt in sorted(priority_2, key=lambda x: x[1])
        ]
        
        return priority_1, priority_2_members
    
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

    async def _get_all_eligible_member_data(self, ctx: commands.Context) -> List[dict]:
        """
        Retrieves comprehensive data for all members who meet EITHER the poke or summon inactivity criteria.
        
        Does NOT include role-excluded members.
        """
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
            
            # Member must be eligible for at least one action
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

        # Sort by most inactive (highest last_seen_days)
        eligible_list.sort(key=lambda x: x['last_seen_days'], reverse=True)
        return eligible_list
    
    async def _get_excluded_eligible_members(self, ctx: commands.Context) -> List[dict]:
        """
        Retrieves data for members who are eligible by activity but excluded by role.
        """
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
            
            # Check if excluded by role
            if not self._is_excluded(member, excluded_roles):
                continue
            
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            is_poke_eligible = last_seen_dt < poke_cutoff
            is_summon_eligible = last_seen_dt < summon_cutoff
            
            # Member must be inactive enough for at least one action
            if is_poke_eligible or is_summon_eligible:
                
                last_seen_diff = (datetime.now(timezone.utc) - last_seen_dt).days
                excluded_names = self._get_excluded_role_names(member, excluded_roles)
                
                excluded_eligible_list.append({
                    "member": member,
                    "last_seen_days": last_seen_diff,
                    "eligible_for": ("Poke" if is_poke_eligible else "") + (" & Summon" if is_poke_eligible and is_summon_eligible else "Summon" if is_summon_eligible else ""),
                    "excluded_by": humanize_list([f"@{name}" for name in excluded_names])
                })

        # Sort by most inactive (highest last_seen_days)
        excluded_eligible_list.sort(key=lambda x: x['last_seen_days'], reverse=True)
        return excluded_eligible_list


    # --- Listeners (Event Handlers) ---
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Updates the last_seen time for any message sent."""
        if message.guild is None or message.author.bot or message.webhook_id:
            return
        
        await self._update_last_seen(message.guild, message.author.id)
    
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
        """Tracks voice channel connection duration and updates last_seen if > 5 minutes."""
        if member.bot:
            return
        
        member_id = member.id
        
        if after.channel is not None and before.channel != after.channel:
            if not after.self_mute and not after.self_deaf and not after.mute and not after.deaf:
                self.voice_connect_times[member_id] = datetime.now(timezone.utc)
        
        if before.channel is not None and after.channel is None:
            if member_id in self.voice_connect_times:
                join_time = self.voice_connect_times.pop(member_id)
                duration = datetime.now(timezone.utc) - join_time
                
                if duration >= timedelta(minutes=5):
                    await self._update_last_seen(member.guild, member.id)
                


    # --- Poking/Summoning Logic ---
    
    async def _send_activity_message(self, ctx: commands.Context, member: discord.Member, message_text: str, gif_list: list[str]):
        """
        Sends the message text and the GIF URL as two separate messages 
        to ensure the GIF unfurls properly.
        """
        
        final_message = message_text.replace("{user_mention}", member.mention)
        
        await ctx.send(content=final_message)
        
        if gif_list:
            gif_url = random.choice(gif_list)
            await ctx.send(content=gif_url)


    # --- User Commands ---

    @commands.group(invoke_without_command=True, aliases=["ouija"])
    async def ouijapoke(self, ctx: commands.Context):
        """
        Commands for OuijaPoke: check your status, or poke/summon inactive members.
        
        Use [p]poke or [p]summon to call these directly.
        """
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @commands.command(name="poke")
    async def poke(self, ctx: commands.Context):
        """
        Pokes a random member who has been inactive for the configured number of days.
        (Equivalent to [p]ouijapoke poke)
        """
        try:
            await self.ouijapoke_random(ctx)
        finally:
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete()
            else:
                await ctx.send("I need the `Manage Messages` permission to delete your command message.", delete_after=10)


    @commands.command(name="summon")
    async def summon(self, ctx: commands.Context):
        """
        Summons a random member who has been inactive for the configured number of days.
        (Equivalent to [p]ouijapoke summon)
        """
        try:
            await self.ouijasummon_random(ctx)
        finally:
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete()
            else:
                await ctx.send("I need the `Manage Messages` permission to delete your command message.", delete_after=10)


    @ouijapoke.command(name="check")
    async def ouijapoke_check(self, ctx: commands.Context):
        """Shows how many days it has been since you last sent a message."""
        user_id = str(ctx.author.id)
        data = await self.config.guild(ctx.guild).last_seen()
        last_seen_dt_str = data.get(user_id)

        try:
            if not last_seen_dt_str:
                return await ctx.send("I haven't recorded any activity for you yet! Say something now!")

            last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            
            difference = now_dt - last_seen_dt
            days = difference.days
            
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
        """
        Pokes a random member who has been inactive for the configured number of days, 
        prioritizing those who haven't been poked before.
        """
        async with ctx.typing():
            settings = await self._get_settings(ctx.guild)
            
            p1_members, p2_members = await self._get_eligible_members(ctx, settings.poke_days, "last_poked")
            
            member_to_poke = None
            
            if p1_members:
                member_to_poke = random.choice(p1_members)
            elif p2_members:
                member_to_poke = random.choice(p2_members)
            
            if member_to_poke is None:
                return await ctx.send(f"Everyone is active or has been recently poked! No one is eligible to be poked (needs >{settings.poke_days} days of inactivity).")

            await self._set_last_action_time(ctx.guild, member_to_poke.id, "last_poked")

            await self._send_activity_message(
                ctx,
                member_to_poke,
                settings.poke_message, 
                settings.poke_gifs,
            )
    
    @ouijapoke.command(name="summon")
    async def ouijasummon_random(self, ctx: commands.Context):
        """
        Summons a random member who has been inactive for the configured number of days,
        prioritizing those who haven't been summoned before.
        """
        async with ctx.typing():
            settings = await self._get_settings(ctx.guild)
            
            p1_members, p2_members = await self._get_eligible_members(ctx, settings.summon_days, "last_summoned")
            
            member_to_summon = None
            
            if p1_members:
                member_to_summon = random.choice(p1_members)
            elif p2_members:
                member_to_summon = random.choice(p2_members)
            
            if member_to_summon is None:
                return await ctx.send(f"The spirits are quiet! No one is eligible to be summoned (needs >{settings.summon_days} days of inactivity).")

            await self._set_last_action_time(ctx.guild, member_to_summon.id, "last_summoned")

            await self._send_activity_message(
                ctx,
                member_to_summon,
                settings.summon_message, 
                settings.summon_gifs, 
            )


    # --- Admin Commands (Settings and Overrides) ---

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def ouijaset(self, ctx: commands.Context):
        """Manages the OuijaPoke settings."""
        if ctx.invoked_subcommand is None:
            settings = await self._get_settings(ctx.guild)
            excluded_roles = await self.config.guild(ctx.guild).excluded_roles()
            excluded_names = []
            for role_id in excluded_roles:
                role = ctx.guild.get_role(role_id)
                if role:
                    excluded_names.append(role.name)

            
            msg = (
                "**OuijaPoke Settings**\n"
                f"- **Poke Inactivity:** {settings.poke_days} days\n"
                f"- **Summon Inactivity:** {settings.summon_days} days\n"
                f"- **Poke Message:** `{settings.poke_message}`\n"
                f"- **Summon Message:** `{settings.summon_message}`\n"
                f"- **Poke GIFs:** {len(settings.poke_gifs)} stored\n"
                f"- **Summon GIFs:** {len(settings.summon_gifs)} stored\n"
                f"- **Excluded Roles:** {humanize_list(excluded_names) if excluded_names else 'None'}"
            )
            await ctx.send(msg)

    # --- Days Settings ---

    @ouijaset.command(name="pokedays")
    async def ouijaset_pokedays(self, ctx: commands.Context, days: int):
        """Sets the number of days a member must be inactive to be eligible for a 'poke'."""
        if days < 1:
            return await ctx.send("Days must be 1 or greater.")
        settings = await self._get_settings(ctx.guild)
        settings.poke_days = days
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Members are now eligible to be poked after **{days}** days of inactivity.")

    @ouijaset.command(name="summondays")
    async def ouijaset_summondays(self, ctx: commands.Context, days: int):
        """Sets the number of days a member must be inactive to be eligible for a 'summon'."""
        if days < 1:
            return await ctx.send("Days must be 1 or greater.")
        settings = await self._get_settings(ctx.guild)
        settings.summon_days = days
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Members are now eligible to be summoned after **{days}** days of inactivity.")

    # --- Message Settings ---
    
    @ouijaset.command(name="pokemessage")
    async def ouijaset_pokemessage(self, ctx: commands.Context, *, message: str):
        """
        Sets the message used when a user is poked. 
        
        Use `{user_mention}` as a variable for the user mention.
        """
        if "{user_mention}" not in message:
            return await ctx.send("The message must contain `{user_mention}` to mention the inactive user.")
        settings = await self._get_settings(ctx.guild)
        settings.poke_message = message
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Poke message set to: `{message}`")
        
    @ouijaset.command(name="summonmessage")
    async def ouijaset_summonmessage(self, ctx: commands.Context, *, message: str):
        """
        Sets the message used when a user is summoned. 
        
        Use `{user_mention}` as a variable for the user mention.
        """
        if "{user_mention}" not in message:
            return await ctx.send("The message must contain `{user_mention}` to mention the inactive user.")
        settings = await self._get_settings(ctx.guild)
        settings.summon_message = message
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Summon message set to: `{message}`")


    # --- GIF Management Commands ---

    @ouijaset.group(name="pokegifs", invoke_without_command=True)
    async def ouijaset_pokegifs(self, ctx: commands.Context):
        """
        Manages the list of GIFs used for the 'poke' command.
        
        Use `[p]ouijaset pokegifs add <url>` or `[p]ouijaset pokegifs remove <url>`
        """
        settings = await self._get_settings(ctx.guild)
        gifs = settings.poke_gifs
        if not gifs:
            msg = "There are currently no Poke GIFs configured."
        else:
            gif_list = "\n".join(f"`{i+1}.` <{g}>" for i, g in enumerate(gifs))
            msg = f"**Current Poke GIFs ({len(gifs)} total):**\n{gif_list}"
        
        await ctx.send(msg)

    @ouijaset_pokegifs.command(name="add")
    async def pokegifs_add(self, ctx: commands.Context, url: str):
        """Adds a new GIF URL to the poke list."""
        if not self._is_valid_gif_url(url):
            return await ctx.send("That doesn't look like a valid URL. Please ensure it's a link to an image/GIF, or a Tenor/GIPHY page link.")
        
        settings = await self._get_settings(ctx.guild)
        if url in settings.poke_gifs:
            return await ctx.send("That GIF is already in the list.")
        
        settings.poke_gifs.append(url)
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Added new Poke GIF: <{url}>")

    @ouijaset_pokegifs.command(name="remove")
    async def pokegifs_remove(self, ctx: commands.Context, url: str):
        """Removes a GIF URL from the poke list."""
        settings = await self._get_settings(ctx.guild)
        
        try:
            settings.poke_gifs.remove(url)
            await self._set_settings(ctx.guild, settings)
            await ctx.send(f"Removed Poke GIF: <{url}>")
        except ValueError:
            await ctx.send("That GIF URL was not found in the list.")


    @ouijaset.group(name="summongifs", invoke_without_command=True)
    async def ouijaset_summongifs(self, ctx: commands.Context):
        """
        Manages the list of GIFs used for the 'summon' command.
        
        Use `[p]ouijaset summongifs add <url>` or `[p]ouijaset summongifs remove <url>`
        """
        settings = await self._get_settings(ctx.guild)
        gifs = settings.summon_gifs
        if not gifs:
            msg = "There are currently no Summon GIFs configured."
        else:
            gif_list = "\n".join(f"`{i+1}.` <{g}>" for i, g in enumerate(gifs))
            msg = f"**Current Summon GIFs ({len(gifs)} total):**\n{gif_list}"
        
        await ctx.send(msg)

    @ouijaset_summongifs.command(name="add")
    async def summongifs_add(self, ctx: commands.Context, url: str):
        """Adds a new GIF URL to the summon list."""
        if not self._is_valid_gif_url(url):
            return await ctx.send("That doesn't look like a valid URL. Please ensure it's a link to an image/GIF, or a Tenor/GIPHY page link.")
        
        settings = await self._get_settings(ctx.guild)
        if url in settings.summon_gifs:
            return await ctx.send("That GIF is already in the list.")
        
        settings.summon_gifs.append(url)
        await self._set_settings(ctx.guild, settings)
        await ctx.send(f"Added new Summon GIF: <{url}>")

    @ouijaset_summongifs.command(name="remove")
    async def summongifs_remove(self, ctx: commands.Context, url: str):
        """Removes a GIF URL from the summon list."""
        settings = await self._get_settings(ctx.guild)
        
        try:
            settings.summon_gifs.remove(url)
            await self._set_settings(ctx.guild, settings)
            await ctx.send(f"Removed Summon GIF: <{url}>")
        except ValueError:
            await ctx.send("That GIF URL was not found in the list.")

    # --- Eligible Members Display ---

    @ouijaset.command(name="eligible")
    async def ouijaset_eligible(self, ctx: commands.Context):
        """Displays a list of all members currently eligible for being poked/summoned OR excluded from being so."""
        
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

        # 2. Handle excluded members list
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
                    title=f"🛡️ Excluded Eligible Members ({len(excluded_eligible_members)} Total)",
                    description=f"Members below are inactive enough, but **EXCLUDED** due to role:\n\n{content}",
                    color=discord.Color.orange()
                )
                embed.set_footer(text=f"Page {page_num + 1}/{len(excluded_pages)} (Excluded) | Total Excluded: {len(excluded_eligible_members)}")
                await ctx.send(embed=embed)
        elif eligible_members:
             # Only send this message if we sent the first embed, to keep the output clean
             await ctx.send("✅ No members are currently excluded by role who would otherwise be eligible for action.")

        elif not eligible_members and not excluded_eligible_members:
            # If nothing was eligible *at all*, no message is needed here.
            pass


    # --- Excluded Roles Management ---

    @ouijaset.group(name="excludedroles", aliases=["exclrole"], invoke_without_command=True)
    async def ouijaset_excludedroles(self, ctx: commands.Context):
        """
        Manages roles whose members are permanently excluded from being poked or summoned.
        """
        excluded_roles = await self.config.guild(ctx.guild).excluded_roles()
        
        if not excluded_roles:
            return await ctx.send("No roles are currently excluded from poking/summoning.")
        
        role_names = []
        for role_id in excluded_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                role_names.append(role.name)
                
        await ctx.send(
            f"The following roles are **excluded** (members are ineligible):\n"
            f"{humanize_list(role_names)}"
        )

    @ouijaset_excludedroles.command(name="add")
    async def excludedroles_add(self, ctx: commands.Context, role: discord.Role):
        """Adds a role to the exclusion list."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded_roles:
            if role.id in excluded_roles:
                return await ctx.send(f"The role **{role.name}** is already excluded.")
            excluded_roles.append(role.id)
        
        await ctx.send(f"Added role **{role.name}** to the excluded list. Members with this role will no longer be poked or summoned.")

    @ouijaset_excludedroles.command(name="remove")
    async def excludedroles_remove(self, ctx: commands.Context, role: discord.Role):
        """Removes a role from the exclusion list."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded_roles:
            if role.id not in excluded_roles:
                return await ctx.send(f"The role **{role.name}** was not found in the excluded list.")
            excluded_roles.remove(role.id)
            
        await ctx.send(f"Removed role **{role.name}** from the excluded list. Members with this role may now be poked or summoned if they meet the inactivity criteria.")


    # --- Last Seen Override Command ---

    @ouijaset.command(name="override")
    async def ouijaset_override(self, ctx: commands.Context, role: discord.Role, days_ago: int):
        """
        Overrides the last active date for all members of a given role.

        Example: `[p]ouijaset override @Spirits 60` 
        Sets everyone with the @Spirits role to last active 60 days ago.
        """
        if days_ago < 0:
            return await ctx.send("The number of days must be 0 or greater.")
        
        async with ctx.typing():
            
            target_last_active_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
            target_last_active_dt_str = target_last_active_dt.isoformat()
            
            last_seen_data = await self.config.guild(ctx.guild).last_seen()
            
            updated_count = 0
            
            for member in role.members:
                if member.bot:
                    continue
                
                last_seen_data[str(member.id)] = target_last_active_dt_str
                updated_count += 1
                
            await self.config.guild(ctx.guild).last_seen.set(last_seen_data)
        
        await ctx.send(
            f"The Ouija spirits have whispered that **{updated_count}** members "
            f"in the **{role.name}** role were last seen **{days_ago} days ago** "
            f"({target_last_active_dt.strftime('%Y-%m-%d %H:%M:%S UTC')})."
        )
        
    # --- Reset Activity Command ---
    
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
        except TimeoutError:
            return await ctx.send("Activity reset canceled.")
        
        # Perform the reset
        await self.config.guild(ctx.guild).last_seen.set({})
        await self.config.guild(ctx.guild).last_poked.set({})
        await self.config.guild(ctx.guild).last_summoned.set({})
        
        await ctx.send(
            "✅ **Activity tracking successfully reset.** "
            "All members are now considered 'new' and tracking will start with the next message they send."
        )

# --- Red Setup Function ---

async def setup(bot):
    await bot.add_cog(OuijaPoke(bot))