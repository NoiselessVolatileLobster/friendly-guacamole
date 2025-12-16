import discord
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.predicates import MessagePredicate

# Tabulate is available in the Red environment
from tabulate import tabulate

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
            "duration_days": 30,
            "min_level": 0,
            "req_level_enabled": False
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
        Checks eligibility based on server join date and LevelUp level.
        """
        guild = ctx.guild
        member = ctx.author

        # Fetch settings
        settings = await self.config.guild(guild).all()
        target_role_id = settings["target_role_id"]
        min_days = settings["min_days_joined"]
        duration = settings["duration_days"]
        
        min_level = settings["min_level"]
        req_level_enabled = settings["req_level_enabled"]

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
        
        # --- Join Date Check ---
        if min_days > 0:
            if member.joined_at:
                joined_at_utc = member.joined_at.replace(tzinfo=timezone.utc) if member.joined_at.tzinfo is None else member.joined_at
                days_joined = (datetime.now(timezone.utc) - joined_at_utc).days
                if days_joined < min_days:
                    reasons.append(f"• You have not been a member long enough. (Required: {min_days} days, You: {days_joined} days)")
            else:
                reasons.append("• Could not determine your join date.")

        # --- LevelUp Check ---
        if req_level_enabled:
            levelup_cog = self.bot.get_cog("LevelUp")
            if not levelup_cog:
                reasons.append("• The LevelUp cog is required for eligibility but is not loaded. Please contact an admin.")
            else:
                try:
                    user_level = await levelup_cog.get_level(member)
                    if user_level < min_level:
                        reasons.append(f"• Your level is too low. (Required: Level {min_level}, You: Level {user_level})")
                except AttributeError:
                    reasons.append("• Could not fetch level data (Method not found). Contact admin.")
                except Exception as e:
                    reasons.append(f"• An error occurred checking level eligibility: {str(e)}")

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

    # --- Admin Configuration & Management ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def hibernateset(self, ctx):
        """Configuration and management for user hibernation."""
        pass

    @hibernateset.command(name="view")
    async def view_settings(self, ctx):
        """Show current configuration and active stats."""
        data = await self.config.guild(ctx.guild).all()
        
        # Count active hibernations
        all_members = await self.config.all_members(ctx.guild)
        active_count = sum(1 for m_data in all_members.values() if m_data.get("hibernation_end"))

        role = ctx.guild.get_role(data['target_role_id']) if data['target_role_id'] else "Not Set"

        msg = (
            f"**Target Role:** {role.mention if isinstance(role, discord.Role) else role}\n"
            f"**Duration:** {data['duration_days']} days\n"
            f"**Min Join Days:** {data['min_days_joined']}\n"
            f"**Current Hibernating Users:** {active_count}\n\n"
            
            f"__**Requirements**__\n"
            f"**Level Required:** {'✅ Yes' if data['req_level_enabled'] else '❌ No'}\n"
            f"**Min Level:** {data['min_level']}"
        )
        embed = discord.Embed(title="Hibernate Settings", description=msg, color=discord.Color.blue())
        await ctx.send(embed=embed)

    @hibernateset.command(name="list")
    async def list_hibernating(self, ctx):
        """List all users currently tracking a hibernation end date."""
        members_data = await self.config.all_members(ctx.guild)
        
        table_data = []
        for member_id, data in members_data.items():
            end = data.get("hibernation_end")
            if end:
                member = ctx.guild.get_member(member_id)
                name = member.display_name if member else "Left-User"
                
                # Calculate time left
                now = datetime.now(timezone.utc).timestamp()
                remaining = end - now
                
                # Format remaining time
                if remaining < 0:
                    time_left = "Expired"
                else:
                    days = int(remaining // 86400)
                    hours = int((remaining % 86400) // 3600)
                    time_left = f"{days}d {hours}h"

                end_dt = datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d")
                table_data.append([str(member_id), name, end_dt, time_left])

        if not table_data:
            return await ctx.send("No users are currently recorded as hibernating.")

        headers = ["ID", "User", "End Date", "Time Left"]
        # Using tabulate within a code block for the table preference
        output = tabulate(table_data, headers=headers, tablefmt="presto")
        
        await ctx.send(box(output, lang="text"))

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

    @hibernateset.command(name="level")
    async def set_level(self, ctx, level: int):
        """Set minimum level required to hibernate (requires LevelUp cog)."""
        if level < 0:
            return await ctx.send("Level cannot be negative.")
        await self.config.guild(ctx.guild).min_level.set(level)
        await ctx.send(f"Minimum level set to: {level}")

    @hibernateset.command(name="togglelevel")
    async def toggle_level(self, ctx, toggle: bool = None):
        """Enable or disable the LevelUp level requirement."""
        if toggle is None:
            current = await self.config.guild(ctx.guild).req_level_enabled()
            toggle = not current
        
        await self.config.guild(ctx.guild).req_level_enabled.set(toggle)
        status = "Enabled" if toggle else "Disabled"
        await ctx.send(f"Level requirement is now **{status}**.")

    @hibernateset.command(name="extend")
    async def extend_user(self, ctx, member: discord.Member, days: int):
        """Extend a user's hibernation by X days."""
        current_end = await self.config.member(member).hibernation_end()
        
        if not current_end:
            return await ctx.send(f"{member.display_name} is not currently hibernating.")
        
        new_end_dt = datetime.fromtimestamp(current_end, timezone.utc) + timedelta(days=days)
        new_ts = new_end_dt.timestamp()
        
        await self.config.member(member).hibernation_end.set(new_ts)
        await ctx.send(f"Extended {member.display_name}'s hibernation by {days} days.\nNew End: <t:{int(new_ts)}:F>")

    @hibernateset.command(name="cancel")
    async def cancel_user(self, ctx, member: discord.Member):
        """Immediately cancels a user's active hibernation."""
        current_end = await self.config.member(member).hibernation_end()
        
        if not current_end:
            return await ctx.send(f"**{member.display_name}** is not currently tracked as hibernating.")

        settings = await self.config.guild(ctx.guild).all()
        target_role_id = settings["target_role_id"]
        role_removed = False

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

        await self.config.member(member).hibernation_end.set(None)

        status_msg = "Hibernation tracking cleared."
        if role_removed:
            status_msg = f"Hibernation role **{target_role.name}** removed and tracking cleared."
        
        await ctx.send(f"**{member.display_name}**'s hibernation has been cancelled. {status_msg}")

    @hibernateset.command(name="force")
    async def force_hibernate(self, ctx, member: discord.Member):
        """
        Force a user into hibernation mode.
        
        This bypasses all eligibility checks (Level, Roles, Join Date) 
        and immediately applies the hibernation role and timer.
        """
        guild = ctx.guild
        settings = await self.config.guild(guild).all()
        target_role_id = settings["target_role_id"]
        duration = settings["duration_days"]

        # 1. Basic Config Checks
        if not target_role_id:
            return await ctx.send("Hibernation role is not configured.")
        
        target_role = guild.get_role(target_role_id)
        if not target_role:
            return await ctx.send("The configured hibernation role no longer exists.")

        # 2. Check if already hibernating (Optional: You could allow overwriting)
        if target_role in member.roles:
            return await ctx.send(f"{member.display_name} is already hibernating.")

        # 3. Apply Role
        try:
            await member.add_roles(target_role, reason=f"Hibernation forced by admin {ctx.author.name}")
        except discord.Forbidden:
            return await ctx.send("I do not have permission to assign the hibernation role.")
        except discord.HTTPException as e:
            return await ctx.send(f"An error occurred assigning the role: {e}")

        # 4. Set Timer
        end_date = datetime.now(timezone.utc) + timedelta(days=duration)
        await self.config.member(member).hibernation_end.set(end_date.timestamp())

        # 5. Confirm
        embed = discord.Embed(
            title="Hibernation Forced",
            description=f"**{member.display_name}** has been put into hibernation.\n"
                        f"**Role:** {target_role.mention}\n"
                        f"**Ends:** <t:{int(end_date.timestamp())}:F>",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)