import discord
from redbot.core import Config, commands
from redbot.core.commands import Context
from redbot.core import checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timezone
from typing import Union

# Define the default configuration structure for the cog
DEFAULT_GUILD = {
    "welcome_channel_id": None,
    # Store role ID for mention in the welcome message
    "welcome_role_id": None, 
    # Customizable message template for REJOINS (supports {user}, {role}, {count}, and {last_join_date})
    "welcome_message": "Welcome back, {user}! We're glad you're here for your {count} time. You were last here on {last_join_date}. Please check out {role} for next steps.",
    # Customizable message template for FIRST TIME JOINS (supports {user} and {role})
    "first_join_message": "Welcome, {user}! We are thrilled to have you here for the first time. Check out {role} to get started.",
    # Toggle for enabling/disabling welcome messages
    "welcome_enabled": True,
}
DEFAULT_MEMBER = {
    "rejoin_count": 0,
    "last_join_date": None,  # Timestamp of when they last joined
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

    async def get_join_count(self, guild: discord.Guild, user_id: int) -> int:
        """
        Public API to retrieve the number of times a user has joined a guild.
        
        Usage from another cog:
            cog = bot.get_cog("JoinTracker")
            count = await cog.get_join_count(guild, user_id)

        Args:
            guild (discord.Guild): The guild to query.
            user_id (int): The discord ID of the user.

        Returns:
            int: The number of times the user has joined. Returns 0 if no record exists.
        """
        member_config = self.config.member_from_ids(guild.id, user_id)
        data = await member_config.all()
        
        # If last_join_date is None, they haven't been tracked joining yet.
        if data["last_join_date"] is None:
            return 0
            
        # rejoin_count is 0 for the first join, so we add 1 for the total count
        return data["rejoin_count"] + 1

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
        
        # Determine if this is a first-time join
        is_first_join = member_data["last_join_date"] is None and member_data["rejoin_count"] == 0
        rejoin_count = member_data["rejoin_count"]

        # Capture the previous date BEFORE we overwrite it with the new join date
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
        # Check if messages are enabled in settings
        if not guild_settings["welcome_enabled"]:
            return

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
                        role_mention = role.mention
                    else:
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
                    
                    # Format the previous date for display
                    if previous_join_date_iso:
                        try:
                            prev_dt = datetime.fromisoformat(previous_join_date_iso)
                            prev_date_str = prev_dt.strftime("%Y-%m-%d")
                        except ValueError:
                            prev_date_str = "Unknown Date"
                    else:
                        prev_date_str = "Unknown Date"

                    template_vars = {
                        "user": member.mention,
                        "role": role_mention,
                        "count": count_display,
                        "last_join_date": prev_date_str
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
                
                allowed_mentions = discord.AllowedMentions(
                    users=True, 
                    roles=True,
                    everyone=False,
                )

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

    @jointracker.command(name="messagetoggle")
    async def jointracker_toggle(self, ctx: Context):
        """
        Toggles whether welcome messages are sent.
        
        When disabled, the bot will still track user join counts and dates, 
        but will not send welcome messages to the channel.
        """
        current_setting = await self.config.guild(ctx.guild).welcome_enabled()
        new_setting = not current_setting
        await self.config.guild(ctx.guild).welcome_enabled.set(new_setting)
        
        status = "enabled" if new_setting else "disabled"
        await ctx.send(f"Welcome messages have been **{status}**.")

    @jointracker.command(name="settings")
    async def jointracker_settings(self, ctx: Context):
        """
        Shows the current settings for JoinTracker.
        """
        guild_settings = await self.config.guild(ctx.guild).all()
        
        channel_id = guild_settings["welcome_channel_id"]
        role_id = guild_settings["welcome_role_id"]
        welcome_msg = guild_settings["welcome_message"]
        first_join_msg = guild_settings["first_join_message"]
        enabled = guild_settings["welcome_enabled"]

        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        role = ctx.guild.get_role(role_id) if role_id else None

        embed = discord.Embed(title=f"JoinTracker Settings for {ctx.guild.name}", color=await ctx.embed_color())
        
        embed.add_field(
            name="Messages Enabled",
            value="✅ Yes" if enabled else "❌ No",
            inline=True
        )

        embed.add_field(
            name="Welcome Channel", 
            value=channel.mention if channel else "Not Set", 
            inline=True
        )
        embed.add_field(
            name="Welcome Role", 
            value=role.mention if role else "Not Set", 
            inline=True
        )
        
        embed.add_field(name="\u200b", value="\u200b", inline=False) # Spacer

        embed.add_field(
            name="First Join Message", 
            value=f"```\n{first_join_msg}\n```", 
            inline=False
        )
        
        embed.add_field(
            name="Welcome Back Message", 
            value=f"```\n{welcome_msg}\n```", 
            inline=False
        )

        await ctx.send(embed=embed)

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
        Overrides the rejoin counter for a specific member or user ID.

        <target>: The member (mention) or user (ID) whose counter you want to change.
        <count>: The new number of times they have rejoined (e.g., 0 for a first-timer).
        
        This works even if the user is not currently in the server.
        """
        if count < 0:
            return await ctx.send("The rejoin count must be zero or a positive number.")

        # Use member_from_ids to explicitly scope to the guild ID and member ID.
        # This handles both Member (in-server) and User (out-of-server) objects correctly
        # without triggering AttributeError (missing guild) or TypeError (arg count).
        config_member = self.config.member_from_ids(ctx.guild.id, target.id)

        # 1. Set the rejoin count
        await config_member.rejoin_count.set(count)
        
        # 2. Update the last_join_date if they are currently a member
        if isinstance(target, discord.Member):
             join_date_iso = target.joined_at.astimezone(timezone.utc).isoformat()
             await config_member.last_join_date.set(join_date_iso)
        else:
             # If target is only a User (not in guild), we set last_join_date to None.
             # It will be populated when they actually join the server next time.
             await config_member.last_join_date.set(None)
             
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
        
        # Get all members' config data in one go to minimize database calls
        all_member_data = await self.config.all_members(guild)

        for member in guild.members:
            if member.bot:
                continue

            member_id_str = str(member.id)
            
            # Check if data exists for this member
            # We treat missing data OR missing 'last_join_date' as a candidate for population
            if member_id_str not in all_member_data or all_member_data[member_id_str].get("last_join_date") is None:
                # Populate the initial join date based on their current discord join date
                join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
                
                # Set rejoin_count to 0 (meaning 1 total join)
                await self.config.member(member).rejoin_count.set(0)
                await self.config.member(member).last_join_date.set(join_date_iso)
                
                members_updated += 1

        await ctx.send(
            f"Successfully checked and initialized tracking data for **{members_updated}** untracked members."
        )

    @jointracker.command(name="info")
    async def jointracker_info(self, ctx: Context, member: discord.Member = None):
        """Shows the join/rejoin info for a member (defaults to you)."""
        member = member or ctx.author
        
        member_data = await self.config.member(member).all()
        rejoin_count = member_data["rejoin_count"]
        last_join_date_iso = member_data["last_join_date"]
        
        # Determine the effective number of times they have been here
        times_here = rejoin_count + 1
        
        if last_join_date_iso:
            # Parse the stored ISO date
            last_join_date = datetime.fromisoformat(last_join_date_iso).strftime('%Y-%m-%d %H:%M:%S UTC')
        else:
            # Fallback to current discord.py data if not yet populated
            last_join_date = member.joined_at.strftime('%Y-%m-%d %H:%M:%S UTC')
            
        embed = discord.Embed(
            title=f"Join/Rejoin History for {member.display_name}",
            color=member.color if member.color != discord.Color.default() else discord.Color.blue()
        )
        
        embed.add_field(
            name="Times in Server",
            value=f"**{times_here}** time{'s' if times_here > 1 else ''} total.",
            inline=False
        )
        
        embed.add_field(
            name="Last Joined",
            value=last_join_date,
            inline=False
        )
        
        await ctx.send(embed=embed)

    @jointracker.command(name="list")
    async def jointracker_list(self, ctx: Context):
        """
        Generates a paginated table of all recorded user join/rejoin history.
        """
        await ctx.defer()
        guild = ctx.guild
        all_member_data = await self.config.all_members(guild)
        
        if not all_member_data:
            return await ctx.send("No join tracking data found for this server.")

        data_rows = []
        
        # Define padding for columns
        USER_ID_PAD = 18
        USERNAME_PAD = 20
        JOINS_PAD = 5
        DATE_PAD = 12
        
        # Create header and separator
        header_text = "{:<{uid}} | {:<{un}} | {:<{joins}} | {:<{date}}".format(
            "USER ID", "USERNAME", "JOINS", "LAST JOINED",
            uid=USER_ID_PAD, un=USERNAME_PAD, joins=JOINS_PAD, date=DATE_PAD
        )
        separator_text = "-" * len(header_text)
        
        for user_id_str, data in all_member_data.items():
            user_id = int(user_id_str)
            
            # --- Get Username ---
            user = self.bot.get_user(user_id)
            if user:
                username = user.name
            else:
                # If user is not cached (e.g. left server), try to fetch member? 
                # unlikely if they left. fallback to "?"
                username = "?"
            
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

        # --- Dynamic Pagination Logic ---
        # Discord message limit is 2000. We use 1900 to safely account for wrappers (` ``` ` and page title).
        MAX_MESSAGE_LENGTH = 1900 
        current_page_content = [header_text, separator_text]
        page_number = 1
        
        async def send_page(content_list, page_num):
            message_content = "\n".join(content_list)
            if message_content.strip():
                await ctx.send(
                    f"**Join Tracker Report (Page {page_num})**\n"
                    f"```{message_content}```"
                )

        for row in data_rows:
            # Check length: current content + new row + newline
            # We calculate what the string length WOULD be if we added this row
            potential_content = "\n".join(current_page_content + [row])
            
            if len(potential_content) > MAX_MESSAGE_LENGTH:
                # If adding the new row exceeds the limit, send the current page
                await send_page(current_page_content, page_number)
                
                # Start a new page with the header and the row that caused the overflow
                page_number += 1
                current_page_content = [header_text, separator_text, row]
            else:
                # Otherwise, add the row to the current page
                current_page_content.append(row)

        # Send the final, remaining page if it has data
        if len(current_page_content) > 2: # > 2 means we have more than just header+separator
            await send_page(current_page_content, page_number)