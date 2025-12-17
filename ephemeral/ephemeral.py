import asyncio
import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.chat_formatting import humanize_list, box, bold, pagify
from datetime import datetime, timedelta
import typing

def timedelta_to_human(td: timedelta) -> str:
    """Converts a timedelta object to a human-readable string (Red's style)."""
    total_seconds = int(td.total_seconds())
    if total_seconds == 0:
        return "0 seconds"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if hours > 0: parts.append(f"{hours} hours")
    if minutes > 0: parts.append(f"{minutes} minutes")
    # Only show up to two parts for brevity, and seconds only if less than an hour
    if seconds > 0 and hours == 0 and minutes == 0: parts.append(f"{seconds} seconds")

    if len(parts) > 2:
        return humanize_list(parts[:2])
    return humanize_list(parts)


# Main Cog Class
class Ephemeral(commands.Cog):
    """
    Manages temporary roles and message counting for users entering 'Ephemeral Mode' 
    by typing a configurable phrase in a specific channel, provided they have a required role.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1480084004, force_registration=True)
        self.config.register_guild(
            ephemeral_expire_threshold=timedelta(hours=8).total_seconds(),
            messages_threshold=10,
            message_length_threshold=10,
            ephemeral_role_id=None,
            ephemeral_expire_role_id=None,
            
            # --- CONFIGURATION FOR ACTIVATION SCOPE ---
            activation_phrase="let me in", 
            ephemeral_timer_channel_id=None,
            ephemeral_not_started_role_id=None,
            ephemeral_read_rules_role_id=None,
            # --- END CONFIGURATION ---

            # --- START MESSAGE CONFIGURATION (CONDITIONAL) ---
            start_message_first_channel_id=None,
            start_message_first_content="{mention} has joined for the first time!",
            
            start_message_returning_channel_id=None,
            start_message_returning_content="Welcome back {mention}!",
            # --- END START MESSAGE CONFIGURATION ---
            
            # --- WELCOME EMBED CONFIGURATION ---
            welcome_embed_channel_id=None,
            welcome_embed_title="Welcome to the Server!",
            welcome_embed_description="Welcome {mention}! Please read the rules.",
            # --- END WELCOME EMBED CONFIGURATION ---

            # --- FAREWELL EMBED CONFIGURATION ---
            farewell_embed_channel_id=None,
            farewell_embed_title="Goodbye!",
            farewell_embed_description="{mention} has left the server.",
            # --- END FAREWELL EMBED CONFIGURATION ---
            
            nomessages_threshold=timedelta(hours=4).total_seconds(), 
            nomessages_role_id=None,
            nomessages_failed_message_channel_id=None,
            nomessages_failed_message="👻 {mention} has failed Ephemeral mode (No Messages) and has been assigned the No Messages role.",
            
            first_greeting_threshold=timedelta(hours=3).total_seconds(),
            first_greeting_channel_id=None,
            first_greeting_message="It looks like you haven't sent enough messages yet in {time_passed}. {mention}",
            
            second_greeting_threshold=timedelta(hours=5).total_seconds(),
            second_greeting_channel_id=None,
            second_greeting_message="You've been in Ephemeral mode for {time_passed}. Please continue interacting! {mention}",
            
            expire_message_channel_id=None,
            expire_message="⚠️ {mention} has had their Ephemeral Timer Expire and has been assigned the Expired role.",
            
            # --- SUCCESS MESSAGE CONFIGURATION (EMBED) ---
            success_message_channel_id=None,
            success_embed_data={
                "title": "Success!",
                "description": "{mention} is no longer in Ephemeral mode! 🎉",
                "image_url": "none",
                "footer": "Welcome to the server!"
            },
            # --- END SUCCESS MESSAGE CONFIGURATION ---
            
            # --- WARNING SYSTEM CONFIGURATION ---
            warn_nomessages={
                "enabled": False,
                "action": "warn",
                "reason": "Ephemeral Mode Failure: No messages sent within threshold."
            },
            warn_expire={
                "enabled": False,
                "action": "warn",
                "reason": "Ephemeral Mode Failure: Timer expired before message count met."
            },
            warn_second_greeting={
                "enabled": False,
                "action": "warn",
                "reason": "Ephemeral Mode Warning: Reached second greeting threshold."
            },
            warn_timernotstarted={
                "enabled": False,
                "action": "warn",
                "time": 0, # seconds
                "reason": "Automatic action: User joined and did not continue onboarding process"
            },
            # --- END WARNING SYSTEM CONFIGURATION ---

            log_channel_id=None,
        )
        self.config.register_member(
            is_ephemeral=False,
            start_time=None,
            message_count=0,
            first_greeting_sent=False,
            second_greeting_sent=False,
        )
        self.timers = {}
        self.join_timers = {} # Track users who haven't started yet
        
        self.bg_task = self.bot.loop.create_task(self._init_timers(), name="ephemeral_init")
        
    def cog_unload(self):
        if self.bg_task:
            self.bg_task.cancel()
        for task in self.timers.values():
            task.cancel()
        for task in self.join_timers.values():
            task.cancel()
        
    async def _init_timers(self):
        await self.bot.wait_until_ready()
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            
            # 1. Initialize Active Ephemeral Timers
            # Note: Checking all_members on very large guilds can be expensive.
            for member_id, data in (await self.config.all_members(guild)).items():
                if data["is_ephemeral"] and data["start_time"]:
                    self.start_user_timer(guild_id, member_id)

            # 2. Initialize "Not Started" Timers
            settings = await self.config.guild(guild).all()
            not_started_role_id = settings.get("ephemeral_not_started_role_id")
            if not_started_role_id:
                not_started_role = guild.get_role(not_started_role_id)
                if not_started_role:
                    for member in guild.members:
                        if not_started_role in member.roles:
                            m_data = await self.config.member(member).all()
                            if not m_data["is_ephemeral"]:
                                self.start_join_timer(guild.id, member.id)

    def start_user_timer(self, guild_id: int, user_id: int):
        task_key = (guild_id, user_id)
        if task_key in self.timers:
            self.timers[task_key].cancel()

        print(f"Ephemeral DEBUG: Timer task CREATED for User {user_id} in Guild {guild_id}")
        task = self.bot.loop.create_task(
            self.check_ephemeral_status(guild_id, user_id), 
            name=f"ephemeral_timer_{guild_id}_{user_id}"
        )
        self.timers[task_key] = task
    
    def stop_user_timer(self, guild_id: int, user_id: int):
        task = self.timers.pop((guild_id, user_id), None)
        if task and not task.done():
            task.cancel()

    # --- Join Timer Management ---
    def start_join_timer(self, guild_id: int, user_id: int):
        task_key = (guild_id, user_id)
        if task_key in self.join_timers:
            self.join_timers[task_key].cancel()

        task = self.bot.loop.create_task(
            self.check_join_status(guild_id, user_id),
            name=f"ephemeral_join_timer_{guild_id}_{user_id}"
        )
        self.join_timers[task_key] = task

    def stop_join_timer(self, guild_id: int, user_id: int):
        task = self.join_timers.pop((guild_id, user_id), None)
        if task and not task.done():
            task.cancel()

    async def _log_event(self, guild: discord.Guild, message: str):
        """Logs an event to the configured log channel."""
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return

        channel = guild.get_channel(log_channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                print(f"Ephemeral ERROR: Cannot send log to {channel.name} ({channel.id})")
            except Exception as e:
                print(f"Ephemeral ERROR logging event: {e}")

    async def _log_ephemeral_message(self, message: discord.Message, settings: dict):
        """Logs the content of an ephemeral message before deletion."""
        log_channel_id = settings["log_channel_id"]
        if not log_channel_id:
            return

        channel = message.guild.get_channel(log_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        content = message.content[:1000] 
        embed = discord.Embed(
            description=box(content, lang="log"),
            color=discord.Color.blue()
        )
        embed.set_author(name=f"💬 EPHEMERAL MESSAGE from {message.author.display_name}", icon_url=message.author.display_avatar.url)
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.set_footer(text=f"Sent at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            print(f"Ephemeral ERROR: Cannot send log message to {channel.name}.")
        except Exception as e:
            print(f"Ephemeral ERROR logging message: {e}")

    async def _send_custom_message(self, guild: discord.Guild, user: discord.Member, channel_id: typing.Optional[int], message: str, time_passed: typing.Optional[timedelta] = None):
        if not channel_id:
            return
            
        channel = guild.get_channel(channel_id)
        
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        formatted_message = message.replace("{mention}", user.mention).replace("{username}", user.name)
        if time_passed is not None:
            formatted_message = formatted_message.replace("{time_passed}", timedelta_to_human(time_passed))

        try:
            await channel.send(
                formatted_message, 
                allowed_mentions=discord.AllowedMentions(roles=True, users=True)
            )
        except discord.Forbidden:
            print(f"Ephemeral ERROR in Guild {guild.id}: Missing permissions in {channel.name}.")
        except Exception as e:
            print(f"Ephemeral ERROR in Guild {guild.id}: Unexpected error: {e}")

    async def _perform_automated_action(self, guild: discord.Guild, user: discord.Member, config_key: str):
        """Executes an automated action (Warn, Kick, Ban) via WarnSystem integration."""
        settings = await self.config.guild(guild).all()
        action_config = settings.get(config_key)
        
        if not action_config or not action_config.get("enabled", False):
            return

        action = action_config.get("action", "warn").lower()
        reason = action_config.get("reason", "Automated Ephemeral Action")
        
        warn_system = self.bot.get_cog("WarnSystem")
        
        if not warn_system:
            print(f"Ephemeral WARNING: WarnSystem cog not found. Cannot perform automated action '{action}' for {user.id}.")
            return

        # Explicitly fetch the bot member to be the "author" of the warning
        author = self.bot.user
        if not author:
            # Fallback if self.bot.user isn't available for some reason (rare)
            author = guild.me

        try:
            # 1. WARN ACTION
            if action == "warn":
                await warn_system.api.warn(user, author, reason)
                await self._log_event(guild, f"⚠️ **Warned:** {user.mention} (`{user.id}`) - Reason: {reason}")
            
            # 2. KICK ACTION
            elif action == "kick":
                # Warn FIRST, then kick. This ensures the warning is logged/DM'd before they leave.
                try: 
                    await warn_system.api.warn(user, author, f"[Auto-Kick] {reason}")
                except Exception as e:
                    print(f"Ephemeral: Failed to log warn before kick: {e}")

                await guild.kick(user, reason=reason)
                await self._log_event(guild, f"👢 **Kicked:** {user.mention} (`{user.id}`) - Reason: {reason}")
            
            # 3. BAN ACTION
            elif action == "ban":
                # Warn FIRST, then ban.
                try: 
                    await warn_system.api.warn(user, author, f"[Auto-Ban] {reason}")
                except Exception as e:
                    print(f"Ephemeral: Failed to log warn before ban: {e}")

                await guild.ban(user, reason=reason)
                await self._log_event(guild, f"🔨 **Banned:** {user.mention} (`{user.id}`) - Reason: {reason}")

        except discord.Forbidden:
            await self._log_event(guild, f"❌ **Error:** Failed to perform action '{action}' on {user.mention}. Missing Permissions.")
        except Exception as e:
            print(f"Ephemeral ERROR performing action '{action}' on {user.id}: {e}")

    async def _send_success_embed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        """Sends the Success (formerly Removed) embed."""
        channel_id = settings["success_message_channel_id"]
        embed_data = settings["success_embed_data"]
        
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        title = embed_data.get("title", "Success").replace("{username}", user.name).replace("{mention}", user.display_name)
        description = embed_data.get("description", "").replace("{mention}", user.mention).replace("{username}", user.name)
        footer_text = embed_data.get("footer", "").replace("{username}", user.name)
        image_url = embed_data.get("image_url", "none")

        embed = discord.Embed(title=title, description=description, color=discord.Color.green())
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if image_url and image_url.lower() != "none":
            embed.set_image(url=image_url)
        if footer_text:
            embed.set_footer(text=footer_text)

        try:
            await channel.send(content=user.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.Forbidden:
            print(f"Ephemeral ERROR in Guild {guild.id}: Missing permissions to send Success Embed in {channel.name}.")
        except Exception as e:
            print(f"Ephemeral ERROR in Guild {guild.id}: Unexpected error sending Success Embed: {e}")

    async def _send_welcome_embed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        """Sends the Welcome embed when a user starts the timer."""
        channel_id = settings.get("welcome_embed_channel_id")
        
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        title = settings.get("welcome_embed_title", "Welcome!").replace("{username}", user.name).replace("{mention}", user.display_name)
        description = settings.get("welcome_embed_description", "").replace("{mention}", user.mention).replace("{username}", user.name)
        footer_text = f"User ID: {user.id}"
        
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=footer_text)

        try:
            await channel.send(content=user.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.Forbidden:
            print(f"Ephemeral ERROR: Missing permissions to send Welcome Embed.")
        except Exception as e:
            print(f"Ephemeral ERROR: Unexpected error sending Welcome Embed: {e}")

    async def _send_farewell_embed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        """Sends the Farewell embed when a user leaves (if qualified)."""
        channel_id = settings.get("farewell_embed_channel_id")
        
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        # Replace tags, but use display name/name for mention to avoid ghost pings/broken links
        title = settings.get("farewell_embed_title", "Goodbye!").replace("{username}", user.name).replace("{mention}", user.display_name)
        description = settings.get("farewell_embed_description", "").replace("{mention}", user.display_name).replace("{username}", user.name)
        footer_text = f"User ID: {user.id}"
        
        embed = discord.Embed(title=title, description=description, color=discord.Color.red())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=footer_text)

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"Ephemeral ERROR in Guild {guild.id}: Missing permissions to send Farewell Embed in {channel.name}.")
        except Exception as e:
            print(f"Ephemeral ERROR in Guild {guild.id}: Unexpected error sending Farewell Embed: {e}")

    async def _handle_ephemeral_success(self, guild: discord.Guild, user: discord.Member, settings: dict, manual: bool = False):
        """Handles the success case: message threshold met."""
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])

        # 1. Remove Ephemeral Role (MANDATORY)
        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral message threshold met.")
            except discord.Forbidden:
                print(f"Ephemeral ERROR: Forbidden to remove ephemeral role for {user.id} on success.")
        
        # 2. Remove Not Started Role (OPTIONAL, if they still have it)
        if not_started_role and not_started_role in user.roles:
            try:
                await user.remove_roles(not_started_role, reason="Ephemeral mode successfully completed.")
            except discord.Forbidden:
                print(f"Ephemeral ERROR: Forbidden to remove not started role for {user.id} on success.")

        self.stop_user_timer(guild.id, user.id)
        self.stop_join_timer(guild.id, user.id) 
        await self.config.member(user).clear()
        
        await self._send_success_embed(guild, user, settings)

        log_msg = f"✅ **Success:** {user.mention} (`{user.id}`) met the message threshold and is no longer Ephemeral."
        if manual:
            log_msg = f"⭐ **Manual Success:** {user.mention} (`{user.id}`) was manually removed from Ephemeral mode."

        await self._log_event(guild, log_msg)

    async def _handle_activation(self, message: discord.Message, settings: dict, guild: discord.Guild, user: discord.Member):
        """Handles the activation phrase being typed."""
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])
        read_rules_role = guild.get_role(settings["ephemeral_read_rules_role_id"])
        
        if not ephemeral_role or not not_started_role:
            try: await message.delete() 
            except: pass
            await message.channel.send(f"{user.mention}, Ephemeral Mode is not fully configured. Please notify an admin.", delete_after=10)
            return

        try:
            await message.delete()
        except discord.Forbidden:
            print(f"Ephemeral CRITICAL ERROR: Cannot delete activation message by {user.id}.")
            return
        except discord.NotFound:
            pass
            
        try:
            self.stop_join_timer(guild.id, user.id)
            self.stop_user_timer(guild.id, user.id)
            
            await user.remove_roles(not_started_role, reason="Started Ephemeral mode via phrase (Role removed).")
            await user.add_roles(ephemeral_role, reason="Has Started Ephemeral Timer")
            
            if read_rules_role:
                try: await user.add_roles(read_rules_role, reason="Has Read Rules")
                except discord.Forbidden: print(f"Ephemeral ERROR: Forbidden to add Read Rules role for {user.id}")

            now = datetime.now().timestamp()
            await self.config.member(user).set({
                "start_time": now,
                "message_count": 0,
                "is_ephemeral": True,
            })
            
            start_channel_id = settings.get("start_message_first_channel_id")
            start_message_content = settings.get("start_message_first_content")
            
            join_tracker = self.bot.get_cog("JoinTracker")
            if join_tracker:
                try:
                    count = await join_tracker.get_join_count(guild, user.id)
                    if count > 1:
                        start_channel_id = settings.get("start_message_returning_channel_id")
                        start_message_content = settings.get("start_message_returning_content")
                except Exception as e:
                    print(f"Ephemeral ERROR: Could not fetch join count: {e}")
            
            await self._send_custom_message(guild, user, start_channel_id, start_message_content)
            await self._send_welcome_embed(guild, user, settings)
            
            self.start_user_timer(guild.id, user.id)
            await self._log_event(guild, f"▶️ **Started:** {user.mention} (`{user.id}`) typed the phrase and started their timer.")

        except discord.Forbidden:
            print(f"Ephemeral ERROR: Forbidden to manage roles for {user.id}")
            await message.channel.send(f"{user.mention}, I lack permissions to manage roles.", delete_after=10)
        except Exception as e:
            print(f"Ephemeral ERROR during activation for {user.id}: {e}")

    async def _handle_nomessages_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        nomessages_role = guild.get_role(settings["nomessages_role_id"])

        if ephemeral_role and ephemeral_role in user.roles:
            try: await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: No Messages sent.")
            except: pass

        if nomessages_role:
            try: await user.add_roles(nomessages_role, reason="Ephemeral Failed: No Messages sent.")
            except: pass

        await self._send_custom_message(guild, user, settings["nomessages_failed_message_channel_id"], settings["nomessages_failed_message"])
        await self._log_event(guild, f"👻 **No messages:** {user.mention} (`{user.id}`) did not send any messages and received the No Messages role.")
        await self._perform_automated_action(guild, user, "warn_nomessages")

        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    async def _handle_ephemeral_expire(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        expire_role = guild.get_role(settings["ephemeral_expire_role_id"])

        if ephemeral_role and ephemeral_role in user.roles:
            try: await user.remove_roles(ephemeral_role, reason="Ephemeral Timer Expired.")
            except: pass

        if expire_role:
            try: await user.add_roles(expire_role, reason="Ephemeral Timer Expired.")
            except: pass

        await self._send_custom_message(guild, user, settings["expire_message_channel_id"], settings["expire_message"])
        await self._log_event(guild, f"❌ **Expired:** {user.mention} (`{user.id}`) timer expired and received the expired role.")
        await self._perform_automated_action(guild, user, "warn_expire")

        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    async def check_ephemeral_status(self, guild_id: int, user_id: int):
        print(f"Ephemeral DEBUG: Timer task STARTED execution for User {user_id}")
        await asyncio.sleep(10)

        while True:
            await asyncio.sleep(10)
            guild = self.bot.get_guild(guild_id)
            user = guild.get_member(user_id)
            
            if not guild or not user:
                self.stop_user_timer(guild_id, user_id)
                return

            member_data = await self.config.member(user).all()
            if not member_data["is_ephemeral"]:
                self.stop_user_timer(guild_id, user_id)
                return

            settings = await self.config.guild(guild).all()
            
            expire_threshold = timedelta(seconds=settings["ephemeral_expire_threshold"])
            nomessages_threshold = timedelta(seconds=settings["nomessages_threshold"])
            second_greeting_threshold = timedelta(seconds=settings["second_greeting_threshold"])
            first_greeting_threshold = timedelta(seconds=settings["first_greeting_threshold"])
            
            start_time = datetime.fromtimestamp(member_data["start_time"])
            time_passed: timedelta = datetime.now() - start_time

            print(f"Ephemeral DEBUG: Checking {user.id}. Time: {time_passed.total_seconds():.0f}s. Msgs: {member_data['message_count']}.")

            if time_passed >= nomessages_threshold and member_data["message_count"] == 0:
                print(f"Ephemeral DEBUG: NO MESSAGES FAILED trigger for {user.id}")
                await self._handle_nomessages_failed(guild, user, settings)
                return

            if time_passed >= expire_threshold:
                print(f"Ephemeral DEBUG: EXPIRE trigger for {user.id}")
                await self._handle_ephemeral_expire(guild, user, settings)
                return

            elif time_passed >= second_greeting_threshold and not member_data["second_greeting_sent"]:
                await self._send_custom_message(guild, user, settings["second_greeting_channel_id"], settings["second_greeting_message"], time_passed)
                await self.config.member(user).second_greeting_sent.set(True)
                await self._log_event(guild, f"🕑 **Second Greeting:** Sent to {user.mention} (`{user.id}`).")
                await self._perform_automated_action(guild, user, "warn_second_greeting")

            elif time_passed >= first_greeting_threshold and not member_data["first_greeting_sent"]:
                await self._send_custom_message(guild, user, settings["first_greeting_channel_id"], settings["first_greeting_message"], time_passed)
                await self.config.member(user).first_greeting_sent.set(True)
                await self._log_event(guild, f"🕐 **First Greeting:** Sent to {user.mention} (`{user.id}`).")

    async def check_join_status(self, guild_id: int, user_id: int):
        """Background task to check if a user has stayed in 'Not Started' state too long."""
        await asyncio.sleep(60) # Initial wait

        while True:
            await asyncio.sleep(60) # Check every minute
            
            guild = self.bot.get_guild(guild_id)
            user = guild.get_member(user_id)
            
            if not guild or not user:
                self.stop_join_timer(guild_id, user_id)
                return
            
            settings = await self.config.guild(guild).all()
            not_started_role_id = settings.get("ephemeral_not_started_role_id")
            not_started_config = settings.get("warn_timernotstarted", {})
            
            # If feature disabled or role not set, exit
            if not not_started_role_id or not not_started_config.get("enabled"):
                self.stop_join_timer(guild_id, user_id)
                return
                
            not_started_role = guild.get_role(not_started_role_id)
            
            # If user no longer has the role (or role deleted), stop timer
            if not not_started_role or not_started_role not in user.roles:
                self.stop_join_timer(guild_id, user_id)
                return
            
            # Check elapsed time since join
            # Fallback to now if joined_at is missing (shouldn't happen for active members)
            join_time = user.joined_at or datetime.utcnow()
            # Convert to naive UTC if needed
            if join_time.tzinfo:
                join_time = join_time.replace(tzinfo=None)
            
            time_passed = datetime.utcnow() - join_time
            threshold_seconds = not_started_config.get("time", 0)
            
            if time_passed.total_seconds() >= threshold_seconds:
                # Trigger action
                print(f"Ephemeral DEBUG: Timer Not Started EXPIRED for {user.id}")
                await self._perform_automated_action(guild, user, "warn_timernotstarted")
                self.stop_join_timer(guild_id, user_id)
                return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        member = message.author
        guild = message.guild
        settings = await self.config.guild(guild).all()
        member_data = await self.config.member(member).all()
        is_ephemeral = member_data["is_ephemeral"]

        # Phase 3: Counting
        if is_ephemeral:
            msg_len = len(message.content)
            threshold = settings["message_length_threshold"]
            print(f"Ephemeral DEBUG: Msg from {member.id}. Len: {msg_len} vs Thresh: {threshold}. In Channel: {message.channel.id}")
            
            if msg_len >= threshold:
                new_count = member_data["message_count"] + 1
                await self.config.member(member).message_count.set(new_count)
                print(f"Ephemeral DEBUG: Count incremented for {member.id} -> {new_count}")
                if new_count >= settings["messages_threshold"]:
                    print(f"Ephemeral DEBUG: Threshold met for {member.id}!")
                    await self._handle_ephemeral_success(guild, member, settings)
            
            timer_channel_id = settings.get("ephemeral_timer_channel_id")
            if timer_channel_id and message.channel.id == timer_channel_id:
                await self._log_ephemeral_message(message, settings)
                try: await message.delete()
                except: pass
            return 
        
        # Phases 1 & 2: Activation
        timer_channel_id = settings.get("ephemeral_timer_channel_id")
        if not timer_channel_id or message.channel.id != timer_channel_id:
            return 

        # Staff Check: If staff doesn't have the role, ignore them
        if await self.bot.is_owner(member) or await self.bot.is_admin(member) or await self.bot.is_mod(member):
            return

        not_started_role_id = settings.get("ephemeral_not_started_role_id")
        if not not_started_role_id: return 
        
        not_started_role = guild.get_role(not_started_role_id)
        
        # Self-Correction: If user is in the channel but missing the role, add it.
        if not_started_role and not_started_role not in member.roles:
            try:
                await member.add_roles(not_started_role, reason="Ephemeral: Correcting missing Not Started role.")
                self.start_join_timer(guild.id, member.id) 
            except discord.Forbidden:
                print(f"Ephemeral ERROR: Cannot assign 'Not Started' role to {member.id} in on_message.")
                return

        # Double check role presence
        if not not_started_role or not (not_started_role in member.roles): return

        activation_phrase = settings.get("activation_phrase", "let me in")
        if message.content.lower().strip() == activation_phrase.lower().strip():
            await self._handle_activation(message, settings, guild, member)
            return

        # Deletion for non-activation messages in timer channel
        await self._log_ephemeral_message(message, settings)
        try:
            await message.delete()
            await self._log_event(guild, f"🗑️ **Deleted:** Message from {member.mention} deleted in activation channel.")
        except: pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member join: Ghost ping and start Not Started timer."""
        guild = member.guild
        settings = await self.config.guild(guild).all()
        
        # Give 'Not Started' Role if configured
        not_started_role_id = settings.get("ephemeral_not_started_role_id")
        if not_started_role_id:
            role = guild.get_role(not_started_role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Ephemeral: New user joined.")
                except discord.Forbidden:
                    print(f"Ephemeral ERROR: Cannot assign 'Not Started' role to {member.id}.")

        # Start the join timer check
        self.start_join_timer(guild.id, member.id)
        
        timer_channel_id = settings.get("ephemeral_timer_channel_id")
        if not timer_channel_id: return
        channel = guild.get_channel(timer_channel_id)
        if not channel: return
        try:
            msg = await channel.send(member.mention)
            await msg.delete(delay=120)
        except: pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        
        # --- CLEANUP: Reset all ephemeral stats for this user ---
        # Stop any running timers
        self.stop_user_timer(guild.id, member.id)
        self.stop_join_timer(guild.id, member.id)
        # Clear database config for this member
        await self.config.member(member).clear()
        
        settings = await self.config.guild(guild).all()
        
        # Check if farewell is configured
        channel_id = settings.get("farewell_embed_channel_id")
        if not channel_id:
            return

        # Check for Not Started Role
        not_started_role_id = settings.get("ephemeral_not_started_role_id")
        if not_started_role_id:
            role = guild.get_role(not_started_role_id)
            # If user has the "Not Started" role, it means they left without activating. Skip embed.
            if role and role in member.roles:
                return
        
        # If we are here, send the embed
        await self._send_farewell_embed(guild, member, settings)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralstatus(self, ctx: commands.Context):
        """Shows all users currently in Ephemeral mode."""
        guild = ctx.guild
        settings = await self.config.guild(guild).all()
        all_member_data = await self.config.all_members(guild)
        ephemeral_members = []

        for member_id, data in (await self.config.all_members(guild)).items():
            if data["is_ephemeral"] and data["start_time"] is not None:
                member = guild.get_member(member_id)
                if member: ephemeral_members.append((member, data))
        
        if not ephemeral_members:
            return await ctx.send("No users are currently in Ephemeral mode.")

        MAX_FIELDS_PER_PAGE = 10
        expire_threshold = timedelta(seconds=settings["ephemeral_expire_threshold"])
        pages = []
        current_embed = None
        
        for i, (member, data) in enumerate(ephemeral_members):
            if i % MAX_FIELDS_PER_PAGE == 0:
                if current_embed: pages.append(current_embed)
                current_embed = discord.Embed(title="👻 Ephemeral Mode Status", color=await ctx.embed_color())
                
            start_time = datetime.fromtimestamp(data["start_time"])
            time_remaining = (start_time + expire_threshold) - datetime.now()
            if time_remaining.total_seconds() < 0: time_remaining = timedelta(seconds=0)

            val = (f"**User:** {member.mention} (`{member.id}`)\n"
                   f"**Progress:** {data['message_count']}/{settings['messages_threshold']}\n"
                   f"**Expires:** {timedelta_to_human(time_remaining)}")
            current_embed.add_field(name=f"{member.display_name}", value=val, inline=False)

        if current_embed: pages.append(current_embed)
        for idx, embed in enumerate(pages): embed.set_footer(text=f"Page {idx + 1}/{len(pages)}")
        await menu(ctx, pages, DEFAULT_CONTROLS)

    @commands.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset(self, ctx: commands.Context):
        """Configures the Ephemeral cog settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
            
    @ephemeralset.command(name="success")
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset_success(self, ctx: commands.Context, user: discord.Member):
        """Manually marks a user as having succeeded Ephemeral mode."""
        member_data = await self.config.member(user).all()
        if not member_data["is_ephemeral"]: return await ctx.send(f"{user.mention} is not in Ephemeral mode.")
        settings = await self.config.guild(ctx.guild).all()
        await self._handle_ephemeral_success(ctx.guild, user, settings, manual=True)
        await ctx.send(f"✅ Marked {user.mention} as succeeded.")

    @ephemeralset.command(name="phrase")
    async def ephemeralset_phrase(self, ctx: commands.Context, *, phrase: str):
        """Sets the activation phrase."""
        await self.config.guild(ctx.guild).activation_phrase.set(phrase)
        await ctx.send(f"Activation phrase set to: `{phrase}`")

    @ephemeralset.command(name="startmessage")
    async def ephemeralset_startmessage(self, ctx: commands.Context, msg_type: str, channel: discord.TextChannel, *, message: str):
        """Sets the start message. msg_type: firsttime, notfirsttime."""
        msg_type = msg_type.lower()
        if msg_type in ["firsttime", "first"]:
            await self.config.guild(ctx.guild).start_message_first_channel_id.set(channel.id)
            await self.config.guild(ctx.guild).start_message_first_content.set(message)
            await ctx.send(f"First Time start message set for {channel.mention}.")
        elif msg_type in ["notfirsttime", "returning"]:
            await self.config.guild(ctx.guild).start_message_returning_channel_id.set(channel.id)
            await self.config.guild(ctx.guild).start_message_returning_content.set(message)
            await ctx.send(f"Returning User start message set for {channel.mention}.")
        else:
            await ctx.send("Invalid type! Use `firsttime` or `notfirsttime`.")

    @ephemeralset.command(name="welcomeembed")
    async def ephemeralset_welcomeembed(self, ctx: commands.Context, channel: discord.TextChannel, title: str, *, description: str):
        """Sets the welcome embed posted on timer start."""
        await self.config.guild(ctx.guild).welcome_embed_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).welcome_embed_title.set(title)
        await self.config.guild(ctx.guild).welcome_embed_description.set(description)
        await ctx.send(f"Welcome Embed configured for {channel.mention}.")

    @ephemeralset.command(name="farewellembed")
    async def ephemeralset_farewellembed(self, ctx: commands.Context, channel: discord.TextChannel, title: str, *, description: str):
        """Sets the farewell embed posted when a user leaves."""
        await self.config.guild(ctx.guild).farewell_embed_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).farewell_embed_title.set(title)
        await self.config.guild(ctx.guild).farewell_embed_description.set(description)
        await ctx.send(f"Farewell Embed configured for {channel.mention}.")

    @ephemeralset.command(name="timerchannel")
    async def ephemeralset_timerchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the ephemeral timer channel."""
        await self.config.guild(ctx.guild).ephemeral_timer_channel_id.set(channel.id)
        await ctx.send(f"Timer channel set to {channel.mention}.")

    @ephemeralset.command(name="notstartedrole")
    async def ephemeralset_notstartedrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Not Started' role."""
        await self.config.guild(ctx.guild).ephemeral_not_started_role_id.set(role.id)
        await ctx.send(f"'Not Started' role set to **{role.name}**.")

    @ephemeralset.command(name="readrulesrole")
    async def ephemeralset_readrulesrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Read Rules' role."""
        await self.config.guild(ctx.guild).ephemeral_read_rules_role_id.set(role.id)
        await ctx.send(f"'Read Rules' role set to **{role.name}**.")

    @ephemeralset.command(name="view", aliases=["show"])
    async def ephemeralset_view(self, ctx: commands.Context):
        """Displays configuration."""
        settings = await self.config.guild(ctx.guild).all()
        
        def get_role_str(rid): 
            r = ctx.guild.get_role(rid)
            return r.mention if r else "Not Set"
        def get_channel_str(cid):
            c = ctx.guild.get_channel(cid)
            return c.mention if c else "Not Set"
        def fmt_warn(c): return f"{'Enabled' if c['enabled'] else 'Disabled'} | {c['action']} | {c['reason']}"

        embed = discord.Embed(title="Ephemeral Mode Config", color=await ctx.embed_color())
        
        act = (f"Phrase: `{settings['activation_phrase']}`\nTimer Ch: {get_channel_str(settings['ephemeral_timer_channel_id'])}\n"
               f"Not Started Role: {get_role_str(settings['ephemeral_not_started_role_id'])}\n"
               f"Read Rules Role: {get_role_str(settings['ephemeral_read_rules_role_id'])}\n"
               f"Start(1st): {get_channel_str(settings['start_message_first_channel_id'])}\n"
               f"Msg: {settings['start_message_first_content']}\n"
               f"Start(Ret): {get_channel_str(settings['start_message_returning_channel_id'])}\n"
               f"Msg: {settings['start_message_returning_content']}\n"
               f"Welcome Embed Ch: {get_channel_str(settings['welcome_embed_channel_id'])}\n"
               f"Welcome Title: `{settings['welcome_embed_title']}`\n"
               f"Farewell Embed Ch: {get_channel_str(settings['farewell_embed_channel_id'])}\n"
               f"Farewell Title: `{settings['farewell_embed_title']}`")
        embed.add_field(name="Activation", value=act, inline=False)
        
        thresh = (f"Expire: {timedelta_to_human(timedelta(seconds=settings['ephemeral_expire_threshold']))}\n"
                  f"NoMsg: {timedelta_to_human(timedelta(seconds=settings['nomessages_threshold']))}\n"
                  f"Msgs: {settings['messages_threshold']} | MinLen: {settings['message_length_threshold']}")
        embed.add_field(name="Thresholds", value=thresh, inline=False)
        
        roles = (f"Ephemeral: {get_role_str(settings['ephemeral_role_id'])}\n"
                 f"Expired: {get_role_str(settings['ephemeral_expire_role_id'])}\n"
                 f"NoMsg: {get_role_str(settings['nomessages_role_id'])}")
        embed.add_field(name="Roles", value=roles, inline=False)
        
        first_td = timedelta(seconds=settings['first_greeting_threshold'])
        second_td = timedelta(seconds=settings['second_greeting_threshold'])
        
        greetings_val = (
            f"**1st Greeting:** {timedelta_to_human(first_td)} in {get_channel_str(settings['first_greeting_channel_id'])}\n"
            f"Message: {settings['first_greeting_message']}\n\n"
            f"**2nd Greeting:** {timedelta_to_human(second_td)} in {get_channel_str(settings['second_greeting_channel_id'])}\n"
            f"Message: {settings['second_greeting_message']}"
        )
        embed.add_field(name="Greetings", value=greetings_val, inline=False)

        failures_val = (
            f"**Expire Fail:** {get_channel_str(settings['expire_message_channel_id'])}\n"
            f"Message: `{settings['expire_message']}`\n\n"
            f"**No Messages Fail:** {get_channel_str(settings['nomessages_failed_message_channel_id'])}\n"
            f"Message: `{settings['nomessages_failed_message']}`"
        )
        embed.add_field(name="Failures", value=failures_val, inline=False)
        
        warns = (f"NoMsg: {fmt_warn(settings['warn_nomessages'])}\n"
                 f"Expire: {fmt_warn(settings['warn_expire'])}\n"
                 f"2ndGreet: {fmt_warn(settings['warn_second_greeting'])}\n"
                 f"NotStarted: {fmt_warn(settings.get('warn_timernotstarted', {'enabled':False, 'action':'N/A', 'reason':'N/A'}))} (Time: {timedelta_to_human(timedelta(seconds=settings.get('warn_timernotstarted', {}).get('time', 0)))})")
        embed.add_field(name="Warnings", value=warns, inline=False)
        
        succ = (f"Channel: {get_channel_str(settings['success_message_channel_id'])}\n"
                f"Title: `{settings.get('success_embed_data', {}).get('title', 'Success')}`")
        embed.add_field(name="Success Embed", value=succ, inline=False)

        embed.add_field(name="Logging", value=f"Channel: {get_channel_str(settings['log_channel_id'])}", inline=False)
        await ctx.send(embed=embed)

    @ephemeralset.command(name="logchannel")
    async def ephemeralset_logchannel(self, ctx: commands.Context, channel: typing.Optional[discord.TextChannel] = None):
        """Sets the log channel."""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"Logging to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("Logging disabled.")

    @ephemeralset.command(name="expiretime")
    async def ephemeralset_expiretime(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours")):
        """Sets Expire threshold."""
        await self.config.guild(ctx.guild).ephemeral_expire_threshold.set(time.total_seconds())
        await ctx.send(f"Expire threshold set to **{timedelta_to_human(time)}**.")

    @ephemeralset.command(name="nomessages")
    async def ephemeralset_nomessages(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours")):
        """Sets No Messages threshold."""
        await self.config.guild(ctx.guild).nomessages_threshold.set(time.total_seconds())
        await ctx.send(f"No Messages threshold set to **{timedelta_to_human(time)}**.")

    @ephemeralset.command(name="messages")
    async def ephemeralset_messages(self, ctx: commands.Context, count: int):
        """Sets required messages."""
        await self.config.guild(ctx.guild).messages_threshold.set(count)
        await ctx.send(f"Required messages set to **{count}**.")

    @ephemeralset.command(name="messagelength")
    async def ephemeralset_messagelength(self, ctx: commands.Context, length: int):
        """Sets min message length."""
        await self.config.guild(ctx.guild).message_length_threshold.set(length)
        await ctx.send(f"Min message length set to **{length}**.")
        
    @ephemeralset.command(name="ephemeralrole")
    async def ephemeralset_ephemeralrole(self, ctx: commands.Context, role: discord.Role):
        """Sets Ephemeral role."""
        await self.config.guild(ctx.guild).ephemeral_role_id.set(role.id)
        await ctx.send(f"Ephemeral role set to **{role.name}**.")

    @ephemeralset.command(name="expirerole")
    async def ephemeralset_expirerole(self, ctx: commands.Context, role: discord.Role):
        """Sets Expired role."""
        await self.config.guild(ctx.guild).ephemeral_expire_role_id.set(role.id)
        await ctx.send(f"Expired role set to **{role.name}**.")

    @ephemeralset.command(name="nomessagesrole")
    async def ephemeralset_nomessagesrole(self, ctx: commands.Context, role: discord.Role):
        """Sets No Messages role."""
        await self.config.guild(ctx.guild).nomessages_role_id.set(role.id)
        await ctx.send(f"No Messages role set to **{role.name}**.")

    @ephemeralset.command(name="firstgreeting")
    async def ephemeralset_firstgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """Sets First Greeting."""
        await self.config.guild(ctx.guild).first_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).first_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).first_greeting_message.set(message)
        await ctx.send(f"First Greeting set for {channel.mention}.")

    @ephemeralset.command(name="secondgreeting")
    async def ephemeralset_secondgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """Sets Second Greeting."""
        await self.config.guild(ctx.guild).second_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).second_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).second_greeting_message.set(message)
        await ctx.send(f"Second Greeting set for {channel.mention}.")
        
    @ephemeralset.command(name="expiremessage")
    async def ephemeralset_expiremessage(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Sets Expire message."""
        await self.config.guild(ctx.guild).expire_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).expire_message.set(message)
        await ctx.send(f"Expire message set for {channel.mention}.")

    @ephemeralset.command(name="nomessagesfailedmessage")
    async def ephemeralset_nomessagesfailedmessage(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Sets No Messages Failed message."""
        await self.config.guild(ctx.guild).nomessages_failed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).nomessages_failed_message.set(message)
        await ctx.send(f"No Messages Failed message set for {channel.mention}.")

    @ephemeralset.command(name="successmessage")
    async def ephemeralset_successmessage(self, ctx: commands.Context, channel: discord.TextChannel, title: str, image_url: str, footer: str, *, description: str):
        """Sets Success Embed."""
        await self.config.guild(ctx.guild).success_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).success_embed_data.set({"title": title, "image_url": image_url, "footer": footer, "description": description})
        await ctx.send(f"Success Embed configured for {channel.mention}.")

    @ephemeralset.group(name="warnings")
    async def ephemeralset_warnings(self, ctx: commands.Context):
        """Configure automated Warning System actions."""
        pass
    
    @ephemeralset_warnings.command(name="nomessages")
    async def warnings_nomessages(self, ctx: commands.Context, enabled: bool, action: str, *, reason: str):
        """Configure warning for 'No Messages' failure."""
        await self.config.guild(ctx.guild).warn_nomessages.set({"enabled": enabled, "action": action.lower(), "reason": reason})
        await ctx.send("Updated 'No Messages' warning config.")

    @ephemeralset_warnings.command(name="expire")
    async def warnings_expire(self, ctx: commands.Context, enabled: bool, action: str, *, reason: str):
        """Configure warning for 'Timer Expired' failure."""
        await self.config.guild(ctx.guild).warn_expire.set({"enabled": enabled, "action": action.lower(), "reason": reason})
        await ctx.send("Updated 'Timer Expired' warning config.")

    @ephemeralset_warnings.command(name="secondgreeting")
    async def warnings_secondgreeting(self, ctx: commands.Context, enabled: bool, action: str, *, reason: str):
        """Configure warning for 'Second Greeting' trigger."""
        await self.config.guild(ctx.guild).warn_second_greeting.set({"enabled": enabled, "action": action.lower(), "reason": reason})
        await ctx.send("Updated 'Second Greeting' warning config.")
        
    @ephemeralset_warnings.command(name="timernotstarted")
    async def warnings_timernotstarted(self, ctx: commands.Context, action: str, time: commands.TimedeltaConverter(default_unit="hours"), *, reason: str):
        """Configure warning for users who join but do not start the timer."""
        if action.lower() not in ["warn", "kick", "ban"]: return await ctx.send("Action must be one of: warn, kick, ban")
        config = {"enabled": True, "action": action.lower(), "time": time.total_seconds(), "reason": reason}
        await self.config.guild(ctx.guild).warn_timernotstarted.set(config)
        await ctx.send(f"Updated 'Timer Not Started' warning config: {config}")