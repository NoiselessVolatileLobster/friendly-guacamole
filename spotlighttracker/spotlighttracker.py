import discord
import logging
import asyncio
import time
import os
from datetime import datetime
from typing import Optional

from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import humanize_timedelta

__author__ = ["NoiselessVolatileLobster"]

log = logging.getLogger("red.NoiselessVolatileLobster.SpotlightTracker")

class SpotlightTracker(commands.Cog):
    """
    Track voice channel participation for D&D sessions.
    
    Monitors 'Unmuted' time as a proxy for speaking time to help DMs 
    balance the spotlight among players.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=84737281923, force_registration=True)
        
        default_guild = {
            "enabled": False,
            "session_active": False,
            "monitored_channel": None,
            "output_path": None,
            "ignored_users": [],
            "session_start_time": 0
        }
        
        default_member = {
            "total_unmuted": 0,
            "last_action": 0,
            "last_unmute_start": 0 
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        # Cache for live session data
        # {guild_id: {user_id: {data}}}
        self.session_cache = {}
        
        # Cache for the active dashboard message
        # {guild_id: {"channel_id": int, "message_id": int}}
        self.active_dashboards = {}
        
        self.file_task = self.bot.loop.create_task(self.file_update_loop())
        self.dash_task = self.bot.loop.create_task(self.dashboard_refresh_loop())

    async def cog_unload(self):
        if self.file_task:
            self.file_task.cancel()
        if self.dash_task:
            self.dash_task.cancel()

    # ---------------------------------------------------------------------
    # Events
    # ---------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        guild_id = member.guild.id
        if not await self.config.guild(member.guild).session_active():
            return

        monitored_channel = await self.config.guild(member.guild).monitored_channel()
        
        if guild_id not in self.session_cache:
            self.session_cache[guild_id] = {}

        now = time.time()
        
        # Check if user is in the monitored channel
        in_channel = after.channel and after.channel.id == monitored_channel
        was_in_channel = before.channel and before.channel.id == monitored_channel

        ignored = await self.config.guild(member.guild).ignored_users()
        if member.id in ignored:
            return

        if member.id not in self.session_cache[guild_id]:
            self.session_cache[guild_id][member.id] = {
                "total_unmuted": 0,
                "last_action": now,
                "is_muted": True, 
                "last_unmute_start": 0
            }

        user_data = self.session_cache[guild_id][member.id]

        # 1. User Joined Channel
        if in_channel and not was_in_channel:
            user_data["last_action"] = now
            if not after.self_mute and not after.mute:
                user_data["is_muted"] = False
                user_data["last_unmute_start"] = now
            else:
                user_data["is_muted"] = True

        # 2. User Left Channel
        elif not in_channel and was_in_channel:
            if not user_data["is_muted"]:
                duration = now - user_data["last_unmute_start"]
                user_data["total_unmuted"] += duration
            
            user_data["is_muted"] = True
            user_data["last_action"] = now

        # 3. User Changed State (Mute/Unmute) within Channel
        elif in_channel:
            is_now_muted = after.self_mute or after.mute
            was_muted = before.self_mute or before.mute

            if is_now_muted != was_muted:
                user_data["last_action"] = now
                
                if is_now_muted:
                    if not user_data["is_muted"]:
                        duration = now - user_data["last_unmute_start"]
                        user_data["total_unmuted"] += duration
                    user_data["is_muted"] = True
                else:
                    user_data["is_muted"] = False
                    user_data["last_unmute_start"] = now

    # ---------------------------------------------------------------------
    # Loops
    # ---------------------------------------------------------------------

    async def file_update_loop(self):
        """Updates the local text file for StreamDeck integration (Every 5s)."""
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog("SpotlightTracker"):
            try:
                for guild_id, members in self.session_cache.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild: continue
                        
                    path = await self.config.guild(guild).output_path()
                    if not path: continue

                    # Use helper to get stats, but we need simplified text for file
                    stats_list = self._calculate_stats(guild)
                    if not stats_list: continue

                    quietest = stats_list[-1]["name"] if stats_list else "None" # List is sorted Descending
                    loudest = stats_list[0]["name"] if stats_list else "None"

                    output_text = f"Quiet: {quietest} | Loud: {loudest}"

                    def write_file():
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(output_text)
                    
                    await self.bot.loop.run_in_executor(None, write_file)

            except Exception as e:
                log.error(f"Error in file update loop: {e}")
            
            await asyncio.sleep(5)

    async def dashboard_refresh_loop(self):
        """Updates the Discord embed dashboard (Every 120s)."""
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog("SpotlightTracker"):
            try:
                # Iterate over a copy of keys to avoid modification issues
                current_dashboards = list(self.active_dashboards.items())
                
                for guild_id, msg_info in current_dashboards:
                    guild = self.bot.get_guild(guild_id)
                    if not guild: continue
                    
                    # Verify session is still active
                    if not await self.config.guild(guild).session_active():
                        del self.active_dashboards[guild_id]
                        continue

                    channel = guild.get_channel(msg_info["channel_id"])
                    if not channel: continue

                    try:
                        message = await channel.fetch_message(msg_info["message_id"])
                        new_embed = self._get_dashboard_embed(guild)
                        if new_embed:
                            await message.edit(embed=new_embed)
                    except discord.NotFound:
                        # Message deleted manually, stop tracking it
                        del self.active_dashboards[guild_id]
                    except discord.Forbidden:
                        del self.active_dashboards[guild_id]
                    except Exception as e:
                        log.error(f"Error updating dashboard for guild {guild.name}: {e}")

            except Exception as e:
                log.error(f"Error in dashboard main loop: {e}")
                
            await asyncio.sleep(120) # 2 minutes

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    def _calculate_stats(self, guild):
        """Returns a sorted list of user stats."""
        data = self.session_cache.get(guild.id, {})
        if not data:
            return []

        now = time.time()
        stats_list = []

        for uid, user_stats in data.items():
            member = guild.get_member(uid)
            if not member:
                continue

            total_seconds = user_stats["total_unmuted"]
            if not user_stats["is_muted"]:
                total_seconds += (now - user_stats["last_unmute_start"])

            if not user_stats["is_muted"]:
                last_active_str = "**Active Now**"
            else:
                last_active_seconds = now - user_stats["last_action"]
                last_active_str = humanize_timedelta(seconds=last_active_seconds) + " ago"

            total_str = humanize_timedelta(seconds=total_seconds)
            if not total_str: total_str = "0s"

            stats_list.append({
                "name": member.display_name,
                "total": total_seconds,
                "total_str": total_str,
                "last_active": last_active_str,
                "status": "🟢" if not user_stats["is_muted"] else "🔴"
            })
            
        # Sort by Total Time (Descending)
        stats_list.sort(key=lambda x: x["total"], reverse=True)
        return stats_list

    def _get_dashboard_embed(self, guild):
        stats_list = self._calculate_stats(guild)
        if not stats_list:
            return None

        embed = discord.Embed(title="🎙️ Spotlight Dashboard (Live)", color=discord.Color.blue())
        desc = f"Last Updated: <t:{int(time.time())}:R>\n\n"
        
        for stat in stats_list:
            desc += f"{stat['status']} **{stat['name']}**\n"
            desc += f"└ Time Unmuted: `{stat['total_str']}`\n"
            desc += f"└ Last Event: {stat['last_active']}\n\n"
        
        embed.description = desc
        embed.set_footer(text="Updates every 2 mins | 🟢 = Unmuted | 🔴 = Muted")
        return embed

    # ---------------------------------------------------------------------
    # Commands
    # ---------------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    async def spotlight(self, ctx):
        """Tools for tracking player activity in D&D sessions."""
        pass

    @spotlight.command(name="start")
    @commands.admin_or_permissions(manage_channels=True)
    async def spotlight_start(self, ctx):
        """Start a new tracking session."""
        channel = ctx.author.voice.channel if ctx.author.voice else None
        if not channel:
            return await ctx.send("You must be in a voice channel to start a session.")

        # Clear data
        self.session_cache[ctx.guild.id] = {}
        if ctx.guild.id in self.active_dashboards:
            del self.active_dashboards[ctx.guild.id]
        
        # Populate initial cache
        now = time.time()
        ignored = await self.config.guild(ctx.guild).ignored_users()
        
        for member in channel.members:
            if member.bot or member.id in ignored:
                continue
            
            is_muted = member.voice.self_mute or member.voice.mute
            self.session_cache[ctx.guild.id][member.id] = {
                "total_unmuted": 0,
                "last_action": now,
                "is_muted": is_muted,
                "last_unmute_start": now if not is_muted else 0
            }

        await self.config.guild(ctx.guild).session_active.set(True)
        await self.config.guild(ctx.guild).monitored_channel.set(channel.id)
        await self.config.guild(ctx.guild).session_start_time.set(now)

        await ctx.send(f"🎙️ **Spotlight Session Started**\nTracking activity in: {channel.mention}")

    @spotlight.command(name="stop")
    @commands.admin_or_permissions(manage_channels=True)
    async def spotlight_stop(self, ctx):
        """Stop the current tracking session."""
        await self.config.guild(ctx.guild).session_active.set(False)
        self.session_cache.pop(ctx.guild.id, None)
        self.active_dashboards.pop(ctx.guild.id, None) # Stop auto-updating
        await ctx.send("🛑 **Spotlight Session Ended**")

    @spotlight.command(name="dashboard", aliases=["view", "stats"])
    async def spotlight_dashboard(self, ctx):
        """
        View the current spotlight statistics.
        
        This message will auto-update every 2 minutes.
        """
        if not await self.config.guild(ctx.guild).session_active():
            return await ctx.send("No session is currently active. Start one with `[p]spotlight start`.")

        embed = self._get_dashboard_embed(ctx.guild)
        if not embed:
            return await ctx.send("No data collected yet (or everyone is ignored).")

        msg = await ctx.send(embed=embed)
        
        # Register this message for auto-updates
        self.active_dashboards[ctx.guild.id] = {
            "channel_id": ctx.channel.id,
            "message_id": msg.id
        }

    # ---------------------------------------------------------------------
    # Admin / Settings
    # ---------------------------------------------------------------------

    @commands.group(name="spotlightset")
    @commands.admin_or_permissions(administrator=True)
    async def spotlightset(self, ctx):
        """Configuration for SpotlightTracker."""
        pass

    @spotlightset.command(name="file")
    async def spotlightset_file(self, ctx, path: str = None):
        """
        Set the local file path for StreamDeck output.
        
        Leave empty to disable.
        Example: `[p]spotlightset file C:/Users/MyName/Desktop/spotlight.txt`
        """
        if path:
            await self.config.guild(ctx.guild).output_path.set(path)
            await ctx.send(f"✅ Output file set to: `{path}`")
        else:
            await self.config.guild(ctx.guild).output_path.set(None)
            await ctx.send("✅ Output file disabled.")

    @spotlightset.command(name="ignore")
    async def spotlightset_ignore(self, ctx, member: discord.Member):
        """Toggle ignoring a user (e.g. the DM) from stats."""
        async with self.config.guild(ctx.guild).ignored_users() as ignored:
            if member.id in ignored:
                ignored.remove(member.id)
                await ctx.send(f"Now tracking {member.display_name}.")
            else:
                ignored.append(member.id)
                await ctx.send(f"Now ignoring {member.display_name}.")

    @spotlightset.command(name="view")
    async def spotlightset_view(self, ctx):
        """View all configured settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        ignored_list = settings['ignored_users']
        ignored_names = []
        for uid in ignored_list:
            m = ctx.guild.get_member(uid)
            if m:
                ignored_names.append(m.display_name)
            else:
                ignored_names.append(str(uid))
        
        ignored_str = ", ".join(ignored_names) if ignored_names else "None"
        file_path = settings['output_path'] if settings['output_path'] else "Disabled"
        session_status = "Active" if settings['session_active'] else "Inactive"

        msg = (
            f"**SpotlightTracker Settings**\n"
            f"```ini\n"
            f"[ Session Status ]    :  {session_status}\n"
            f"[ Output File Path ]  :  {file_path}\n"
            f"[ Ignored Users ]     :  {ignored_str}\n"
            f"```"
        )
        await ctx.send(msg)

async def setup(bot):
    await bot.add_cog(SpotlightTracker(bot))