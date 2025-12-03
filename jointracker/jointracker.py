import discord
from redbot.core import Config, commands
from redbot.core.commands import Context
from redbot.core import checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timezone
from typing import Union # Required for Union[discord.Member, discord.User]

# Define the default configuration structure for the cog
DEFAULT_GUILD = {
    "welcome_channel_id": None,
    # Store role ID for mention in the welcome message
    "welcome_role_id": None, 
    # Customizable message template for REJOINS (supports {user}, {role}, {count}, and {last_join_date})
    "welcome_message": "Welcome back, {user}! We're glad you're here for your {count} time. You were last here on {last_join_date}. Please check out {role} for next steps.",
    # Customizable message template for FIRST TIME JOINS (supports {user} and {role})
    "first_join_message": "Welcome, {user}! We are thrilled to have you here for the first time. Check out {role} to get started.",
}
DEFAULT_MEMBER = {
    "rejoin_count": 0,
    "last_join_date": None,  # Timestamp of when they last joined the server
}

class JoinTracker(commands.Cog):
    """
    Tracks member join dates, calculates rejoin counts, and provides customizable welcome messages.
    """

    def __init__(self, bot):
        self.bot = bot
        # Initialize configuration using Red's Config system
        self.config = Config.get_conf(self, identifier=148008422401290145, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_member(**DEFAULT_MEMBER)

    def _get_ordinal(self, n: int) -> str:
        """Converts an integer to its ordinal string representation (1 -> 1st, 2 -> 2nd, etc.)."""
        if 10 <= n % 100 <= 20:
            return str(n) + 'th'
        else:
            return str(n) + {1 : 'st', 2 : 'nd', 3 : 'rd'}.get(n % 10, 'th')

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new members joining the guild."""
        if member.bot:
            return

        guild = member.guild
        member_data = await self.config.member(member).all()
        guild_settings = await self.config.guild(guild).all() # Fetch all guild settings
        
        # Determine if this is a first-time join (no previous join date stored AND rejoin count is 0)
        is_first_join = member_data["last_join_date"] is None and member_data["rejoin_count"] == 0
        rejoin_count = member_data["rejoin_count"]

        # Store the old last_join_date before updating it
        previous_join_date_iso = member_data["last_join_date"]

        # 1. Update/Calculate Rejoin Count
        if not is_first_join:
            # They are rejoining, increment the counter
            rejoin_count += 1
            await self.config.member(member).rejoin_count.set(rejoin_count)

        # 2. Store the current join date
        join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
        await self.config.member(member).last_join_date.set(join_date_iso)
        
        # 3. Send Welcome Message
        channel_id = guild_settings["welcome_channel_id"]

        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                
                role_id = guild_settings["welcome_role_id"]
                
                # Prepare role mention
                role_mention = ""
                if role_id:
                    role = guild.get_role(role_id)
                    
                    if role:
                        # Role exists: use the proper mention
                        role_mention = role.mention
                    else:
                        # Role is missing: Fallback to the raw mention string format 
                        role_mention = f"<@&{role_id}>"

                if is_first_join:
                    # Case A: First Time Join
                    msg_template = guild_settings["first_join_message"]
                    template_vars = {
                        "user": member.mention,
                        "role": role_mention,
                    }
                else:
                    # Case B: Rejoin
                    msg_template = guild_settings["welcome_message"]
                    
                    # Calculate total times here (rejoin_count + 1 is the total times here)
                    count_int = rejoin_count + 1 
                    count_display = self._get_ordinal(count_int)
                    
                    # Prepare the previous join date for the message
                    if previous_join_date_iso:
                        # Format the previous join date (only show date for brevity in message)
                        prev_date = datetime.fromisoformat(previous_join_date_iso).strftime('%Y-%m-%d')
                    else:
                        prev_date = "an unknown date"
                        
                    template_vars = {
                        "user": member.mention,
                        "role": role_mention,
                        "count": count_display,
                        "last_join_date": prev_date, # New variable
                    }

                # Format the message using the template variables
                try:
                    formatted_message = msg_template.format(**template_vars)
                except KeyError:
                    # Fallback message if the template is broken or variables are missing
                    if is_first_join:
                        formatted_message = f"Welcome, {member.mention}! (Error formatting custom first-join message.)"
                    else:
                        formatted_message = (
                            f"Welcome back, {member.mention}! This is your {rejoin_count + 1} time "
                            f"joining the server. (Error formatting custom rejoin message.)"
                        )
                
                # Explicitly allow mentions
                allowed_mentions = discord.AllowedMentions(
                    users=True, 
                    roles=True,
                    everyone=False,
                )

                # Send the customized message with explicit allowed mentions
                await channel.send(formatted_message, allowed_mentions=allowed_mentions)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Handle members leaving. No explicit action is needed here.
        """
        if member.bot:
            return
        pass
        
    @commands.group(name="jointracker", aliases=["jt"])
    @checks.admin_or_permissions(manage_guild=True)
    async def jointracker(self, ctx: Context):
        """Manage the member join tracking settings."""
        pass

    @jointracker.command(name="setchannel")
    async def jointracker_setchannel(self, ctx: Context, channel: discord.TextChannel = None):
        """
        Sets the channel where "Welcome" messages are sent.
        
        If no channel is provided, the current setting will be cleared.
        """
        if channel:
            await self.config.guild(ctx.guild).welcome_channel_id.set(channel.id)
            await ctx.send(f"The welcome channel has been set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).welcome_channel_id.set(None)
            await ctx.send("The welcome channel has been cleared. No automated welcome messages will be sent.")

    @jointracker.command(name="setwelcomerole")
    async def jointracker_setwelcomerole(self, ctx: Context, role: discord.Role = None):
        """
        Sets the role to be mentioned using the {role} variable in welcome messages.
        
        If no role is provided, the current setting will be cleared.
        """
        if role:
            await self.config.guild(ctx.guild).welcome_role_id.set(role.id)
            await ctx.send(f"The welcome role to mention has been set to **{role.name}**.")
        else:
            await self.config.guild(ctx.guild).welcome_role_id.set(None)
            await ctx.send("The welcome role mention has been cleared.")

    @jointracker.command(name="setfirstjoinmsg")
    async def jointracker_setfirstjoinmsg(self, ctx: Context, *, message: str):
        """
        Sets the custom "First time join" message template.
        
        Use the following variables:
        - {user}: The member mention.
        - {role}: The mention of the configured welcome role.
        
        Example: [p]jointracker setfirstjoinmsg Hello {user}! Check out {role}.
        """
        await self.config.guild(ctx.guild).first_join_message.set(message)
        await ctx.send(
            "The custom **first time join** message template has been set to:\n"
            f"```\n{message}\n```\n"
            "Ensure you use `{user}` and `{role}` for dynamic content."
        )

    @jointracker.command(name="setwelcomemsg")
    async def jointracker_setwelcomemsg(self, ctx: Context, *, message: str):
        """
        Sets the custom "Welcome back" message template for returning users.
        
        Use the following variables:
        - {user}: The member mention.
        - {role}: The mention of the configured welcome role.
        - {count}: The total number of times the user has joined (e.g., 2nd, 3rd, 4th...).
        - {last_join_date}: The date (YYYY-MM-DD) the user was last seen in the server.
        
        Example: [p]jointracker setwelcomemsg Welcome back, {user}! You were last here on {last_join_date}!
        """
        await self.config.guild(ctx.guild).welcome_message.set(message)
        await ctx.send(
            "The custom **welcome back** message template has been set to:\n"
            f"```\n{message}\n```\n"
            "Ensure you use `{user}`, `{role}`, `{count}`, and `{last_join_date}` for dynamic content."
        )

    @jointracker.command(name="setrejoins")
    async def jointracker_setrejoins(self, ctx: Context, target: Union[discord.Member, discord.User], count: int):
        """
        Overrides the rejoin counter for a specific member/user.

        <target>: The member (mention/ID) or user (ID) whose counter you want to change.
        <count>: The new number of times they have rejoined (e.g., 0 for a first-timer).

        This works even if the user is not currently in the server.
        """
        if count < 0:
            return await ctx.send("The rejoin count must be zero or a positive number.")

        # --- FINAL FIX: Use the correct positional argument structure for Config.member() ---
        
        if isinstance(target, discord.Member):
            # 1. If it's a Member object, use the object directly (Guild context is implicit).
            config_member = self.config.member(target)
            
            # Use their current join date
            join_date_iso = target.joined_at.astimezone(timezone.utc).isoformat()
            await config_member.last_join_date.set(join_date_iso)
            
        elif isinstance(target, discord.User):
            # 2. If it's a User object, we MUST pass the Guild object as the second positional argument.
            # This satisfies the requirement for guild context without using the rejected 'guild=' keyword.
            config_member = self.config.member(target, ctx.guild)
            
            # Clear/set last_join_date to None since they are not currently in the server
            await config_member.last_join_date.set(None)
            
        else:
            return await ctx.send("Could not determine the target user type.")

        # 3. Set the rejoin count (Common to both branches)
        await config_member.rejoin_count.set(count)
             
        await ctx.send(
            f"Successfully set the rejoin counter for {target.display_name} (ID: {target.id}) to **{count}**."
        )

    @jointracker.command(name="populate")
    async def jointracker_populate(self, ctx: Context):
        """
        Sets the initial join count to 1 for all members currently in the server 
        that do not have existing tracking data.
        """
        await ctx.defer()
        guild = ctx.guild
        members_updated = 0
        
        # Get all members' config data in one go
        all_member_data = await self.config.all_members(guild)

        for member in guild.members:
            if member.bot:
                continue

            # Check if we have join date data for this member
            # If the member ID is not in our data structure, or the date is missing/None
            member_id_str = str(member.id)
            if member_id_str not in all_member_data or all_member_data[member_id_str].get("last_join_date") is None:
                # Populate the initial join date (which will be the date they joined the server)
                join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
                
                # Set rejoin_count to 0 (meaning 1 total join)
                await self.config.member(member).rejoin_count.set(0)
                await self.config.member(member).last_join_date.set(join_date_iso)
                
                members_updated += 1

        await ctx.send(
            f"Successfully checked and initialized tracking data for **{members_updated}** untracked members."
        )

    @jointracker.command(name="list")
    async def jointracker_list(self, ctx: Context):
        """
        Generates a table of all recorded user join/rejoin history, paginating if needed.
        
        Displays User ID, Username, Number of join times, and Last joined date.
        """
        await ctx.defer()
        guild = ctx.guild
        all_member_data = await self.config.all_members(guild)
        
        data_rows = []
        
        # Define padding for columns
        USER_ID_PAD = 18
        USERNAME_PAD = 20
        JOINS_PAD = 5
        DATE_PAD = 10
        
        # Create header and separator
        header_text = "{:<{uid}} | {:<{un}} | {:<{joins}} | {:<{date}}".format(
            "USER ID", "USERNAME", "JOINS", "LAST JOINED",
            uid=USER_ID_PAD, un=USERNAME_PAD, joins=JOINS_PAD, date=DATE_PAD
        )
        separator_text = "-" * len(header_text)
        
        # Gather all data rows
        for user_id_str, data in all_member_data.items():
            user_id = int(user_id_str)
            
            # --- Get Username ---
            user = self.bot.get_user(user_id)
            username = user.name if user else '?'
            
            # Truncate username if too long for clean display
            if len(username) > USERNAME_PAD:
                username = username[:USERNAME_PAD - 3] + '...'
            
            # --- Calculate Joins ---
            rejoin_count = data.get("rejoin_count", 0)
            times_here = rejoin_count + 1
            count_display = str(times_here)
            
            # --- Get Last Joined Date ---
            last_join_date_iso = data.get("last_join_date")
            if last_join_date_iso:
                try:
                    # Format the date to YYYY-MM-DD
                    date_display = datetime.fromisoformat(last_join_date_iso).strftime('%Y-%m-%d')
                except ValueError:
                    date_display = '?'
            else:
                date_display = '?'
                
            # Create the data row
            row = "{:<{uid}} | {:<{un}} | {:<{joins}} | {:<{date}}".format(
                user_id_str, username, count_display, date_display,
                uid=USER_ID_PAD, un=USERNAME_PAD, joins=JOINS_PAD, date=DATE_PAD
            )
            data_rows.append(row)


        if not data_rows:
            return await ctx.send("No user join history records found for this server.")

        # --- Dynamic Pagination Logic ---
        # Discord message limit is 2000. We use 1900 to safely account for wrappers (` ``` ` and page title).
        MAX_MESSAGE_LENGTH = 1900 
        current_page_content = [header_text, separator_text]
        page_number = 1
        
        async def send_page(content_list, page_num):
            message_content = "\n".join(content_list)
            # Send the current page with title and code block wrapper
            await ctx.send(
                f"**Join Tracker Report (Page {page_num})**\n"
                f"```{message_content}```"
            )

        for row in data_rows:
            # Check length: current content + new row + newline
            test_content = "\n".join(current_page_content + [row])
            
            if len(test_content) > MAX_MESSAGE_LENGTH:
                # If adding the new row exceeds the limit, send the current page
                await send_page(current_page_content, page_number)
                
                # Start a new page with the header and the row that caused the overflow
                page_number += 1
                current_page_content = [header_text, separator_text, row]
            else:
                # Otherwise, add the row to the current page
                current_page_content.append(row)

        # Send the final, remaining page
        if len(current_page_content) > 2: # Check if there is data beyond the header/separator
            await send_page(current_page_content, page_number)