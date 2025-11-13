import discord
from redbot.core import Config, commands
from redbot.core.commands import Context
from redbot.core import checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timezone

# Define the default configuration structure for the cog
DEFAULT_GUILD = {
    "welcome_channel_id": None,
    # New: Store role ID for mention in the welcome message
    "welcome_role_id": None, 
    # New: Customizable message template with {user}, {role}, and {count} variables
    "welcome_message": "Welcome back, {user}! We're glad you're here for your {count} time. Please check out {role} for next steps.",
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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new members joining the guild."""
        if member.bot:
            return

        guild = member.guild
        member_data = await self.config.member(member).all()
        guild_settings = await self.config.guild(guild).all() # Fetch all guild settings
        
        # 1. Update/Calculate Rejoin Count
        rejoin_count = member_data["rejoin_count"]
        
        # Check if they have been here before
        if member_data["last_join_date"] is not None:
            # They are rejoining, increment the counter
            rejoin_count += 1
            await self.config.member(member).rejoin_count.set(rejoin_count)

        # 2. Store the current join date
        join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
        await self.config.member(member).last_join_date.set(join_date_iso)
        
        # 3. Send "Welcome Back" message if applicable
        channel_id = guild_settings["welcome_channel_id"]

        if rejoin_count > 0 and channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                
                # Fetch customization settings
                welcome_msg_template = guild_settings["welcome_message"]
                role_id = guild_settings["welcome_role_id"]
                
                # Prepare role mention
                role_mention = ""
                if role_id:
                    role = guild.get_role(role_id)
                    # Use role mention if role exists, otherwise use a plain string name
                    role_mention = role.mention if role else "the specified role"

                # Prepare count display (rejoin_count + 1 is the total times here)
                count_display = rejoin_count + 1
                
                # Format the message using the template variables
                try:
                    formatted_message = welcome_msg_template.format(
                        user=member.mention,
                        role=role_mention,
                        count=count_display
                    )
                except KeyError:
                    # Fallback message if the template is broken or variables are missing
                    formatted_message = (
                        f"Welcome back, {member.mention}! This is your {count_display} time "
                        f"joining the server. (Error formatting custom message.)"
                    )

                # Send the customized message
                await channel.send(formatted_message)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Handle members leaving. No explicit action is needed here as the join date 
        is stored on join, which is what we check for when they rejoin.
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
        Sets the channel where "Welcome back" messages are sent.
        
        If no channel is provided, the current setting will be cleared.
        """
        if channel:
            await self.config.guild(ctx.guild).welcome_channel_id.set(channel.id)
            await ctx.send(f"The welcome back channel has been set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).welcome_channel_id.set(None)
            await ctx.send("The welcome back channel has been cleared. No automated welcome messages will be sent.")

    @jointracker.command(name="setwelcomerole")
    async def jointracker_setwelcomerole(self, ctx: Context, role: discord.Role = None):
        """
        Sets the role to be mentioned using the {role} variable in the welcome message.
        
        If no role is provided, the current setting will be cleared.
        """
        if role:
            await self.config.guild(ctx.guild).welcome_role_id.set(role.id)
            await ctx.send(f"The welcome role to mention has been set to **{role.name}**.")
        else:
            await self.config.guild(ctx.guild).welcome_role_id.set(None)
            await ctx.send("The welcome role mention has been cleared.")

    @jointracker.command(name="setwelcomemsg")
    async def jointracker_setwelcomemsg(self, ctx: Context, *, message: str):
        """
        Sets the custom "Welcome back" message template.
        
        Use the following variables:
        - {user}: The member mention.
        - {role}: The mention of the configured welcome role.
        - {count}: The total number of times the user has joined (e.g., 2, 3, 4...).
        
        Example: [p]jointracker setwelcomemsg Welcome back, {user}! The {role} team missed you!
        """
        await self.config.guild(ctx.guild).welcome_message.set(message)
        await ctx.send(
            "The custom welcome message template has been set to:\n"
            f"```\n{message}\n```\n"
            "Ensure you use `{user}`, `{role}`, and `{count}` for dynamic content."
        )

    @jointracker.command(name="setrejoins")
    async def jointracker_setrejoins(self, ctx: Context, member: discord.Member, count: int):
        """
        Overrides the rejoin counter for a specific member.

        <member>: The member whose counter you want to change.
        <count>: The new number of times they have rejoined (e.g., 0 for a first-timer).
        """
        if count < 0:
            return await ctx.send("The rejoin count must be zero or a positive number.")

        # The count stored is the number of times they have rejoined *after* their first time.
        # e.g., count 0 = first time, count 1 = rejoined once (second time total).
        await self.config.member(member).rejoin_count.set(count)
        
        # Update the last_join_date to the member's current join date as well for consistency
        join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
        await self.config.member(member).last_join_date.set(join_date_iso)
        
        await ctx.send(
            f"Successfully set the rejoin counter for {member.display_name} to **{count}**."
        )

    @jointracker.command(name="populate")
    async def jointracker_populate(self, ctx: Context):
        """
        Populates all current members' join dates into the database.

        This is useful for initializing the cog on an existing server. It only runs
        if a member's data is completely missing.
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
            # We look for the last_join_date field, which is the primary indicator of presence
            member_id_str = str(member.id)
            
            # If the member ID is not in our data structure, or the date is missing/None
            if member_id_str not in all_member_data or all_member_data[member_id_str].get("last_join_date") is None:
                # Populate the initial join date (which will be the date they joined the server)
                join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
                
                # We use the raw config access for efficiency
                await self.config.member(member).last_join_date.set(join_date_iso)
                
                # Ensure rejoin_count is 0 if populating for the first time
                await self.config.member(member).rejoin_count.set(0)
                
                members_updated += 1

        await ctx.send(
            f"Successfully checked and populated join dates for **{members_updated}** members."
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
