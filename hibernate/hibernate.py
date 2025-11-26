import discord
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import humanize_list, humanize_timedelta
from redbot.core.utils.predicates import MessagePredicate

class Hibernate(commands.Cog):
    """
    Allow users to 'hibernate' by self-assigning a temporary role.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98123749812, force_registration=True)

        # Default Global/Guild Settings
        default_guild = {
            "target_role_id": None,
            "min_days_joined": 0,
            "required_role_ids": [],
            "duration_days": 30
        }

        # Default Member Settings
        default_member = {
            "hibernation_end": None  # Timestamp
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        # Start the background loop
        self.bg_loop = self.bot.loop.create_task(self.check_hibernations())

    def cog_unload(self):
        if self.bg_loop:
            self.bg_loop.cancel()

    async def check_hibernations(self):
        """Background task to remove roles from users whose hibernation has expired."""
        await self.bot.wait_until_ready()
        while True:
            try:
                # Check every hour
                await asyncio.sleep(3600)
                
                now = datetime.now(timezone.utc).timestamp()
                all_guilds = await self.config.all_guilds()

                for guild_id, guild_data in all_guilds.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    target_role_id = guild_data["target_role_id"]
                    if not target_role_id:
                        continue
                    
                    target_role = guild.get_role(target_role_id)
                    if not target_role:
                        continue

                    # Get all members in this guild with data
                    all_members = await self.config.all_members(guild)
                    
                    for member_id, member_data in all_members.items():
                        end_time = member_data.get("hibernation_end")
                        
                        if end_time and now >= end_time:
                            # Hibernation expired
                            member = guild.get_member(member_id)
                            
                            # Clean up config
                            await self.config.member_from_ids(guild_id, member_id).hibernation_end.set(None)

                            if member:
                                try:
                                    await member.remove_roles(target_role, reason="Hibernation expired.")
                                except discord.Forbidden:
                                    print(f"Failed to remove hibernation role in guild {guild.name}: Missing Permissions")
                                except discord.HTTPException:
                                    pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in Hibernate loop: {e}")

    @commands.hybrid_command(name="hibernate", description="Self-assign the hibernation role for a set period.")
    @commands.guild_only()
    async def hibernate(self, ctx: commands.Context):
        """
        Request to enter hibernation. 
        Checks eligibility based on server join date and required roles.
        """
        guild = ctx.guild
        member = ctx.author

        # Fetch settings
        settings = await self.config.guild(guild).all()
        target_role_id = settings["target_role_id"]
        min_days = settings["min_days_joined"]
        req_role_ids = settings["required_role_ids"]
        duration = settings["duration_days"]

        # 1. Configuration Check
        if not target_role_id:
            embed = discord.Embed(title="Error", description="Hibernation has not been configured by admins yet.", color=discord.Color.red())
            return await ctx.send(embed=embed, ephemeral=True)

        target_role = guild.get_role(target_role_id)
        if not target_role:
            embed = discord.Embed(title="Error", description="The configured hibernation role no longer exists.", color=discord.Color.red())
            return await ctx.send(embed=embed, ephemeral=True)

        # 2. Check if already hibernating
        if target_role in member.roles:
            current_end = await self.config.member(member).hibernation_end()
            desc = "You are already hibernating."
            if current_end:
                 desc += f"\nEnds: <t:{int(current_end)}:R>"
            
            embed = discord.Embed(title="Already Active", description=desc, color=discord.Color.orange())
            return await ctx.send(embed=embed, ephemeral=True)

        # 3. Eligibility Checks
        reasons = []
        
        # Check Join Date
        if min_days > 0:
            # handle case where joined_at might be None (API edge case)
            if member.joined_at:
                # Ensure comparison is done with aware datetimes
                joined_at_utc = member.joined_at.replace(tzinfo=timezone.utc) if member.joined_at.tzinfo is None else member.joined_at
                days_joined = (datetime.now(timezone.utc) - joined_at_utc).days
                if days_joined < min_days:
                    reasons.append(f"• You have not been a member long enough. (Required: {min_days} days, You: {days_joined} days)")
            else:
                reasons.append("• Could not determine your join date.")

        # Check Required Roles (Must have ANY)
        if req_role_ids:
            member_role_ids = {r.id for r in member.roles}
            
            # Check if the set of required IDs has any intersection with the set of member's role IDs
            # This implements the "Must have ANY" logic
            has_required_role = bool(set(req_role_ids) & member_role_ids)
            
            if not has_required_role:
                # Prepare error message
                required_role_names = [r.name for rid in req_role_ids if (r := guild.get_role(rid))]
                
                if required_role_names:
                    reasons.append(f"• You must have at least one of these roles to be eligible: {humanize_list(required_role_names)}")
                else:
                    reasons.append("• Required roles are configured, but I couldn't verify your eligibility as none of the roles are available in this server. Please contact an admin.")


        # If Failed Checks
        if reasons:
            embed = discord.Embed(title="Eligibility Failed", description="You cannot hibernate at this time:", color=discord.Color.red())
            for reason in reasons:
                embed.add_field(name="Reason", value=reason, inline=False)
            return await ctx.send(embed=embed, ephemeral=True)

        # 4. Apply Hibernation
        try:
            await member.add_roles(target_role, reason="User requested hibernation.")
        except discord.Forbidden:
            embed = discord.Embed(title="Error", description="I do not have permission to assign the hibernation role. Please contact an admin.", color=discord.Color.red())
            return await ctx.send(embed=embed, ephemeral=True)

        # Calculate End Date
        end_date = datetime.now(timezone.utc) + timedelta(days=duration)
        await self.config.member(member).hibernation_end.set(end_date.timestamp())

        embed = discord.Embed(
            title="Hibernation Active", 
            description=f"You are now hibernating.\nRole **{target_role.name}** has been applied.\n\nEnds: <t:{int(end_date.timestamp())}:F>", 
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- Admin Configuration ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def hibernateset(self, ctx):
        """Configuration for user hibernation."""
        pass

    @hibernateset.command(name="role")
    async def set_role(self, ctx, role: discord.Role):
        """Set the role that will be assigned for hibernation."""
        await self.config.guild(ctx.guild).target_role_id.set(role.id)
        await ctx.send(f"Hibernate role set to: {role.mention}")

    @hibernateset.command(name="duration")
    async def set_duration(self, ctx, days: int):
        """Set how many days the hibernation lasts."""
        if days < 1:
            return await ctx.send("Duration must be at least 1 day.")
        await self.config.guild(ctx.guild).duration_days.set(days)
        await ctx.send(f"Hibernate duration set to: {days} days.")

    @hibernateset.command(name="joindays")
    async def set_join_days(self, ctx, days: int):
        """Set minimum days a user must be in the server to hibernate."""
        if days < 0:
            return await ctx.send("Days cannot be negative.")
        await self.config.guild(ctx.guild).min_days_joined.set(days)
        await ctx.send(f"Minimum join days set to: {days} days.")

    @hibernateset.command(name="addreqrole")
    async def add_req_role(self, ctx, role: discord.Role):
        """Add a role to the list of required roles to be eligible."""
        async with self.config.guild(ctx.guild).required_role_ids() as roles:
            if role.id in roles:
                return await ctx.send("That role is already required.")
            roles.append(role.id)
        await ctx.send(f"Added {role.name} to required roles.")

    @hibernateset.command(name="removereqrole")
    async def remove_req_role(self, ctx, role: discord.Role):
        """Remove a role from the list of required roles."""
        async with self.config.guild(ctx.guild).required_role_ids() as roles:
            if role.id not in roles:
                return await ctx.send("That role is not in the required list.")
            roles.remove(role.id)
        await ctx.send(f"Removed {role.name} from required roles.")

    @hibernateset.command(name="settings")
    async def show_settings(self, ctx):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()
        
        role = ctx.guild.get_role(data['target_role_id']) if data['target_role_id'] else "Not Set"
        req_roles_names = []
        for rid in data['required_role_ids']:
            r = ctx.guild.get_role(rid)
            if r: req_roles_names.append(r.name)
            else: req_roles_names.append(f"Deleted-Role({rid})")

        msg = (
            f"**Target Role:** {role.mention if isinstance(role, discord.Role) else role}\n"
            f"**Duration:** {data['duration_days']} days\n"
            f"**Min Join Days:** {data['min_days_joined']}\n"
            f"**Required Roles (Must have ANY):** {humanize_list(req_roles_names) if req_roles_names else 'None'}"
        )
        embed = discord.Embed(title="Hibernate Settings", description=msg, color=discord.Color.blue())
        await ctx.send(embed=embed)

    # --- Admin Management ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def hibernatemanage(self, ctx):
        """Manage active hibernations."""
        pass

    @hibernatemanage.command(name="list")
    async def list_hibernating(self, ctx):
        """List all users currently tracking a hibernation end date."""
        members_data = await self.config.all_members(ctx.guild)
        
        active_list = []
        for member_id, data in members_data.items():
            end = data.get("hibernation_end")
            if end:
                # Basic check if user is still in server
                member = ctx.guild.get_member(member_id)
                name = member.display_name if member else f"Left-User({member_id})"
                active_list.append(f"{name}: Ends <t:{int(end)}:R>")

        if not active_list:
            return await ctx.send("No users are currently recorded as hibernating.")

        # Simple pagination for discord limits
        chunked = "\n".join(active_list)
        if len(chunked) > 2000:
            await ctx.send("Too many users to list in one message. Showing first 2000 chars.")
            await ctx.send(chunked[:2000])
        else:
            embed = discord.Embed(title="Hibernating Users", description=chunked, color=discord.Color.blue())
            await ctx.send(embed=embed)

    @hibernatemanage.command(name="extend")
    async def extend_user(self, ctx, member: discord.Member, days: int):
        """Extend a user's hibernation by X days."""
        current_end = await self.config.member(member).hibernation_end()
        
        if not current_end:
            return await ctx.send(f"{member.display_name} is not currently hibernating.")
        
        new_end_dt = datetime.fromtimestamp(current_end, timezone.utc) + timedelta(days=days)
        new_ts = new_end_dt.timestamp()
        
        await self.config.member(member).hibernation_end.set(new_ts)
        await ctx.send(f"Extended {member.display_name}'s hibernation by {days} days.\nNew End: <t:{int(new_ts)}:F>")

    @hibernatemanage.command(name="cancel")
    async def cancel_user(self, ctx, member: discord.Member):
        """Immediately cancels a user's active hibernation."""
        current_end = await self.config.member(member).hibernation_end()
        
        if not current_end:
            return await ctx.send(f"**{member.display_name}** is not currently tracked as hibernating.")

        settings = await self.config.guild(ctx.guild).all()
        target_role_id = settings["target_role_id"]
        role_removed = False

        # Attempt to remove the role if configured
        if target_role_id:
            target_role = ctx.guild.get_role(target_role_id)
            if target_role and target_role in member.roles:
                try:
                    await member.remove_roles(target_role, reason="Hibernation cancelled by admin.")
                    role_removed = True
                except discord.Forbidden:
                    await ctx.send(f"Warning: Could not remove the role **{target_role.name}** due to missing permissions, but hibernation tracking was cleared.")
                except discord.HTTPException:
                    pass

        # Clear the config tracking regardless of role removal success
        await self.config.member(member).hibernation_end.set(None)

        status_msg = "Hibernation tracking cleared."
        if role_removed:
            status_msg = f"Hibernation role **{target_role.name}** removed and tracking cleared."
        
        await ctx.send(f"**{member.display_name}**'s hibernation has been cancelled. {status_msg}")