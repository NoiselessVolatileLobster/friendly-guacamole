import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
import random
import logging
from typing import List, Dict, Optional, Tuple

# Pydantic fallback for Red 3.5.22 compatibility
try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:
        def model_dump(self): return self.__dict__
        def __init__(self, **data):
            for key, value in data.items(): setattr(self, key, value)
    def Field(default, **kwargs): return default

log = logging.getLogger("red.ouijapoke")

class OuijaSettings(BaseModel):
    poke_days: int = Field(default=30)
    summon_days: int = Field(default=60)
    warn_days: int = Field(default=90)
    kick_days: int = Field(default=120)
    warn_exempt_roles: List[int] = Field(default=[])
    kick_exempt_roles: List[int] = Field(default=[])
    required_messages: int = Field(default=1)
    required_window_hours: float = Field(default=0)
    min_message_length: int = Field(default=0)
    poke_message: str = Field(default="Hey {user_mention}, the spirits feel your presence. Come say hello!")
    summon_message: str = Field(default="**{user_mention}**! The spirits demand your return!")
    poke_gifs: list[str] = Field(default=[])
    summon_gifs: list[str] = Field(default=[])
    auto_channel_id: Optional[int] = Field(default=None)

class OuijaPoke(commands.Cog):
    """Tracks activity with a strict Level 1 -> Level 3 WarnSystem escalation path."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=148000552390, force_registration=True)
        self.config.register_guild(
            last_seen={},
            last_poked={},
            last_summoned={},
            ws_state={}, # {user_id: "warned" | "kicked" | "none"}
            excluded_roles=[], 
            excluded_channels=[],
            ouija_settings=OuijaSettings().model_dump(),
            next_auto_event=None,
        )
        self.recent_activity_cache: Dict[int, List[datetime]] = {}
        self.auto_poke_loop.start()

    def cog_unload(self):
        self.auto_poke_loop.cancel()

    async def _get_settings(self, guild: discord.Guild) -> OuijaSettings:
        data = await self.config.guild(guild).ouija_settings()
        return OuijaSettings(**data)

    async def _set_settings(self, guild: discord.Guild, settings: OuijaSettings):
        await self.config.guild(guild).ouija_settings.set(settings.model_dump())

    # --- Task Loop Logic ---

    @tasks.loop(minutes=5)
    async def auto_poke_loop(self):
        for guild in self.bot.guilds:
            try:
                settings = await self._get_settings(guild)
                next_run_str = await self.config.guild(guild).next_auto_event()
                now = datetime.now(timezone.utc)
                
                if not next_run_str or now >= datetime.fromisoformat(next_run_str).replace(tzinfo=timezone.utc):
                    await self._process_warnsystem_logic(guild, settings)
                    if settings.auto_channel_id:
                        channel = guild.get_channel(settings.auto_channel_id)
                        if channel: await self._run_daily_lottery(guild, channel, settings)
                    
                    next_run = now + timedelta(hours=24) + timedelta(seconds=random.randint(-7200, 7200))
                    await self.config.guild(guild).next_auto_event.set(next_run.isoformat())
            except Exception as e:
                log.error(f"Error in loop for {guild.id}: {e}")

    async def _process_warnsystem_logic(self, guild: discord.Guild, settings: OuijaSettings):
        ws = self.bot.get_cog("WarnSystem")
        if not ws: return

        data = await self.config.guild(guild).all()
        last_seen = data["last_seen"]
        ws_state = data["ws_state"]
        now = datetime.now(timezone.utc)

        for uid_str, ts in last_seen.items():
            member = guild.get_member(int(uid_str))
            if not member or member.bot: continue

            inactive_days = (now - datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)).days
            current_state = ws_state.get(uid_str, "none")

            # Level 3 (Kick) Check
            if inactive_days >= settings.kick_days and current_state != "kicked":
                if not any(r.id in settings.kick_exempt_roles for r in member.roles):
                    await ws.api.warn(guild, [member], guild.me, 3, f"Inactivity Kick: {inactive_days} days.")
                    async with self.config.guild(guild).ws_state() as state: state[uid_str] = "kicked"

            # Level 1 (Warn) Check - Only triggers if we haven't warned/kicked them yet
            elif inactive_days >= settings.warn_days and current_state == "none":
                if not any(r.id in settings.warn_exempt_roles for r in member.roles):
                    await ws.api.warn(guild, [member], guild.me, 1, f"Inactivity Warning: {inactive_days} days.")
                    async with self.config.guild(guild).ws_state() as state: state[uid_str] = "warned"

    async def _run_daily_lottery(self, guild: discord.Guild, channel: discord.TextChannel, settings: OuijaSettings):
        roll = random.random()
        if roll < 0.10: # Summon
            target = await self._get_eligible(guild, settings.summon_days, "last_summoned")
            if target:
                await self._send_msg(channel, target, settings.summon_message, settings.summon_gifs)
                async with self.config.guild(guild).last_summoned() as ls: ls[str(target.id)] = datetime.now(timezone.utc).isoformat()
        elif roll < 0.20: # Poke
            target = await self._get_eligible(guild, settings.poke_days, "last_poked")
            if target:
                await self._send_msg(channel, target, settings.poke_message, settings.poke_gifs)
                async with self.config.guild(guild).last_poked() as lp: lp[str(target.id)] = datetime.now(timezone.utc).isoformat()

    # --- Admin Commands ---

    @commands.group(name="ouijapokeset")
    @checks.admin_or_permissions(manage_guild=True)
    async def ouijapokeset(self, ctx: commands.Context):
        """Manage OuijaPoke and WarnSystem settings."""
        pass

    @ouijapokeset.command(name="view")
    async def ouijapokeset_view(self, ctx: commands.Context):
        """Displays all configured settings for OuijaPoke."""
        settings = await self._get_settings(ctx.guild)
        embed = discord.Embed(title="🔮 OuijaPoke Config", color=0x7289da)
        
        embed.add_field(name="🕒 Thresholds", value=(
            f"**Poke:** {settings.poke_days}d | **Summon:** {settings.summon_days}d\n"
            f"**WS Warn (L1):** {settings.warn_days}d\n"
            f"**WS Kick (L3):** {settings.kick_days}d"
        ), inline=False)

        warn_ex = [f"<@&{r}>" for r in settings.warn_exempt_roles if ctx.guild.get_role(r)]
        kick_ex = [f"<@&{r}>" for r in settings.kick_exempt_roles if ctx.guild.get_role(r)]
        embed.add_field(name="🚫 Exemptions", value=f"**Warn:** {humanize_list(warn_ex) or 'None'}\n**Kick:** {humanize_list(kick_ex) or 'None'}", inline=False)
        
        await ctx.send(embed=embed)

    # --- Listeners & Helpers ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot: return
        settings = await self._get_settings(message.guild)
        if len(message.content) < settings.min_message_length: return

        uid = message.author.id
        now = datetime.now(timezone.utc)
        if uid not in self.recent_activity_cache: self.recent_activity_cache[uid] = []
        self.recent_activity_cache[uid].append(now)
        
        cutoff = now - timedelta(hours=settings.required_window_hours)
        self.recent_activity_cache[uid] = [t for t in self.recent_activity_cache[uid] if t > cutoff]

        if len(self.recent_activity_cache[uid]) >= settings.required_messages:
            async with self.config.guild(message.guild).last_seen() as ls: ls[str(uid)] = now.isoformat()
            # Reset WarnSystem tracking upon return to activity
            async with self.config.guild(message.guild).ws_state() as state: state.pop(str(uid), None)

    async def _get_eligible(self, guild, days, action_key) -> Optional[discord.Member]:
        data = await self.config.guild(guild).all()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        
        candidates = []
        for uid_str, ts in data["last_seen"].items():
            member = guild.get_member(int(uid_str))
            if not member or member.bot or any(r.id in data["excluded_roles"] for r in member.roles): continue
            
            # Must be inactive long enough
            if datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) > cutoff: continue
            
            # Spam check: Haven't been poked/summoned in 14 days
            last_act = data[action_key].get(uid_str)
            if last_act and datetime.fromisoformat(last_act).replace(tzinfo=timezone.utc) > recent_cutoff: continue
            
            candidates.append(member)
        
        return random.choice(candidates) if candidates else None

    async def _send_msg(self, chan, member, text, gifs):
        await chan.send(text.replace("{user_mention}", member.mention))
        if gifs: await chan.send(random.choice(gifs))

async def setup(bot):
    await bot.add_cog(OuijaPoke(bot))