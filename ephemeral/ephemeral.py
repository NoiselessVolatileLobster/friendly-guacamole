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
        # Call a helper to load the configured button properties onto the component
        self.cog.bot.loop.create_task(self._update_button_appearance())

    # This method is called internally by discord.py to restore persistent views.
    async def _update_button_appearance(self):
        """Update button label and style from stored config after cog load."""
        await self.cog.bot.wait_until_ready()
        
        # Simple default button definition
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "ephemeral:start_button":
                item.label = "Click to Start Ephemeral Mode" # Default label
                item.style = discord.ButtonStyle.green # Default style
                break

    @discord.ui.button(label="Click to Start Ephemeral Mode", style=discord.ButtonStyle.green, custom_id="ephemeral:start_button")
    async def start_ephemeral(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild
        
        # Fetch current button data for immediate visual update
        settings = await self.cog.config.guild(guild).all()
        button_data = settings.get("embed_data", {})
        original_label = button_data.get("button_label", "Start Ephemeral Mode")

        # Give immediate feedback to the user on the button
        button.label = "Processing..."
        button.style = discord.ButtonStyle.grey
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        # --- Core Logic ---
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
            
        # Give role and record timestamp
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
            # Start the background task for this user
            self.cog.start_user_timer(guild.id, user.id)

        except discord.Forbidden:
            await interaction.followup.send("I do not have permissions to assign the Ephemeral role.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
        finally:
            # Re-enable the button and restore its original appearance
            button.label = original_label
            button.style = BUTTON_COLOR_MAP.get(button_data.get("button_color", "green").lower(), discord.ButtonStyle.green)
            button.disabled = False
            try:
                # Use edit_original_response if possible, or edit_message
                await interaction.edit_original_response(view=self)
            except discord.NotFound:
                # If the original response was ephemeral, we may need to use a different method.
                # Since we already used response.edit_message, we just rely on the next interaction
                pass


# Main Cog Class
class Ephemeral(commands.Cog):
    """
    Manages temporary roles and message counting for users entering 'Ephemeral Mode'.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1480084004, force_registration=True)
        # Store time thresholds in seconds to be JSON serializable
        self.config.register_guild(
            ephemeral_failed_threshold=timedelta(hours=8).total_seconds(), # 28800.0
            messages_threshold=10,
            message_length_threshold=10,
            ephemeral_role_id=None,
            ephemeral_failed_role_id=None,
            
            # --- Greeting and Notification Configurations ---
            
            # First Greeting
            first_greeting_threshold=timedelta(hours=3).total_seconds(), # 10800.0
            first_greeting_channel_id=None,
            first_greeting_message="It looks like you haven't sent enough messages yet in {time_passed}. {mention}",
            
            # Second Greeting
            second_greeting_threshold=timedelta(hours=5).total_seconds(), # 18000.0
            second_greeting_channel_id=None,
            second_greeting_message="You've been in Ephemeral mode for {time_passed}. Please continue interacting! {mention}",
            
            # Failure Message
            failed_message_channel_id=None,
            failed_message="⚠️ {mention} has failed Ephemeral mode (Timed out) and has been assigned the Failed role.",
            
            # Removed Message
            removed_message_channel_id=None,
            removed_message="{mention} is no longer in Ephemeral mode! 🎉",
            
            # Embed/Button
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
        
        # Register the view class for persistence immediately upon cog load
        self.bot.add_view(EphemeralButton(self))
        
        # Start initial checks for running timers
        self.bg_task = self.bot.loop.create_task(self._init_timers())
        
    def cog_unload(self):
        if self.bg_task:
            self.bg_task.cancel()
        for task in self.timers.values():
            if not task.done():
                task.cancel()
        
    async def _init_timers(self):
        """Initializes all running timers from stored configuration."""
        await self.bot.wait_until_ready()
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            for member_id, data in (await self.config.all_members(guild)).items():
                if data["is_ephemeral"] and data["start_time"]:
                    self.start_user_timer(guild_id, member_id)

    def start_user_timer(self, guild_id: int, user_id: int):
        """Starts the background task for a single user's ephemeral timer."""
        # Cancel any existing task for this user
        task_key = (guild_id, user_id)
        if task_key in self.timers:
            self.timers[task_key].cancel()

        # Start new task
        task = self.bot.loop.create_task(self.check_ephemeral_status(guild_id, user_id))
        self.timers[task_key] = task
    
    def stop_user_timer(self, guild_id: int, user_id: int):
        """Stops the background task for a single user's ephemeral timer."""
        task = self.timers.pop((guild_id, user_id), None)
        if task and not task.done():
            task.cancel()

    async def _send_custom_message(self, guild: discord.Guild, user: discord.Member, channel_id: typing.Optional[int], message: str, time_passed: typing.Optional[timedelta] = None):
        """Replaces placeholders and sends a message to a specific channel."""
        if not channel_id:
            return
            
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        # Replace placeholders
        formatted_message = message.replace("{mention}", user.mention)
        if time_passed is not None:
            formatted_message = formatted_message.replace("{time_passed}", timedelta_to_human(time_passed))

        try:
            await channel.send(formatted_message)
        except discord.Forbidden:
            # Bot cannot send message in this channel
            pass
        except Exception:
            # Other errors (e.g., channel deleted)
            pass

    async def check_ephemeral_status(self, guild_id: int, user_id: int):
        """Background task to check for time-based thresholds and role failure."""
        
        await asyncio.sleep(60) # Initial wait

        while True:
            await asyncio.sleep(120) # Check every 2 minutes

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
            
            # Convert stored seconds back to timedelta for comparison
            failed_threshold = timedelta(seconds=settings["ephemeral_failed_threshold"])
            second_greeting_threshold = timedelta(seconds=settings["second_greeting_threshold"])
            first_greeting_threshold = timedelta(seconds=settings["first_greeting_threshold"])
            
            start_time = datetime.fromtimestamp(member_data["start_time"])
            time_passed: timedelta = datetime.now() - start_time

            # 1. Ephemeral Failed Threshold
            if time_passed >= failed_threshold:
                await self._handle_ephemeral_failed(guild, user, settings)
                return

            # 2. Second Greeting Threshold
            elif time_passed >= second_greeting_threshold and not member_data["second_greeting_sent"]:
                await self._send_custom_message(
                    guild, 
                    user, 
                    settings["second_greeting_channel_id"], 
                    settings["second_greeting_message"],
                    time_passed=time_passed
                )
                await self.config.member(user).second_greeting_sent.set(True)

            # 3. First Greeting Threshold
            elif time_passed >= first_greeting_threshold and not member_data["first_greeting_sent"]:
                await self._send_custom_message(
                    guild, 
                    user, 
                    settings["first_greeting_channel_id"], 
                    settings["first_greeting_message"],
                    time_passed=time_passed
                )
                await self.config.member(user).first_greeting_sent.set(True)

    async def _handle_ephemeral_failed(self, guild: discord.Guild, user: discord.Member, settings: dict):
        """Handles the final 'Ephemeral Failed' state."""
        
        ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
        failed_role = guild.get_role(settings["ephemeral_failed_role_id"])

        # Remove Ephemeral role
        if ephemeral_role and ephemeral_role in user.roles:
            try:
                await user.remove_roles(ephemeral_role, reason="Ephemeral Failed: Timed out.")
            except discord.Forbidden:
                pass

        # Assign Ephemeral Failed role
        if failed_role:
            try:
                await user.add_roles(failed_role, reason="Ephemeral Failed: Timed out.")
            except discord.Forbidden:
                pass

        # Send failure message to configured channel
        await self._send_custom_message(
            guild,
            user,
            settings["failed_message_channel_id"],
            settings["failed_message"]
        )

        # Stop the timer and clear user data
        self.stop_user_timer(guild.id, user.id)
        await self.config.member(user).clear()

    # --- Listener for Message Counting ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listener to count valid messages from ephemeral users."""
        if message.author.bot or not message.guild:
            return

        member = message.author
        guild = message.guild
        
        member_data = await self.config.member(member).all()
        if not member_data["is_ephemeral"]:
            return

        settings = await self.config.guild(guild).all()
        
        # Check if message meets length threshold
        if len(message.content) < settings["message_length_threshold"]:
            return
        
        # Increment message count
        new_count = member_data["message_count"] + 1
        await self.config.member(member).message_count.set(new_count)

        # Check for role removal threshold
        if new_count >= settings["messages_threshold"]:
            ephemeral_role = guild.get_role(settings["ephemeral_role_id"])
            if ephemeral_role and ephemeral_role in member.roles:
                try:
                    await member.remove_roles(ephemeral_role, reason="Ephemeral message threshold met.")
                    
                    self.stop_user_timer(guild.id, member.id)
                    await self.config.member(member).clear()
                    
                    # Send removal message to configured channel
                    await self._send_custom_message(
                        guild,
                        member,
                        settings["removed_message_channel_id"],
                        settings["removed_message"]
                    )
                    
                except discord.Forbidden:
                    # Fallback if bot can't remove role
                    pass # We intentionally don't spam a channel error if the cog is configured right
            
    # --- Utility Command ---

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralstatus(self, ctx: commands.Context):
        """Displays all users currently in Ephemeral mode and their time remaining."""
        
        all_member_data = await self.config.all_members(ctx.guild)
        settings = await self.config.guild(ctx.guild).all()
        
        # The threshold is stored in seconds
        failed_threshold_seconds = settings["ephemeral_failed_threshold"]
        
        ephemeral_users = []
        
        for member_id, data in all_member_data.items():
            if data.get("is_ephemeral") and data.get("start_time"):
                
                member = ctx.guild.get_member(member_id)
                if not member:
                    # Cleanup data for users who left the guild
                    await self.config.member_from_id(member_id).clear()
                    self.stop_user_timer(ctx.guild.id, member_id)
                    continue

                start_time_ts = data["start_time"]
                
                # Calculate the expiration time (UNIX timestamp)
                expiration_ts = start_time_ts + failed_threshold_seconds
                
                # Format: Mention (Messages Sent/Threshold) | Expires (Relative)
                output_line = (
                    f"{member.mention} ({data['message_count']}/{settings['messages_threshold']}) | "
                    f"Expires: <t:{int(expiration_ts)}:R>"
                )
                ephemeral_users.append(output_line)

        if not ephemeral_users:
            await ctx.send("No users are currently in Ephemeral mode.")
            return

        # Prepare pages using Red's formatting utilities for potentially long lists
        pages = []
        # Use pagify to split the list of users into pages that fit within Discord's message limits
        for i, page in enumerate(pagify("\n".join(ephemeral_users), page_length=1000)):
            embed = discord.Embed(
                title=f"Ephemeral Users Status ({len(ephemeral_users)} active)",
                description=page,
                color=await ctx.embed_color()
            )
            # Add page numbering if multiple pages
            if len(ephemeral_users) > 20: 
                 embed.title = f"Ephemeral Users Status (Page {i+1})"

            pages.append(embed)
        
        if len(pages) > 1:
            await menu(ctx, pages, DEFAULT_CONTROLS)
        else:
            await ctx.send(embed=pages[0])


    # --- Configuration Commands ---

    @commands.group(invoke_without_command=True) # Changed: Now shows help when called without subcommand
    @checks.admin_or_permissions(manage_guild=True)
    async def ephemeralset(self, ctx: commands.Context):
        """Configures the Ephemeral cog settings."""
        # No action taken here. The default behaviour of a group command 
        # when invoked without subcommand and invoke_without_command=True is set 
        # is to display its help message (list of subcommands).
        pass

    @ephemeralset.command(name="view", aliases=["show"])
    async def ephemeralset_view(self, ctx: commands.Context):
        """Displays the current Ephemeral cog settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        e_role = ctx.guild.get_role(settings["ephemeral_role_id"])
        f_role = ctx.guild.get_role(settings["ephemeral_failed_role_id"])
        
        # Convert stored seconds back to timedelta for display
        failed_td = timedelta(seconds=settings['ephemeral_failed_threshold'])
        first_td = timedelta(seconds=settings['first_greeting_threshold'])
        second_td = timedelta(seconds=settings['second_greeting_threshold'])

        # Helper to get channel names
        def get_channel_info(cid):
            channel = ctx.guild.get_channel(cid)
            return f"#{channel.name}" if channel else "Not Set"
        
        output = [
            bold("Time/Message Thresholds:"),
            f"Ephemeral Failed Threshold: **{timedelta_to_human(failed_td)}**",
            f"Messages Threshold: **{settings['messages_threshold']}** messages",
            f"Message Length Threshold: **{settings['message_length_threshold']}** characters",
            "",
            bold("Role Configuration:"),
            f"Ephemeral Role: **{e_role.name if e_role else 'Not Set'}** ({settings['ephemeral_role_id'] or 'N/A'})",
            f"Ephemeral Failed Role: **{f_role.name if f_role else 'Not Set'}** ({settings['ephemeral_failed_role_id'] or 'N/A'})",
            "",
            bold("Greetings & Notifications:"),
            f"First Greeting Time: **{timedelta_to_human(first_td)}**",
            f"First Greeting Channel: **{get_channel_info(settings['first_greeting_channel_id'])}**",
            f"First Greeting Message: `{settings['first_greeting_message']}` (Use {{time_passed}}, {{mention}})",
            "",
            f"Second Greeting Time: **{timedelta_to_human(second_td)}**",
            f"Second Greeting Channel: **{get_channel_info(settings['second_greeting_channel_id'])}**",
            f"Second Greeting Message: `{settings['second_greeting_message']}` (Use {{time_passed}}, {{mention}})",
            "",
            f"Failed Message Channel: **{get_channel_info(settings['failed_message_channel_id'])}**",
            f"Failed Message: `{settings['failed_message']}` (Use {{mention}})",
            "",
            f"Removed Message Channel: **{get_channel_info(settings['removed_message_channel_id'])}**",
            f"Removed Message: `{settings['removed_message']}` (Use {{mention}})",
        ]
        
        await ctx.send(box('\n'.join(output)))

    @ephemeralset.command(name="failedtime")
    async def ephemeralset_failedtime(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours")):
        """
        Sets the Ephemeral Failed time threshold.
        
        Accepts time in minutes (m) or hours (h). e.g., `8h` or `480m`.
        """
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
            
        # Store as seconds
        await self.config.guild(ctx.guild).ephemeral_failed_threshold.set(time.total_seconds())
        await ctx.send(f"Ephemeral Failed threshold set to **{timedelta_to_human(time)}**.")

    @ephemeralset.command(name="messages")
    async def ephemeralset_messages(self, ctx: commands.Context, count: int):
        """Sets the number of messages threshold to remove the Ephemeral role."""
        if count <= 0:
            return await ctx.send("Message count must be a positive number.")
            
        await self.config.guild(ctx.guild).messages_threshold.set(count)
        await ctx.send(f"Message count threshold set to **{count}** messages.")

    @ephemeralset.command(name="messagelength")
    async def ephemeralset_messagelength(self, ctx: commands.Context, length: int):
        """Sets the minimum message length (characters) required for a message to count."""
        if length < 1:
            return await ctx.send("Message length must be at least 1 character.")
            
        await self.config.guild(ctx.guild).message_length_threshold.set(length)
        await ctx.send(f"Message length threshold set to **{length}** characters.")
        
    @ephemeralset.command(name="ephemeralrole")
    async def ephemeralset_ephemeralrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Ephemeral' role that users receive upon clicking the button."""
        await self.config.guild(ctx.guild).ephemeral_role_id.set(role.id)
        await ctx.send(f"Ephemeral role set to **{role.name}**.")

    @ephemeralset.command(name="failedrole")
    async def ephemeralset_failedrole(self, ctx: commands.Context, role: discord.Role):
        """Sets the 'Ephemeral Failed' role that users receive if they time out."""
        await self.config.guild(ctx.guild).ephemeral_failed_role_id.set(role.id)
        await ctx.send(f"Ephemeral Failed role set to **{role.name}**.")

    @ephemeralset.command(name="firstgreeting")
    async def ephemeralset_firstgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """
        Sets the First Greeting time threshold, target channel, and message.
        
        Time: e.g., `3h`.
        Message can include `{time_passed}` and `{mention}`.
        """
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
        
        # Store as seconds
        await self.config.guild(ctx.guild).first_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).first_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).first_greeting_message.set(message)
        await ctx.send(
            f"First Greeting set:\n"
            f"Threshold: **{timedelta_to_human(time)}**\n"
            f"Channel: {channel.mention}\n"
            f"Message: `{message}`"
        )

    @ephemeralset.command(name="secondgreeting")
    async def ephemeralset_secondgreeting(self, ctx: commands.Context, time: commands.TimedeltaConverter(default_unit="hours"), channel: discord.TextChannel, *, message: str):
        """
        Sets the Second Greeting time threshold, target channel, and message.
        
        Time: e.g., `5h`.
        Message can include `{time_passed}` and `{mention}`.
        """
        if time.total_seconds() <= 0:
            return await ctx.send("Time must be a positive duration.")
            
        # Store as seconds
        await self.config.guild(ctx.guild).second_greeting_threshold.set(time.total_seconds())
        await self.config.guild(ctx.guild).second_greeting_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).second_greeting_message.set(message)
        await ctx.send(
            f"Second Greeting set:\n"
            f"Threshold: **{timedelta_to_human(time)}**\n"
            f"Channel: {channel.mention}\n"
            f"Message: `{message}`"
        )
        
    @ephemeralset.command(name="failedmessage")
    async def ephemeralset_failedmessage(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """
        Sets the channel and message posted when the Ephemeral Failed timer expires.
        
        Message can include `{mention}`.
        """
        await self.config.guild(ctx.guild).failed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).failed_message.set(message)
        await ctx.send(
            f"Ephemeral Failed message set:\n"
            f"Channel: {channel.mention}\n"
            f"Message: `{message}`"
        )

    @ephemeralset.command(name="ephemeralremoved")
    async def ephemeralset_ephemeralremoved(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """
        Sets the channel and message posted when the message threshold is met.
        
        Message can include `{mention}`.
        """
        await self.config.guild(ctx.guild).removed_message_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).removed_message.set(message)
        await ctx.send(
            f"Ephemeral Removed message set:\n"
            f"Channel: {channel.mention}\n"
            f"Message: `{message}`"
        )

    @ephemeralset.command(name="embed")
    async def ephemeralset_embed(self, ctx: commands.Context, channel: discord.TextChannel, title: str, thumbnail_url: str, image_url: str, button_label: str, button_color: str, *, description: str):
        """
        Generates and posts the Ephemeral starting embed with the button.
        
        Parameters:
        [channel] The channel to post the embed in.
        [title] The title of the embed.
        [thumbnail_url] The URL for the embed's thumbnail. Use 'none' for no thumbnail.
        [image_url] The URL for the embed's main image. Use 'none' for no image.
        [button_label] The text on the button.
        [button_color] The color of the button (e.g., 'blue', 'green', 'red', 'grey/gray').
        [description] The description of the embed (must be the final argument).
        """
        
        button_style = BUTTON_COLOR_MAP.get(button_color.lower())
        if not button_style:
            return await ctx.send(f"Invalid button color. Must be one of: {humanize_list(list(BUTTON_COLOR_MAP.keys()))}")

        embed = discord.Embed(
            title=title, 
            description=description, 
            color=await ctx.embed_color()
        )
        
        if thumbnail_url.lower() != "none":
            embed.set_thumbnail(url=thumbnail_url)
            
        if image_url.lower() != "none":
            embed.set_image(url=image_url)

        # Create the View
        view = EphemeralButton(self)
        
        # Override the default button's properties using the components attribute
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "ephemeral:start_button":
                item.label = button_label
                item.style = button_style
                break
        else:
            return await ctx.send("Error: Could not find the persistent button component.")

        # Try to delete the old message if it exists
        settings = await self.config.guild(ctx.guild).all()
        old_msg_id = settings.get("embed_message_id")
        old_channel_id = settings.get("embed_channel_id")
        
        if old_msg_id and old_channel_id:
            old_channel = ctx.guild.get_channel(old_channel_id)
            if old_channel:
                try:
                    old_msg = await old_channel.fetch_message(old_msg_id)
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass # Ignore if not found or no permissions

        # Post the new message
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            return await ctx.send(f"I don't have permission to send messages in {channel.mention}.")

        # Save new configuration, including button details for persistence reference
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
        
        await ctx.send(f"Ephemeral embed posted successfully in {channel.mention}. The button is now active.")