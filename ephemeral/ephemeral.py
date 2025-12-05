import asyncio
import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.chat_formatting import humanize_list, box, bold, pagify
from datetime import datetime, timedelta
import typing

# Helper to map button color strings to discord.ButtonStyle
BUTTON_COLOR_MAP = {
    "blue": discord.ButtonStyle.blurple,
    "green": discord.ButtonStyle.green,
    "red": discord.ButtonStyle.red,
    "grey": discord.ButtonStyle.grey,
    "gray": discord.ButtonStyle.grey,
}

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


# Custom view for the persistent button
class EphemeralButton(discord.ui.View):
    def __init__(self, cog: "Ephemeral"):
        super().__init__(timeout=None)
        self.cog = cog
        self.cog.bot.loop.create_task(self._update_button_appearance())

    async def _update_button_appearance(self):
        """Update button label and style from stored config after cog load."""
        await self.cog.bot.wait_until_ready()
        
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "ephemeral:start_button":
                item.label = "Click to Start Ephemeral Mode" 
                item.style = discord.ButtonStyle.green
                break

    @discord.ui.button(label="Click to Start Ephemeral Mode", style=discord.ButtonStyle.green, custom_id="ephemeral:start_button")
    async def start_ephemeral(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        # Use bot cache to ensure full guild object (helper for channel lookup reliability)
        guild = self.cog.bot.get_guild(interaction.guild_id) or interaction.guild
        
        settings = await self.cog.config.guild(guild).all()
        button_data = settings.get("embed_data", {})
        original_label = button_data.get("button_label", "Start Ephemeral Mode")

        button.label = "Processing..."
        button.style = discord.ButtonStyle.grey
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        ephemeral_role_id = settings["ephemeral_role_id"]
        
        if not ephemeral_role_id:
            await interaction.followup.send("Ephemeral role is not configured for this server.", ephemeral=True)
            return

        ephemeral_role = guild.get_role(ephemeral_role_id)
        if not ephemeral_role:
            await interaction.followup.send("Ephemeral role not found. Please reconfigure.", ephemeral=True)
            return

        if ephemeral_role in user.roles:
            await interaction.followup.send("You are already in Ephemeral mode!", ephemeral=True)
            return
            
        try:
            await user.add_roles(ephemeral_role, reason="Started Ephemeral mode via button.")
            now = datetime.now().timestamp()
            
            await self.cog.config.member(user).set({
                "start_time": now,
                "message_count": 0,
                "is_ephemeral": True,
            })
            
            await interaction.followup.send(
                f"You have been assigned the **{ephemeral_role.name}** role and your Ephemeral timer has started! "
                "Be sure to meet the message threshold before you time out."
                , ephemeral=True
            )
            self.cog.start_user_timer(guild.id, user.id)
            
            # Log event
            await self.cog._log_event(guild, f"▶️ **Started:** {user.mention} (`{user.id}`) clicked the button and started their timer.")

        except discord.Forbidden:
            await interaction.followup.send("I do not have permissions to assign the Ephemeral role.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
        finally:
            button.label = original_label
            button.style = BUTTON_COLOR_MAP.get(button_data.get("button_color", "green").lower(), discord.ButtonStyle.green)
            button.disabled = False
            try:
                await interaction.edit_original_response(view=self)
            except discord.NotFound:
                pass


# Main Cog Class
class Ephemeral(commands.Cog):
    """
    Manages temporary roles and message counting for users entering 'Ephemeral Mode'.
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
            
            # --- NEW NOMESSAGES CONFIGURATION ---
            nomessages_threshold=timedelta(hours=4).total_seconds(), # Default 4 hours
            nomessages_role_id=None,
            nomessages_failed_message_channel_id=None,
            nomessages_failed_message="👻 {mention} has failed Ephemeral mode (No Messages) and has been assigned the No Messages role.",
            # --- END NEW CONFIGURATION ---
            
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

            embed_channel_id=None,
            embed_message_id=None,
            embed_data={
                "title": "Welcome", 
                "description": "Click the button to start.",
                "thumbnail_url": "none",
                "image_url": "none",
                "button_label": "Start Ephemeral Mode",
                "button_color": "green",
            },
        )
        self.config.register_member(
            is_ephemeral=False,
            start_time=None,
            message_count=0,
            first_greeting_sent=False,
            second_greeting_sent=False,
        )
        self.timers = {}
        
        self.bot.add_view(EphemeralButton(self))
        
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

        # Added name for easier debugging of running tasks
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

        # Attempt to find the channel using the guild object first
        channel = guild.get_channel(log_channel_id)
        # Fallback to bot global cache if guild fetch fails (sometimes needed for interactions)
        if not channel:
            channel = self.bot.get_channel(log_channel_id)

        if channel and isinstance(channel, discord.TextChannel):
            try:
                # Send with allowed_mentions=none to prevent mass pings in logs
                await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                print(f"Ephemeral ERROR: Cannot send log to {channel.name} ({channel.id}) - Missing Permissions")
            except Exception as e:
                print(f"Ephemeral ERROR logging event: {e}")
        elif log_channel_id:
             print(f"Ephemeral DEBUG: Log channel {log_channel_id} configured but could not be resolved.")

    async def _send_custom_message(self, guild: discord.Guild, user: discord.Member, channel_id: typing.Optional[int], message: str, time_passed: typing.Optional[timedelta] = None):
        if not channel_id:
            print(f"Ephemeral DEBUG: Skipping message in Guild {guild.id} (No Channel ID configured).")
            return
            
        channel = guild.get_channel(channel_id)
        
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Ephemeral ERROR in Guild {guild.id}: Channel {channel_id} invalid.")
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

    async def _handle_nomessages_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        nomessages_role = guild.get_role(settings["nomessages_role_id"])

        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: No Messages sent.")
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
        
        # Log Event
        await self._log_event(guild, f"👻 **No messages:** {user.mention} (`{user.id}`) did not send any messages and received the No Messages role.")

        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    async def _handle_ephemeral_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        failed_role = guild.get_role(settings["ephemeral_failed_role_id"])

        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: Timed out.")
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
        
        # Log Event
        await self._log_event(guild, f"❌ **Failed:** {user.mention} (`{user.id}`) timed out and received the failed role.")

        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    async def check_ephemeral_status(self, guild_id: int, user_id: int):
        # Initial short sleep to ensure startup/initial config load is complete.
        await asyncio.sleep(10)

        # Loop runs every 10 seconds for high-frequency checks.
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
            
            failed_threshold = timedelta(seconds=settings["ephemeral_failed_threshold"])
            nomessages_threshold = timedelta(seconds=settings["nomessages_threshold"])
            second_greeting_threshold = timedelta(seconds=settings["second_greeting_threshold"])
            first_greeting_threshold = timedelta(seconds=settings["first_greeting_threshold"])
            
            start_time = datetime.fromtimestamp(member_data["start_time"])
            time_passed: timedelta = datetime.now() - start_time

            # Check 1: No Messages Failure (Highest Priority, specific failure)
            if time_passed >= nomessages_threshold and member_data["message_count"] == 0:
                print(f"Ephemeral DEBUG: NO MESSAGES FAILED trigger for {user.id}")
                await self._handle_nomessages_failed(guild, user, settings)
                return

            # Check 2: General Time Out Failure
            if time_passed >= failed_threshold:
                print(f"Ephemeral DEBUG: FAILED trigger for {user.id}")
                await self._handle_ephemeral_failed(guild, user, settings)
                return

            # Greeting checks (only run if not failed yet)
            elif time_passed >= second_greeting_threshold and not member_data["second_greeting_sent"]:
                print(f"Ephemeral DEBUG: 2nd Greeting trigger for {user.id}")
                await self._send_custom_message(
                    guild, user, settings["second_greeting_channel_id"], settings["second_greeting_message"], time_passed=time_passed
                )
                await self.config.member(user).second_greeting_sent.set(True)
                # Log Event
                await self._log_event(guild, f"🕑 **Second Greeting:** Sent to {user.mention} (`{user.id}`).")

            elif time_passed >= first_greeting_threshold and not member_data["first_greeting_sent"]:
                print(f"Ephemeral DEBUG: 1st Greeting trigger for {user.id}")
                await self._send_custom_message(
                    guild, user, settings["first_greeting_channel_id"], settings["first_greeting_message"], time_passed=time_passed
                )
                await self.config.member(user).first_greeting_sent.set(True)
                # Log Event
                await self._log_event(guild, f"🕐 **First Greeting:** Sent to {user.mention} (`{user.id}`).")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        member = message.author
        guild = message.guild
        
        member_data = await self.config.member(member).all()
        if not member_data["is_ephemeral"]:
            return

        settings = await self.config.guild(guild).all()
        
        if len(message.content) < settings["message_length_threshold"]:
            return
        
        new_count = member_data["message_count"] + 1
        await self.config.member(member).message_count.set(new_count)

        if new_count >= settings["messages_threshold"]:
            ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
            if ephemeral_role and ephemeral_role in member.roles:
                try:
                    await member.remove_roles(ephemeral_role, reason="Ephemeral message threshold met.")
                    
                    self.stop_user_timer(guild.id, member.id)
                    await self.config.member(member).clear()
                    
                    print(f"Ephemeral DEBUG: REMOVED trigger for {member.id}")
                    await self._send_custom_message(
                        guild, member, settings["removed_message_channel_id"], settings["removed_message"]
                    )

                    # Log Event
                    await self._log_event(guild, f"✅ **Success:** {member.mention} (`{member.id}`) met the message threshold and is no longer Ephemeral.")
                    
                except discord.Forbidden:
                    pass

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

    @commands.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset(self, ctx: commands.Context):
        """Configures the Ephemeral cog settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ephemeralset.command(name="view", aliases=["show"])
    async def ephemeralset_view(self, ctx: commands.Context):
        """Displays the current Ephemeral cog settings."""
        settings = await self.config.guild(ctx.guild).all()
        e_role = ctx.guild.get_role(settings["ephemeral_role_id"])
        f_role = ctx.guild.get_role(settings["ephemeral_failed_role_id"])
        nm_role = ctx.guild.get_role(settings["nomessages_role_id"]) # New role lookup
        
        failed_td = timedelta(seconds=settings['ephemeral_failed_threshold'])
        nomessages_td = timedelta(seconds=settings['nomessages_threshold']) # New time lookup
        first_td = timedelta(seconds=settings['first_greeting_threshold'])
        second_td = timedelta(seconds=settings['second_greeting_threshold'])

        def get_channel_info(cid):
            channel = ctx.guild.get_channel(cid)
            return f"#{channel.name}" if channel else "Not Set"
        
        output = [
            bold("Time/Message Thresholds:"),
            f"Ephemeral Failed Threshold (General): **{timedelta_to_human(failed_td)}**",
            f"No Messages Threshold (Zero messages): **{timedelta_to_human(nomessages_td)}**", # New display
            f"Messages Threshold: **{settings['messages_threshold']}** messages",
            f"Message Length Threshold: **{settings['message_length_threshold']}** characters",
            "",
            bold("Role Configuration:"),
            f"Ephemeral Role: **{e_role.name if e_role else 'Not Set'}** ({settings['ephemeral_role_id'] or 'N/A'})",
            f"Ephemeral Failed Role: **{f_role.name if f_role else 'Not Set'}** ({settings['ephemeral_failed_role_id'] or 'N/A'})",
            f"No Messages Role: **{nm_role.name if nm_role else 'Not Set'}** ({settings['nomessages_role_id'] or 'N/A'})", # New display
            "",
            bold("Greetings & Notifications:"),
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
            f"No Messages Failed Channel: **{get_channel_info(settings['nomessages_failed_message_channel_id'])}**", # New display
            f"No Messages Failed Message: `{settings['nomessages_failed_message']}`", # New display
            "",
            f"Removed Message Channel: **{get_channel_info(settings['removed_message_channel_id'])}**",
            f"Removed Message: `{settings['removed_message']}`",
            "",
            bold("Logging:"),
            f"Log Channel: **{get_channel_info(settings['log_channel_id'])}**",
        ]
        
        await ctx.send(box('\n'.join(output)))

    @ephemeralset.command(name="logchannel")
    async def ephemeralset_logchannel(self, ctx: commands.Context, channel: typing.Optional[discord.TextChannel] = None):
        """Sets the channel for logging Ephemeral events.
        
        Leave empty to disable logging.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"Ephemeral events will be logged to {channel.mention}.")
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
        """Sets the 'Ephemeral' role."""
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

    @ephemeralset.command(name="embed")
    async def ephemeralset_embed(self, ctx: commands.Context, channel: discord.TextChannel, title: str, thumbnail_url: str, image_url: str, button_label: str, button_color: str, *, description: str):
        """Generates and posts the Ephemeral starting embed."""
        button_style = BUTTON_COLOR_MAP.get(button_color.lower())
        if not button_style:
            return await ctx.send(f"Invalid button color. Must be one of: {humanize_list(list(BUTTON_COLOR_MAP.keys()))}")

        embed = discord.Embed(title=title, description=description, color=await ctx.embed_color())
        if thumbnail_url.lower() != "none":
            embed.set_thumbnail(url=thumbnail_url)
        if image_url.lower() != "none":
            embed.set_image(url=image_url)

        view = EphemeralButton(self)
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "ephemeral:start_button":
                item.label = button_label
                item.style = button_style
                break
        
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            return await ctx.send(f"I don't have permission to send messages in {channel.mention}.")

        await self.config.guild(ctx.guild).embed_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).embed_message_id.set(msg.id)
        await self.config.guild(ctx.guild).embed_data.set({
            "title": title,
            "description": description,
            "thumbnail_url": thumbnail_url,
            "image_url": image_url,
            "button_label": button_label,
            "button_color": button_color,
        })
        
        await ctx.send(f"Ephemeral embed posted successfully in {channel.mention}.")