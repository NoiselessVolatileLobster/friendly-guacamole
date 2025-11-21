import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timedelta, timezone
import random
import re
from typing import Union, List, Tuple
from asyncio import TimeoutError

# Pydantic is used for structured configuration in modern Red cogs
try:
    from pydantic import BaseModel, Field
except ImportError:
    # Define simple mock classes if pydantic is not available
    class BaseModel:
        pass
    def Field(*args, **kwargs):
        return None

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
    
    # Store user IDs who are exempted from tracking
    exempted_users: list[int] = Field(default=[], description="List of user IDs exempt from activity tracking.")

# --- The Cog ---

class Ouijapoke(commands.Cog):
    """
    Keep track of member activity and poke/summon inactive users.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=20240417, force_registration=True)
        self.config.register_guild(
            last_seen={}, # {user_id: timestamp}
            last_poked={}, # {user_id: timestamp}
            last_summoned={}, # {user_id: timestamp}
            settings=OuijaSettings().model_dump()
        )

    # --- Utility Functions ---

    def _get_guild_settings(self, guild: discord.Guild) -> OuijaSettings:
        """Retrieves and validates guild settings."""
        settings_data = self.config.guild(guild).settings()
        return OuijaSettings.model_validate(settings_data)

    def _time_delta_to_friendly_string(self, td: timedelta) -> str:
        """Converts a timedelta object into a friendly string (e.g., '3 days, 5 hours')."""
        seconds = int(td.total_seconds())
        
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days > 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0 and not days: # Only show minutes if less than 1 day
            parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
        
        return ", ".join(parts) if parts else "just now"

    # --- Listeners ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Update last_seen timestamp on message."""
        if message.guild is None or message.author.bot:
            return

        settings = await self.config.guild(message.guild).settings()
        exempted_users = settings.get("exempted_users", [])

        if message.author.id in exempted_users:
            return

        now = datetime.now(timezone.utc).timestamp()
        
        async with self.config.guild(message.guild).last_seen() as last_seen_data:
            last_seen_data[str(message.author.id)] = now

    # --- Core Commands (Poke and Summon) ---

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="poke")
    async def poke_user(self, ctx: commands.Context, member: discord.Member):
        """Poke an inactive member to encourage them to return."""
        
        if member.bot:
            return await ctx.send("The Ouija Board cannot poke a bot. They are already spirits.")

        settings_data = await self.config.guild(ctx.guild).settings()
        settings = OuijaSettings.model_validate(settings_data)
        exempted_users = settings.exempted_users

        if member.id in exempted_users:
            return await ctx.send(f"{member.mention} is exempt from being poked or tracked.")

        last_seen_data = await self.config.guild(ctx.guild).last_seen()
        last_seen_ts = last_seen_data.get(str(member.id))

        if not last_seen_ts:
            return await ctx.send(f"Cannot poke {member.mention}. Last active date is unknown (no messages recorded).")

        now = datetime.now(timezone.utc)
        last_seen_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)
        time_since_last_seen = now - last_seen_dt
        
        poke_timedelta = timedelta(days=settings.poke_days)

        if time_since_last_seen < poke_timedelta:
            time_left = poke_timedelta - time_since_last_seen
            friendly_time_left = self._time_delta_to_friendly_string(time_left)
            return await ctx.send(
                f"{member.mention} is too active! They need to be inactive for "
                f"at least **{settings.poke_days} days** to be eligible for a poke. "
                f"Time remaining: **{friendly_time_left}**."
            )

        # Send the poke message
        poke_message = settings.poke_message.replace("{user_mention}", member.mention)
        
        embed = discord.Embed(
            title="👻 A Gentle Poke from the Beyond 👻",
            description=poke_message,
            color=discord.Color.gold()
        )
        
        if settings.poke_gifs:
            embed.set_image(url=random.choice(settings.poke_gifs))

        await ctx.send(embed=embed)
        
        # Update last_poked timestamp
        async with self.config.guild(ctx.guild).last_poked() as last_poked_data:
            last_poked_data[str(member.id)] = now.timestamp()


    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="summon")
    async def summon_user(self, ctx: commands.Context, member: discord.Member):
        """Summon a very inactive member to return."""
        
        if member.bot:
            return await ctx.send("The Ouija Board cannot summon a bot. They are already spirits.")

        settings_data = await self.config.guild(ctx.guild).settings()
        settings = OuijaSettings.model_validate(settings_data)
        exempted_users = settings.exempted_users

        if member.id in exempted_users:
            return await ctx.send(f"{member.mention} is exempt from being summoned or tracked.")

        last_seen_data = await self.config.guild(ctx.guild).last_seen()
        last_seen_ts = last_seen_data.get(str(member.id))

        if not last_seen_ts:
            return await ctx.send(f"Cannot summon {member.mention}. Last active date is unknown (no messages recorded).")

        now = datetime.now(timezone.utc)
        last_seen_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)
        time_since_last_seen = now - last_seen_dt
        
        summon_timedelta = timedelta(days=settings.summon_days)

        if time_since_last_seen < summon_timedelta:
            time_left = summon_timedelta - time_since_last_seen
            friendly_time_left = self._time_delta_to_friendly_string(time_left)
            return await ctx.send(
                f"{member.mention} is not inactive enough for a full ritual! "
                f"They need to be inactive for at least **{settings.summon_days} days** to be eligible for a summon. "
                f"Time remaining: **{friendly_time_left}**."
            )

        # Send the summon message
        summon_message = settings.summon_message.replace("{user_mention}", member.mention)
        
        embed = discord.Embed(
            title="😈 The Grand Summoning Ritual is Underway! 😈",
            description=summon_message,
            color=discord.Color.dark_red()
        )
        
        if settings.summon_gifs:
            embed.set_image(url=random.choice(settings.summon_gifs))

        await ctx.send(embed=embed)
        
        # Update last_summoned timestamp
        async with self.config.guild(ctx.guild).last_summoned() as last_summoned_data:
            last_summoned_data[str(member.id)] = now.timestamp()


    # --- Settings Group ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ouijaset(self, ctx: commands.Context):
        """Configure Ouijapoke settings."""
        pass

    @ouijaset.command(name="pokecutoff")
    async def ouijaset_pokecutoff(self, ctx: commands.Context, days: int):
        """Set the number of days a member must be inactive to be eligible for a poke."""
        if days < 1:
            return await ctx.send("The number of days must be at least 1.")
        
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["poke_days"] = days
        
        await ctx.send(f"Poke eligibility cutoff set to **{days} days** of inactivity.")

    @ouijaset.command(name="summoncutoff")
    async def ouijaset_summoncutoff(self, ctx: commands.Context, days: int):
        """Set the number of days a member must be inactive to be eligible for a summon."""
        if days < 1:
            return await ctx.send("The number of days must be at least 1.")
        
        async with self.config.guild(ctx.guild).settings() as settings:
            settings["summon_days"] = days
        
        await ctx.send(f"Summon eligibility cutoff set to **{days} days** of inactivity.")
        
    @ouijaset.command(name="exemptadd")
    async def ouijaset_exemptadd(self, ctx: commands.Context, member: discord.Member):
        """Add a member to the exemption list, preventing them from being tracked, poked, or summoned."""
        if member.bot:
            return await ctx.send("Bots are automatically exempt from tracking.")
            
        async with self.config.guild(ctx.guild).settings() as settings:
            if member.id not in settings["exempted_users"]:
                settings["exempted_users"].append(member.id)
                await ctx.send(f"{member.mention} has been added to the exemption list.")
            else:
                await ctx.send(f"{member.mention} is already on the exemption list.")

    @ouijaset.command(name="exemptremove")
    async def ouijaset_exemptremove(self, ctx: commands.Context, member: discord.Member):
        """Remove a member from the exemption list."""
        async with self.config.guild(ctx.guild).settings() as settings:
            if member.id in settings["exempted_users"]:
                settings["exempted_users"].remove(member.id)
                await ctx.send(f"{member.mention} has been removed from the exemption list.")
            else:
                await ctx.send(f"{member.mention} was not found on the exemption list.")

    @ouijaset.command(name="status")
    async def ouijaset_status(self, ctx: commands.Context):
        """
        Displays a status report of members' last activity, poked, and summoned dates.

        This helps visualize who is currently eligible for poking/summoning.
        """
        await ctx.defer() # Acknowledge the command for potentially long operation

        settings_data = await self.config.guild(ctx.guild).settings()
        settings = OuijaSettings.model_validate(settings_data)
        poke_days = settings.poke_days
        summon_days = settings.summon_days
        exempted_users = settings.exempted_users
        
        last_seen_data = await self.config.guild(ctx.guild).last_seen()
        last_poked_data = await self.config.guild(ctx.guild).last_poked()
        last_summoned_data = await self.config.guild(ctx.guild).last_summoned()
        
        now = datetime.now(timezone.utc)
        
        # Get all members and sort them by time since last seen (oldest first)
        all_member_ids = set(last_seen_data.keys())
        # Filter for members currently in the guild, are not bots, and are not exempted
        members = [
            m for m in ctx.guild.members 
            if not m.bot and m.id not in exempted_users
        ]

        # Determine which members to display: those with activity or those who were poked/summoned
        display_members = []
        for member in members:
            user_id = str(member.id)
            # Must have been seen, OR have been poked, OR have been summoned
            if user_id in all_member_ids or user_id in last_poked_data or user_id in last_summoned_data:
                display_members.append(member)

        # Sort the display members by last seen date (oldest first)
        def sort_key(member):
            ts = last_seen_data.get(str(member.id))
            # Put unknown dates (ts=None) at the end by assigning a very high timestamp
            return ts if ts is not None else float('inf')

        sorted_members = sorted(display_members, key=sort_key)

        status_lines = []
        
        for member in sorted_members:
            user_id = str(member.id)
            last_seen_ts = last_seen_data.get(user_id)
            last_poked_ts = last_poked_data.get(user_id)
            last_summoned_ts = last_summoned_data.get(user_id)

            # --- Last Seen / Activity Status ---
            if last_seen_ts:
                last_seen_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)
                time_since_last_seen = now - last_seen_dt
                
                # Format time string
                last_seen_str = self._time_delta_to_friendly_string(time_since_last_seen)

                # Determine activity icon
                if time_since_last_seen > timedelta(days=summon_days):
                    seen_icon = "🔴"  # Eligible for summon
                elif time_since_last_seen > timedelta(days=poke_days):
                    seen_icon = "🟠"  # Eligible for poke
                else:
                    seen_icon = "🟢"  # Active
            else:
                last_seen_str = "Unknown"
                seen_icon = "❓" # Changed from 👻 to ❓

            # --- Last Poked Status ---
            if last_poked_ts:
                last_poked_dt = datetime.fromtimestamp(last_poked_ts, tz=timezone.utc)
                time_since_last_poked = now - last_poked_dt
                poked_str = f"📋 {self._time_delta_to_friendly_string(time_since_last_poked)} ago"
            else:
                poked_str = "📋 Never"
            
            # --- Last Summoned Status ---
            if last_summoned_ts:
                last_summoned_dt = datetime.fromtimestamp(last_summoned_ts, tz=timezone.utc)
                time_since_last_summoned = now - last_summoned_dt
                summoned_str = f"🔮 {self._time_delta_to_friendly_string(time_since_last_summoned)} ago"
            else:
                summoned_str = "🔮 Never"

            # Combine line
            status_lines.append(
                f"{seen_icon} **{member.display_name}**: Last seen {last_seen_str} ({poked_str} | {summoned_str})"
            )

        # Prepare and send embed
        embed = discord.Embed(
            title=f"Ouijapoke Activity Status for {ctx.guild.name}",
            color=discord.Color.blue()
        )

        embed.description = (
            f"**Configuration:**\n"
            f"Poke Eligibility: **{poke_days} days** of inactivity\n"
            f"Summon Eligibility: **{summon_days} days** of inactivity\n\n"
            "**Activity Status Legend:**\n"
            "🟢: Active (Last seen < Poke Days)\n"
            "🟠: Eligible for Poke (Last seen between Poke and Summon Days)\n"
            "🔴: Eligible for Summon (Last seen > Summon Days)\n"
            "❓: Last active date unknown\n" # Changed from 👻 to ❓
            "📋: Date Last Poked\n"
            "🔮: Date Last Summoned\n\n"
        )
        
        # Split output into pages if necessary
        page_size = 20
        pages = [status_lines[i:i + page_size] for i in range(0, len(status_lines), page_size)]

        if not pages:
            embed.add_field(name="No Members Tracked", value="No non-bot, non-exempt members have sent messages yet, or all tracked members are currently active.", inline=False)
            return await ctx.send(embed=embed)

        for i, page in enumerate(pages):
            page_content = "\n".join(page)
            embed.add_field(
                name=f"Member Activity Report (Page {i+1}/{len(pages)})", 
                value=page_content, 
                inline=False
            )
            
        await ctx.send(embed=embed)


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