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
            # --- END NEW CONFIGURATION ---
            
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
            
            removed_message_channel_id=None,
            removed_message="{mention} is no longer in Ephemeral mode! 🎉",
            
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
        """Stops the background timer task for a specific user."""
        task = self.timers.pop((guild_id, user_id), None)
        if task and not task.done():
            task.cancel()
            print(f"Ephemeral DEBUG: Timer task STOPPED for User {user_id} in Guild {guild_id}")


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

        formatted_message = message.replace("{mention}", user.mention)
        if time_passed is not None:
            formatted_message = formatted_message.replace("{time_passed}", timedelta_to_human(time_passed))

        try:
            await channel.send(formatted_message)
        except discord.Forbidden:
            print(f"Ephemeral ERROR in Guild {guild.id}: Missing permissions in {channel.name}.")
        except Exception as e:
            print(f"Ephemeral ERROR in Guild {guild.id}: Unexpected error: {e}")

    async def _handle_ephemeral_success(self, guild: discord.Guild, user: discord.Member, settings: dict, manual: bool = False):
        """Handles the success case: message threshold met OR manual intervention."""
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        not_started_role = guild.get_role(settings["ephemeral_not_started_role_id"])

        # 1. Remove Ephemeral Role (MANDATORY)
        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral success." + (" (Manual)" if manual else " (Threshold met)"))
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
        
        await self._send_custom_message(
            guild, user, settings["removed_message_channel_id"], settings["removed_message"]
        )

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
            
            # 3. Send confirmation
            await message.channel.send(
                f"{user.mention} has started Ephemeral Mode! Your messages will now be deleted (but still counted). Be quick!", 
                delete_after=15
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
        
        # Wait a short period to allow for startup race conditions
        await asyncio.sleep(10)

        while True:
            # Check status every 10 seconds
            await asyncio.sleep(10)

            guild = self.bot.get_guild(guild_id)
            user = guild.get_member(user_id)
            
            if not guild or not user:
                print(f"Ephemeral DEBUG: Task EXITING (Guild/User not found): {user_id}")
                self.stop_user_timer(guild_id, user_id)
                return

            member_data = await self.config.member(user).all()
            if not member_data["is_ephemeral"]:
                print(f"Ephemeral DEBUG: Task EXITING (is_ephemeral is False, likely cleared by a command): {user_id}")
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
                # User does not have the required role OR role is misconfigured. Do not delete.
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

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralstatus(self, ctx: commands.Context):
        """Displays all users currently in Ephemeral mode and their time remaining."""
        all_member_data = await self.config.all_members(ctx.guild)
        settings = await self.config.guild(ctx.guild).all()
        failed_threshold_seconds = settings["ephemeral_failed_threshold"]
        ephemeral_users = []
        
        for member_id, data in all_member_data.items():
            if data.get("is_ephemeral") and data.get("start_time"):
                member = ctx.guild.get_member(member_id)
                if not member:
                    # Clean up data for users who left
                    await self.config.member_from_id(member_id).clear()
                    self.stop_user_timer(ctx.guild.id, member_id)
                    continue

                start_time_ts = data["start_time"]
                expiration_ts = start_time_ts + failed_threshold_seconds
                output_line = (
                    f"{member.mention} ({data['message_count']}/{settings['messages_threshold']}) | "
                    f"Expires: <t:{int(expiration_ts)}:R>"
                )
                ephemeral_users.append(output_line)

        if not ephemeral_users:
            await ctx.send("No users are currently in Ephemeral mode.")
            return

        pages = []
        for i, page in enumerate(pagify("\n".join(ephemeral_users), page_length=1000)):
            embed = discord.Embed(
                title=f"Ephemeral Users Status ({len(ephemeral_users)} active)",
                description=page,
                color=await ctx.embed_color()
            )
            if len(ephemeral_users) > 20: 
                 embed.title = f"Ephemeral Users Status (Page {i+1})"
            pages.append(embed)
        
        if len(pages) > 1:
            await menu(ctx, pages, DEFAULT_CONTROLS)
        else:
            await ctx.send(embed=pages[0])
            
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralsuccess(self, ctx: commands.Context, user: discord.Member):
        """Manually removes a user from Ephemeral mode (treating it as a success).
        
        This removes the Ephemeral role, stops their timer, clears their data, 
        and logs a success event.
        """
        member_data = await self.config.member(user).all()
        if not member_data["is_ephemeral"]:
            await ctx.send(f"{user.mention} is not currently in Ephemeral mode.")
            return
            
        settings = await self.config.guild(ctx.guild).all()

        # Call the success handler with the manual flag set to True
        await self._handle_ephemeral_success(ctx.guild, user, settings, manual=True)

        await ctx.send(f"✅ **Success:** Manually removed {user.mention} from Ephemeral mode, treating it as a successful completion.")

    # --- Configuration Commands ---

    @commands.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset(self, ctx: commands.Context):
        """Configures the Ephemeral cog settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
            
    @ephemeralset.command(name="phrase")
    async def ephemeralset_phrase(self, ctx: commands.Context, *, phrase: str):
        """Sets the activation phrase a user must type to start Ephemeral Mode."""
        await self.config.guild(ctx.guild).activation_phrase.set(phrase)
        await ctx.send(f"The Ephemeral activation phrase has been set to: `{phrase}`")

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
        e_role = ctx.guild.get_role(settings["ephemeral_role_id"])
        f_role = ctx.guild.get_role(settings["ephemeral_failed_role_id"])
        nm_role = ctx.guild.get_role(settings["nomessages_role_id"])
        ns_role = ctx.guild.get_role(settings["ephemeral_not_started_role_id"]) 
        
        failed_td = timedelta(seconds=settings['ephemeral_failed_threshold'])
        nomessages_td = timedelta(seconds=settings['nomessages_threshold'])
        first_td = timedelta(seconds=settings['first_greeting_threshold'])
        second_td = timedelta(seconds=settings['second_greeting_threshold'])

        def get_channel_info(cid):
            channel = ctx.guild.get_channel(cid)
            return f"#{channel.name}" if channel else "Not Set"
        
        output = [
            bold("--- Activation Scope ---"),
            f"Activation Phrase: **`{settings['activation_phrase']}`**",
            f"Timer/Deletion Channel: **{get_channel_info(settings['ephemeral_timer_channel_id'])}**",
            f"Required 'Not Started' Role: **{ns_role.name if ns_role else 'Not Set'}** ({settings['ephemeral_not_started_role_id'] or 'N/A'})",
            "",
            bold("--- Time/Message Thresholds ---"),
            f"Ephemeral Failed Threshold (General): **{timedelta_to_human(failed_td)}**",
            f"No Messages Threshold (Zero messages): **{timedelta_to_human(nomessages_td)}**",
            f"Messages Threshold: **{settings['messages_threshold']}** messages",
            f"Message Length Threshold: **{settings['message_length_threshold']}** characters",
            "",
            bold("--- Role Configuration ---"),
            f"Ephemeral Role (to be added): **{e_role.name if e_role else 'Not Set'}** ({settings['ephemeral_role_id'] or 'N/A'})",
            f"Ephemeral Failed Role: **{f_role.name if f_role else 'Not Set'}** ({settings['ephemeral_failed_role_id'] or 'N/A'})",
            f"No Messages Role: **{nm_role.name if nm_role else 'Not Set'}** ({settings['nomessages_role_id'] or 'N/A'})",
            "",
            bold("--- Greetings & Notifications ---"),
            f"First Greeting Time: **{timedelta_to_human(first_td)}**",
            f"First Greeting Channel: **{get_channel_info(settings['first_greeting_channel_id'])}**",
            f"First Greeting Message: `{settings['first_greeting_message']}`",
            "",
            f"Second Greeting Time: **{timedelta_to_human(second_td)}**",
            f"Second Greeting Channel: **{get_channel_info(settings['second_greeting_channel_id'])}**",
            f"Second Greeting Message: `{settings['second_greeting_message']}`",
            "",
            f"Failed Message Channel (Timed Out): **{get_channel_info(settings['failed_message_channel_id'])}**",
            f"Failed Message: `{settings['failed_message']}`",
            "",
            f"No Messages Failed Channel: **{get_channel_info(settings['nomessages_failed_message_channel_id'])}**",
            f"No Messages Failed Message: `{settings['nomessages_failed_message']}`",
            "",
            f"Removed Message Channel: **{get_channel_info(settings['removed_message_channel_id'])}**",
            f"Removed Message: `{settings['removed_message']}`",
            "",
            bold("--- Logging ---"),
            f"Log Channel: **{get_channel_info(settings['log_channel_id'])}** (Logs all deleted messages.)",
        ]
        
        await ctx.send(box('\n'.join(output)))

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

    @ephemeralset.command(name="ephemeralremoved")
    async def ephemeralset_ephemeralremoved(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Sets the Ephemeral Removed message."""
        await self.config.guild(ctx.guild).removed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).removed_message.set(message)
        await ctx.send(f"Ephemeral Removed message set for {channel.mention}.")