import discord
from redbot.core import Config, commands
from redbot.core.commands import Context
from redbot.core import checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timezone

# Define the default configuration structure for the cog
DEFAULT_GUILD = {
    "welcome_channel_id": None,
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
        
        # 1. Update/Calculate Rejoin Count
        rejoin_count = member_data["rejoin_count"]
        
        # Check if they have been here before (based on whether we have stored a last join date)
        # Note: on_member_remove stores the join date right before leaving.
        if member_data["last_join_date"] is not None:
            # They are rejoining, increment the counter
            rejoin_count += 1
            await self.config.member(member).rejoin_count.set(rejoin_count)

        # 2. Store the current join date
        join_date_iso = member.joined_at.astimezone(timezone.utc).isoformat()
        await self.config.member(member).last_join_date.set(join_date_iso)
        
        # 3. Send "Welcome Back" message if applicable
        channel_id = await self.config.guild(guild).welcome_channel_id()

        if rejoin_count > 0 and channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                if rejoin_count == 1:
                    await channel.send(
                        f"Welcome back, {member.mention}! This is your second time joining the server. "
                        f"We're glad to have you again."
                    )
                else:
                    await channel.send(
                        f"Welcome back, {member.mention}! This is your **{rejoin_count + 1}** time "
                        f"joining the server! We're glad to have you again."
                    )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Handle members leaving. We store the current join date (which is before they leave)
        to indicate they were here, so the next join counts as a rejoin.
        """
        if member.bot:
            return
            
        # We don't need to increment the counter here; we check for a stored date on join.
        # We just need to make sure the last_join_date is recorded for the *next* time they join.
        # Since on_member_join already stores it, no explicit action is needed here 
        # unless we want to record the *leave* date, but the requirement is just to track the count.
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
            
        channel_id = await self.config.guild(ctx.guild).welcome_channel_id()
        welcome_channel = ctx.guild.get_channel(channel_id) if channel_id else "Not set"

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
            name="Rejoin Count Override",
            value=f"{rejoin_count} time{'s' if rejoin_count > 1 else ''} (The number used for 'Welcome Back' message logic)",
            inline=False
        )

        embed.add_field(
            name="Last Joined",
            value=last_join_date,
            inline=False
        )

        embed.add_field(
            name="Welcome Channel",
            value=welcome_channel.mention if isinstance(welcome_channel, discord.TextChannel) else welcome_channel,
            inline=False
        )
        
        await ctx.send(embed=embed)