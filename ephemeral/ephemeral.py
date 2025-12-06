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
            ephemeral_failed_threshold=timedelta(hours=8).total_seconds(),
            messages_threshold=10,
            message_length_threshold=10,
            ephemeral_role_id=None,
            ephemeral_failed_role_id=None,
            
            # --- CONFIGURATION FOR ACTIVATION SCOPE ---
            activation_phrase="let me in", 
            ephemeral_timer_channel_id=None,
            ephemeral_not_started_role_id=None, 
            # --- END CONFIGURATION ---

            # --- START MESSAGE CONFIGURATION (CONDITIONAL) ---
            start_message_first_channel_id=None,
            start_message_first_content="{mention} has joined for the first time!",
            
            start_message_returning_channel_id=None,
            start_message_returning_content="Welcome back {mention}!",
            # --- END START MESSAGE CONFIGURATION ---
            
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
            
            failed_message_channel_id=None,
            failed_message="⚠️ {mention} has failed Ephemeral mode (Timed out) and has been assigned the Failed role.",
            
            # --- SUCCESS (REMOVED) MESSAGE CONFIGURATION (EMBED) ---
            success_message_channel_id=None,
            success_embed_data={
                "title": "Success!",
                "description": "{mention} is no longer in Ephemeral mode! 🎉",
                "image_url": "none",
                "footer": "Welcome to the server!"
            },
            # --- END SUCCESS MESSAGE CONFIGURATION ---
            
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
        
        self.bg_task = self.bot.loop.create_task(self._init_timers(), name="ephemeral_init")
        
    def cog_unload(self):
        if self.bg_task:
            self.bg_task.cancel()
        for task in self.timers.values():
            if not task.done():
                task.cancel()
        
    async def _init_timers(self):
        await self.bot.wait_until_ready()
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            for member_id, data in (await self.config.all_members(guild)).items():
                if data["is_ephemeral"] and data["start_time"]:
                    self.start_user_timer(guild_id, member_id)

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
                print(f"Ephemeral ERROR: Cannot send log to {channel.name} ({channel.id}) - Missing Permissions")
            except Exception as e:
                print(f"Ephemeral ERROR logging event: {e}")
        elif log_channel_id:
             print(f"Ephemeral DEBUG: Log channel {log_channel_id} configured but could not be resolved.")

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

    async def _send_success_embed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        """Sends the Success (formerly Removed) embed."""
        channel_id = settings["success_message_channel_id"]
        embed_data = settings["success_embed_data"]
        
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        # Prepare strings with replacements
        title = embed_data.get("title", "Success").replace("{username}", user.name).replace("{mention}", user.display_name)
        description = embed_data.get("description", "").replace("{mention}", user.mention).replace("{username}", user.name)
        footer_text = embed_data.get("footer", "").replace("{username}", user.name)
        image_url = embed_data.get("image_url", "none")

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green()
        )
        
        # Automatically set thumbnail to user profile picture
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
        await self.config.member(user).clear()
        
        # Use the new Embed sender
        await self._send_success_embed(guild, user, settings)

        log_msg = f"✅ **Success:** {user.mention} (`{user.id}`) met the message threshold and is no longer Ephemeral."
        if manual:
            log_msg = f"⭐ **Manual Success:** {user.mention} (`{user.id}`) was manually removed from Ephemeral mode."

        await self._log_event(guild, log_msg)
                

    async def _handle_activation(self, message: discord.Message, settings: dict, guild: discord.Guild, user: discord.Member):
        """Handles the activation phrase being typed."""
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])
        
        # Crucial configuration check
        if not ephemeral_role or not not_started_role:
            try:
                await message.delete() # Hide the failed attempt
            except Exception:
                pass
            await message.channel.send(f"{user.mention}, Ephemeral Mode is not fully configured (missing roles). Please notify an admin.", delete_after=10)
            return

        try:
            # 1. Delete the activation message (The phrase itself is hidden)
            await message.delete()
        except discord.Forbidden:
            print(f"Ephemeral CRITICAL ERROR: Cannot delete activation message by {user.id}. Bot is missing 'manage_messages' permission.")
            await message.channel.send(f"{user.mention}, I cannot start Ephemeral Mode because I am missing the **Manage Messages** permission required to hide your activity. Please ask an admin to grant this.", delete_after=15)
            return
        except discord.NotFound:
            pass # Already deleted
            
        try:
            # 2. Add Ephemeral Role & Remove Not Started Role
            self.stop_user_timer(guild.id, user.id)
            
            # The user must have the not_started_role to activate, so we remove it here.
            await user.remove_roles(not_started_role, reason="Started Ephemeral mode via phrase (Role removed).")
            await user.add_roles(ephemeral_role, reason="Started Ephemeral mode via phrase.")
            
            now = datetime.now().timestamp()
            
            await self.config.member(user).set({
                "start_time": now,
                "message_count": 0,
                "is_ephemeral": True,
            })
            
            # 3. Determine and Send Start Message (Conditional on Join Count)
            
            # Default to "first time" config if tracker is unavailable or count is <= 1
            start_channel_id = settings.get("start_message_first_channel_id")
            start_message_content = settings.get("start_message_first_content")
            
            # Check JoinTracker for history
            join_tracker = self.bot.get_cog("JoinTracker")
            if join_tracker:
                try:
                    count = await join_tracker.get_join_count(guild, user.id)
                    # If count > 1 (meaning they have joined more than once), use returning config
                    # 1 join means this is their first time.
                    if count > 1:
                        start_channel_id = settings.get("start_message_returning_channel_id")
                        start_message_content = settings.get("start_message_returning_content")
                except Exception as e:
                    print(f"Ephemeral ERROR: Could not fetch join count from JoinTracker: {e}")
            
            # Send the selected message
            await self._send_custom_message(
                guild, user, start_channel_id, start_message_content
            )
            
            self.start_user_timer(guild.id, user.id)

            # 4. Log event
            await self._log_event(guild, f"▶️ **Started:** {user.mention} (`{user.id}`) typed the phrase and started their timer. **'Not Started' role removed.**")
            print(f"Ephemeral DEBUG: Successful activation sequence completed for {user.id}")

        except discord.Forbidden:
            print(f"Ephemeral ERROR: Forbidden to add/remove role for {user.id}")
            await message.channel.send(f"{user.mention}, I do not have permissions to assign/remove roles. Please check my permissions.", delete_after=10)
        except Exception as e:
            print(f"Ephemeral ERROR: Unhandled exception during activation for {user.id}: {e}")
            await message.channel.send(f"{user.mention}, an unexpected error occurred during activation: {e}", delete_after=10)

    async def _handle_nomessages_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        nomessages_role = guild.get_role(settings["nomessages_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])


        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: No Messages sent.")
            except discord.Forbidden:
                pass
        
        # They should not have this role, but if they do (due to misconfig/error), remove it.
        if not_started_role and not_started_role in user.roles:
            try:
                await user.remove_roles(not_started_role, reason="Ephemeral failed while they still had the 'not started' role.")
            except discord.Forbidden:
                pass


        if nomessages_role:
            try:
                await user.add_roles(nomessages_role, reason="Ephemeral Failed: No Messages sent.")
            except discord.Forbidden:
                pass

        await self._send_custom_message(
            guild, user, settings["nomessages_failed_message_channel_id"], settings["nomessages_failed_message"]
        )
        
        await self._log_event(guild, f"👻 **No messages:** {user.mention} (`{user.id}`) did not send any messages and received the No Messages role.")

        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    async def _handle_ephemeral_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        failed_role = guild.get_role(settings["ephemeral_failed_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])


        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: Timed out.")
            except discord.Forbidden:
                pass

        # They should not have this role, but if they do, remove it.
        if not_started_role and not_started_role in user.roles:
            try:
                await user.remove_roles(not_started_role, reason="Ephemeral failed while they still had the 'not started' role.")
            except discord.Forbidden:
                pass

        if failed_role:
            try:
                await user.add_roles(failed_role, reason="Ephemeral Failed: Timed out.")
            except discord.Forbidden:
                pass

        await self._send_custom_message(
            guild, user, settings["failed_message_channel_id"], settings["failed_message"]
        )
        
        await self._log_event(guild, f"❌ **Failed:** {user.mention} (`{user.id}`) timed out and received the failed role.")

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
                print(f"Ephemeral DEBUG: Task EXITING (Guild/User not found): {user_id}")
                self.stop_user_timer(guild_id, user_id)
                return

            member_data = await self.config.member(user).all()
            if not member_data["is_ephemeral"]:
                print(f"Ephemeral DEBUG: Task EXITING (is_ephemeral is False): {user_id}")
                self.stop_user_timer(guild_id, user_id)
                return

            settings = await self.config.guild(guild).all()
            
            failed_threshold = timedelta(seconds=settings["ephemeral_failed_threshold"])
            nomessages_threshold = timedelta(seconds=settings["nomessages_threshold"])
            second_greeting_threshold = timedelta(seconds=settings["second_greeting_threshold"])
            first_greeting_threshold = timedelta(seconds=settings["first_greeting_threshold"])
            
            start_time = datetime.fromtimestamp(member_data["start_time"])
            time_passed: timedelta = datetime.now() - start_time

            print(f"Ephemeral DEBUG: Checking {user.id}. Time Passed: {time_passed.total_seconds():.0f}s. Message Count: {member_data['message_count']}.")

            # Check 1: No Messages Failure (Highest Priority)
            if time_passed >= nomessages_threshold and member_data["message_count"] == 0:
                print(f"Ephemeral DEBUG: NO MESSAGES FAILED trigger for {user.id}")
                await self._handle_nomessages_failed(guild, user, settings)
                return

            # Check 2: General Time Out Failure
            if time_passed >= failed_threshold:
                print(f"Ephemeral DEBUG: FAILED trigger for {user.id}")
                await self._handle_ephemeral_failed(guild, user, settings)
                return

            # Greeting checks
            elif time_passed >= second_greeting_threshold and not member_data["second_greeting_sent"]:
                print(f"Ephemeral DEBUG: 2nd Greeting trigger for {user.id}")
                await self._send_custom_message(
                    guild, user, settings["second_greeting_channel_id"], settings["second_greeting_message"], time_passed=time_passed
                )
                await self.config.member(user).second_greeting_sent.set(True)
                await self._log_event(guild, f"🕑 **Second Greeting:** Sent to {user.mention} (`{user.id}`).")

            elif time_passed >= first_greeting_threshold and not member_data["first_greeting_sent"]:
                print(f"Ephemeral DEBUG: 1st Greeting trigger for {user.id}")
                await self._send_custom_message(
                    guild, user, settings["first_greeting_channel_id"], settings["first_greeting_message"], time_passed=time_passed
                )
                await self.config.member(user).first_greeting_sent.set(True)
                await self._log_event(guild, f"🕐 **First Greeting:** Sent to {user.mention} (`{user.id}`).")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        member = message.author
        guild = message.guild
        settings = await self.config.guild(guild).all()
        
        # --- PHASE 0: CHANNEL CHECK (Crucial for Scoping) ---
        timer_channel_id = settings.get("ephemeral_timer_channel_id")
        if not timer_channel_id or message.channel.id != timer_channel_id:
            # Not in the configured channel, ignore the message entirely.
            return

        member_data = await self.config.member(member).all()
        is_ephemeral = member_data["is_ephemeral"]
        
        
        # --- PHASE 1: ACTIVATION CHECK (If user is NOT ephemeral) ---
        if not is_ephemeral:
            not_started_role_id = settings.get("ephemeral_not_started_role_id")
            
            if not not_started_role_id:
                # Configuration error, but let the message stay (no deletion)
                return

            not_started_role = guild.get_role(not_started_role_id)
            if not not_started_role or not (not_started_role in member.roles):
                # User does not have the required role to enter Ephemeral Mode, let the message stay and stop.
                return
            
            activation_phrase = settings.get("activation_phrase", "let me in")
            
            # Use .lower().strip() for robust phrase matching
            if message.content.lower().strip() == activation_phrase.lower().strip():
                await self._handle_activation(message, settings, guild, member)
            
            # If the phrase doesn't match, the message stays (as requested: "If a user does not have the 'Ephemeral Timer Not Started' role, we do not delete that message.")
            return 

        # --- PHASE 2: EPHEMERAL MESSAGE HANDLING (If user IS ephemeral) ---
        
        # 1. Log the message content
        await self._log_ephemeral_message(message, settings)
        
        # 2. Delete the message
        try:
            await message.delete()
        except discord.Forbidden:
            print(f"Ephemeral CRITICAL ERROR: Bot cannot delete messages from user {member.id} in channel {message.channel.id}.")
        except discord.NotFound:
            pass
        
        # 3. Message count and removal logic
        if len(message.content) < settings["message_length_threshold"]:
            return
        
        new_count = member_data["message_count"] + 1
        await self.config.member(member).message_count.set(new_count)

        if new_count >= settings["messages_threshold"]:
            await self._handle_ephemeral_success(guild, member, settings)

    # --- Configuration Commands ---

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralstatus(self, ctx: commands.Context):
        """Shows all users currently in Ephemeral mode with their progress and timer."""
        guild = ctx.guild
        settings = await self.config.guild(guild).all()
        
        # Get all members marked as ephemeral
        all_member_data = await self.config.all_members(guild)
        ephemeral_members = []

        for member_id, data in all_member_data.items():
            if data["is_ephemeral"] and data["start_time"] is not None:
                member = guild.get_member(member_id)
                if member:
                    ephemeral_members.append((member, data))
        
        if not ephemeral_members:
            return await ctx.send("No users are currently in Ephemeral mode.")

        # --- NEW EMBED LOGIC for Rich Text Output with Pagination (10 per page) ---
        
        MAX_FIELDS_PER_PAGE = 10
        
        failed_threshold = timedelta(seconds=settings["ephemeral_failed_threshold"])
        
        pages = []
        current_embed = None
        
        for i, (member, data) in enumerate(ephemeral_members):
            if i % MAX_FIELDS_PER_PAGE == 0:
                # Start a new page/embed
                if current_embed:
                    pages.append(current_embed)
                
                current_embed = discord.Embed(
                    title="👻 Ephemeral Mode Status",
                    description=f"Showing **{len(ephemeral_members)}** active ephemeral users in total.",
                    color=await ctx.embed_color()
                )
                
            start_time = datetime.fromtimestamp(data["start_time"])
            
            # Calculate expiry time
            expiry_time = start_time + failed_threshold
            time_remaining = expiry_time - datetime.now()
            
            if time_remaining.total_seconds() < 0:
                time_remaining = timedelta(seconds=0)

            # Format progress
            messages_sent = data["message_count"]
            messages_required = settings["messages_threshold"]
            
            progress_value = (
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Progress:** {messages_sent}/{messages_required} messages\n"
                f"**Started:** {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"**Expires In:** {timedelta_to_human(time_remaining)}"
            )
            
            # Use the member's display name as field title (Mentions don't render in titles)
            current_embed.add_field(
                name=f"{member.display_name}", 
                value=progress_value,
                inline=False
            )

        # Append the last embed
        if current_embed:
            pages.append(current_embed)

        # Update footers with correct total page count
        total_pages = len(pages)
        for idx, embed in enumerate(pages):
             embed.set_footer(text=f"Page {idx + 1}/{total_pages}")

        # Use menu for navigation
        await menu(ctx, pages, DEFAULT_CONTROLS)


    @commands.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset(self, ctx: commands.Context):
        """Configures the Ephemeral cog settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
            
    @ephemeralset.command(name="success")
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset_success(self, ctx: commands.Context, user: discord.Member):
        """Manually marks a user as having succeeded Ephemeral mode (removes role, clears data)."""
        member_data = await self.config.member(user).all()

        if not member_data["is_ephemeral"]:
            return await ctx.send(f"{user.mention} is not currently in Ephemeral mode.")

        settings = await self.config.guild(ctx.guild).all()
        await self._handle_ephemeral_success(ctx.guild, user, settings, manual=True)
        await ctx.send(f"✅ Successfully marked {user.mention} as succeeded in Ephemeral mode. Roles removed and data cleared.")


    @ephemeralset.command(name="phrase")
    async def ephemeralset_phrase(self, ctx: commands.Context, *, phrase: str):
        """Sets the activation phrase a user must type to start Ephemeral Mode."""
        await self.config.guild(ctx.guild).activation_phrase.set(phrase)
        await ctx.send(f"The Ephemeral activation phrase has been set to: `{phrase}`")

    @ephemeralset.command(name="startmessage")
    async def ephemeralset_startmessage(self, ctx: commands.Context, msg_type: str, channel: discord.TextChannel, *, message: str):
        """Sets the start message based on user history.
        
        Args:
            msg_type: "firsttime" or "notfirsttime" (or "returning")
            channel: The channel to post the message in
            message: The message content
        """
        msg_type = msg_type.lower()
        if msg_type in ["firsttime", "first"]:
            await self.config.guild(ctx.guild).start_message_first_channel_id.set(channel.id)
            await self.config.guild(ctx.guild).start_message_first_content.set(message)
            key_name = "First Time"
        elif msg_type in ["notfirsttime", "returning"]:
            await self.config.guild(ctx.guild).start_message_returning_channel_id.set(channel.id)
            await self.config.guild(ctx.guild).start_message_returning_content.set(message)
            key_name = "Returning User"
        else:
            return await ctx.send("Invalid type! Use `firsttime` or `notfirsttime`.")
            
        await ctx.send(
            f"{key_name} start message configured:\n"
            f"Channel: {channel.mention}\n"
            f"Message: `{message}`"
        )

    @ephemeralset.command(name="timerchannel")
    async def ephemeralset_timerchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the specific channel where Ephemeral mode is activated and messages are deleted."""
        await self.config.guild(ctx.guild).ephemeral_timer_channel_id.set(channel.id)
        await ctx.send(f"Ephemeral Timer channel set to {channel.mention}. Only messages here will be deleted/logged.")

    @ephemeralset.command(name="notstartedrole")
    async def ephemeralset_notstartedrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the role users MUST have to start the Ephemeral Timer. This role will be removed upon successful activation."""
        await self.config.guild(ctx.guild).ephemeral_not_started_role_id.set(role.id)
        await ctx.send(f"Ephemeral 'Not Started' required role set to **{role.name}**.")

    @ephemeralset.command(name="view", aliases=["show"])
    async def ephemeralset_view(self, ctx: commands.Context):
        """Displays the current Ephemeral cog settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        # Helpers for consistent formatting
        def get_role_str(role_id):
            if not role_id: return "Not Set"
            role = ctx.guild.get_role(role_id)
            return role.mention if role else f"ID: {role_id} (Deleted)"

        def get_channel_str(channel_id):
            if not channel_id: return "Not Set"
            chan = ctx.guild.get_channel(channel_id)
            return chan.mention if chan else f"ID: {channel_id} (Deleted)"

        embed = discord.Embed(
            title="Ephemeral Mode Configuration",
            color=await ctx.embed_color()
        )

        # Activation Scope
        activation_scope_val = (
            f"**Phrase:** `{settings['activation_phrase']}`\n"
            f"**Timer Channel:** {get_channel_str(settings['ephemeral_timer_channel_id'])}\n"
            f"**Required Role:** {get_role_str(settings['ephemeral_not_started_role_id'])}\n\n"
            f"**Start Msg (First Time):**\n"
            f"Channel: {get_channel_str(settings['start_message_first_channel_id'])}\n"
            f"Message: `{settings['start_message_first_content']}`\n\n"
            f"**Start Msg (Returning):**\n"
            f"Channel: {get_channel_str(settings['start_message_returning_channel_id'])}\n"
            f"Message: `{settings['start_message_returning_content']}`"
        )
        embed.add_field(name="Activation Scope", value=activation_scope_val, inline=False)

        # Time/Message Thresholds
        failed_td = timedelta(seconds=settings['ephemeral_failed_threshold'])
        nomessages_td = timedelta(seconds=settings['nomessages_threshold'])
        
        thresholds_val = (
            f"**Total Timeout:** {timedelta_to_human(failed_td)}\n"
            f"**No Messages Timeout:** {timedelta_to_human(nomessages_td)}\n"
            f"**Required Messages:** {settings['messages_threshold']}\n"
            f"**Min Message Length:** {settings['message_length_threshold']} chars"
        )
        embed.add_field(name="Thresholds", value=thresholds_val, inline=False)

        # Roles
        roles_val = (
            f"**Ephemeral:** {get_role_str(settings['ephemeral_role_id'])}\n"
            f"**Failed:** {get_role_str(settings['ephemeral_failed_role_id'])}\n"
            f"**No Messages:** {get_role_str(settings['nomessages_role_id'])}"
        )
        embed.add_field(name="Roles", value=roles_val, inline=False)

        # Notifications (Greetings)
        first_td = timedelta(seconds=settings['first_greeting_threshold'])
        second_td = timedelta(seconds=settings['second_greeting_threshold'])
        
        greetings_val = (
            f"**1st Greeting:** {timedelta_to_human(first_td)} in {get_channel_str(settings['first_greeting_channel_id'])}\n"
            f"Message: `{settings['first_greeting_message']}`\n\n"
            f"**2nd Greeting:** {timedelta_to_human(second_td)} in {get_channel_str(settings['second_greeting_channel_id'])}\n"
            f"Message: `{settings['second_greeting_message']}`"
        )
        embed.add_field(name="Greetings", value=greetings_val, inline=False)

        # Notifications (Failures)
        failures_val = (
            f"**General Fail:** {get_channel_str(settings['failed_message_channel_id'])}\n"
            f"Message: `{settings['failed_message']}`\n\n"
            f"**No Messages Fail:** {get_channel_str(settings['nomessages_failed_message_channel_id'])}\n"
            f"Message: `{settings['nomessages_failed_message']}`"
        )
        embed.add_field(name="Failures", value=failures_val, inline=False)

        # Success
        success_val = (
            f"**Channel:** {get_channel_str(settings['success_message_channel_id'])}\n"
            f"**Title:** `{settings['success_embed_data']['title']}`\n"
            f"**Footer:** `{settings['success_embed_data']['footer']}`"
        )
        embed.add_field(name="Success", value=success_val, inline=False)

        # Logging
        embed.add_field(name="Logging", value=f"**Channel:** {get_channel_str(settings['log_channel_id'])}", inline=False)

        await ctx.send(embed=embed)

    @ephemeralset.command(name="logchannel")
    async def ephemeralset_logchannel(self, ctx: commands.Context, channel: typing.Optional[discord.TextChannel] = None):
        """Sets the channel for logging Ephemeral events (including hidden messages).
        
        Leave empty to disable logging.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"Ephemeral events (including hidden user messages) will be logged to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("Ephemeral event logging has been disabled.")

    @ephemeralset.command(name="failedtime")
    async def ephemeralset_failedtime(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours")):
        """Sets the Ephemeral Failed time threshold (General Timeout)."""
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
        await self.config.guild(ctx.guild).ephemeral_failed_threshold.set(time.total_seconds())
        await ctx.send(f"Ephemeral Failed threshold set to **{timedelta_to_human(time)}**.")

    @ephemeralset.command(name="nomessages")
    async def ephemeralset_nomessages(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours")):
        """Sets the 'No Messages' time threshold. User fails if they send 0 messages by this time."""
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
        await self.config.guild(ctx.guild).nomessages_threshold.set(time.total_seconds())
        await ctx.send(f"'No Messages' threshold set to **{timedelta_to_human(time)}**.")

    @ephemeralset.command(name="messages")
    async def ephemeralset_messages(self, ctx: commands.Context, count: int):
        """Sets the number of messages threshold to remove the Ephemeral role."""
        if count <= 0:
            return await ctx.send("Message count must be a positive number.")
        await self.config.guild(ctx.guild).messages_threshold.set(count)
        await ctx.send(f"Message count threshold set to **{count}** messages.")

    @ephemeralset.command(name="messagelength")
    async def ephemeralset_messagelength(self, ctx: commands.Context, length: int):
        """Sets the minimum message length (characters) required."""
        if length < 1:
            return await ctx.send("Message length must be at least 1 character.")
        await self.config.guild(ctx.guild).message_length_threshold.set(length)
        await ctx.send(f"Message length threshold set to **{length}** characters.")
        
    @ephemeralset.command(name="ephemeralrole")
    async def ephemeralset_ephemeralrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Ephemeral' role (The role added upon successful activation)."""
        await self.config.guild(ctx.guild).ephemeral_role_id.set(role.id)
        await ctx.send(f"Ephemeral role set to **{role.name}**.")

    @ephemeralset.command(name="failedrole")
    async def ephemeralset_failedrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Ephemeral Failed' role (General Timeout)."""
        await self.config.guild(ctx.guild).ephemeral_failed_role_id.set(role.id)
        await ctx.send(f"Ephemeral Failed role set to **{role.name}**.")

    @ephemeralset.command(name="nomessagesrole")
    async def ephemeralset_nomessagesrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'No Messages' role."""
        await self.config.guild(ctx.guild).nomessages_role_id.set(role.id)
        await ctx.send(f"'No Messages' role set to **{role.name}**.")

    @ephemeralset.command(name="firstgreeting")
    async def ephemeralset_firstgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """Sets the First Greeting."""
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
        
        await self.config.guild(ctx.guild).first_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).first_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).first_greeting_message.set(message)
        await ctx.send(f"First Greeting set for {channel.mention}.")

    @ephemeralset.command(name="secondgreeting")
    async def ephemeralset_secondgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """Sets the Second Greeting."""
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
            
        await self.config.guild(ctx.guild).second_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).second_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).second_greeting_message.set(message)
        await ctx.send(f"Second Greeting set for {channel.mention}.")
        
    @ephemeralset.command(name="failedmessage")
    async def ephemeralset_failedmessage(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Sets the Ephemeral Failed message (General Timeout)."""
        await self.config.guild(ctx.guild).failed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).failed_message.set(message)
        await ctx.send(f"Ephemeral Failed message set for {channel.mention}.")

    @ephemeralset.command(name="nomessagesfailedmessage")
    async def ephemeralset_nomessagesfailedmessage(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Sets the 'No Messages' Failed message."""
        await self.config.guild(ctx.guild).nomessages_failed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).nomessages_failed_message.set(message)
        await ctx.send(f"'No Messages' Failed message set for {channel.mention}.")

    @ephemeralset.command(name="successmessage")
    async def ephemeralset_successmessage(self, ctx: commands.Context, channel: discord.TextChannel, title: str, image_url: str, footer: str, *, description: str):
        """Sets the Ephemeral Success Embed.
        
        Usage: [p]ephemeralset successmessage <channel> <title> <image_url|none> <footer> <description>
        """
        await self.config.guild(ctx.guild).success_message_channel_id.set(channel.id)
        
        embed_data = {
            "title": title,
            "image_url": image_url,
            "footer": footer,
            "description": description
        }
        await self.config.guild(ctx.guild).success_embed_data.set(embed_data)
        
        await ctx.send(f"Ephemeral Success Embed configured for {channel.mention}.")