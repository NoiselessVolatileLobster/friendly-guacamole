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

        # Set the rejoin count using the member scope (tied to the guild ID)
        await self.config.member(target).rejoin_count.set(count)
        
        # Update the last_join_date. If they are currently a member, use their current join date.
        if isinstance(target, discord.Member):
             join_date_iso = target.joined_at.astimezone(timezone.utc).isoformat()
             await self.config.member(target).last_join_date.set(join_date_iso)
        else:
             # If target is only a User (not in guild), we set last_join_date to None 
             # and rely on the next join event to populate it.
             await self.config.member(target).last_join_date.set(None)
             
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
        
        # Field 1: Times in Server
        embed.add_field(
            name="Times in Server",
            value=f"**{times_here}** time{'s' if times_here > 1 else ''} total.",
            inline=False
        )
        
        # Field 2: Last Joined
        embed.add_field(
            name="Last Joined",
            value=last_join_date,
            inline=False
        )
        
        await ctx.send(embed=embed)