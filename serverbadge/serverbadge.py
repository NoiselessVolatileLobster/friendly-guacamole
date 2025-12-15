import discord
import logging
import asyncio  # <--- Added import
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box, pagify

class ServerBadge(commands.Cog):
    """Assign roles to users who have this server as their Primary Guild."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9812374123, force_registration=True)
        self.config.register_guild(role_id=None, channel_id=None)
        self.log = logging.getLogger("red.serverbadge")

    async def _notify(self, guild, message):
        """Helper to send notification to the configured channel."""
        channel_id = await self.config.guild(guild).channel_id()
        if not channel_id:
            return
        
        channel = guild.get_channel(channel_id)
        if channel and channel.permissions_for(guild.me).send_messages:
            try:
                await channel.send(message)
            except discord.HTTPException:
                pass 

    async def _check_and_assign(self, member: discord.Member):
        """
        Checks if the member has the guild as primary and assigns/removes the role.
        Returns True if a role change was attempted/needed, False otherwise.
        """
        if member.bot:
            return False

        guild = member.guild
        role_id = await self.config.guild(guild).role_id()
        
        if not role_id:
            return False

        role = guild.get_role(role_id)
        if not role:
            return False

        # Check documentation: discord.Member.primary_guild
        has_badge = member.primary_guild and member.primary_guild.id == guild.id

        if has_badge:
            if role not in member.roles:
                try:
                    await member.add_roles(role, reason="User has Server Badge (Primary Guild).")
                    self.log.info(f"Assigned Server Badge role '{role.name}' to {member} ({member.id}) in guild '{guild.name}'.")
                    await self._notify(guild, f"✅ **Server Badge Added:** Assigned {role.mention} to {member.mention} (`{member.id}`).")
                    
                    # Wait to prevent rate limits
                    await asyncio.sleep(1.5) 
                    return True
                except discord.Forbidden:
                    self.log.warning(f"Failed to assign role to {member.id} in {guild.name}: Missing Permissions.")
                    pass 
        else:
            if role in member.roles:
                try:
                    await member.remove_roles(role, reason="User no longer has Server Badge.")
                    self.log.info(f"Removed Server Badge role '{role.name}' from {member} ({member.id}) in guild '{guild.name}'.")
                    await self._notify(guild, f"❌ **Server Badge Removed:** Removed {role.mention} from {member.mention} (`{member.id}`).")
                    
                    # Wait to prevent rate limits
                    await asyncio.sleep(1.5)
                    return True
                except discord.Forbidden:
                    self.log.warning(f"Failed to remove role from {member.id} in {guild.name}: Missing Permissions.")
                    pass
        
        return False

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self._check_and_assign(member)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # Fallback for local updates
        await self._check_and_assign(after)

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        """
        Listens for global user profile changes.
        """
        if before.primary_guild != after.primary_guild:
            for guild in self.bot.guilds:
                member = guild.get_member(after.id)
                if member:
                    await self._check_and_assign(member)

    @commands.group(name="serverbadgeset")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def serverbadgeset(self, ctx):
        """Configuration for ServerBadge."""
        pass

    @serverbadgeset.command(name="role")
    async def serverbadgeset_role(self, ctx, role: discord.Role = None):
        """Set the role to assign to users with the Server Badge.

        Leave empty to disable/clear.
        """
        if role:
            await self.config.guild(ctx.guild).role_id.set(role.id)
            await ctx.send(f"Role set to {role.mention}. It will be assigned to users with this server as their primary guild.")
        else:
            await self.config.guild(ctx.guild).role_id.set(None)
            await ctx.send("Server Badge role configuration cleared.")

    @serverbadgeset.command(name="channel")
    async def serverbadgeset_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel where role changes are logged.

        Leave empty to disable notifications.
        """
        if channel:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"Notification channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("Notification channel cleared.")

    @serverbadgeset.command(name="view")
    async def serverbadgeset_view(self, ctx):
        """View all configured settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        role_id = settings['role_id']
        channel_id = settings['channel_id']
        
        role = ctx.guild.get_role(role_id) if role_id else None
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        role_name = role.mention if role else "Not Configured"
        channel_name = channel.mention if channel else "Not Configured"
        
        await ctx.send(f"**ServerBadge Settings**\nRole: {role_name}\nLog Channel: {channel_name}")

    @serverbadgeset.command(name="list")
    async def serverbadgeset_list(self, ctx):
        """List all users who have this guild's server badge."""
        await ctx.typing()
        
        members_with_badge = []
        for member in ctx.guild.members:
            if member.primary_guild and member.primary_guild.id == ctx.guild.id:
                members_with_badge.append(member)

        if not members_with_badge:
            return await ctx.send("No users found who have this server as their primary guild.")

        # Formatting table (Name | ID)
        header = f"{'User':<30} | {'ID':<20}"
        separator = "-" * len(header)
        rows = []
        
        for m in members_with_badge:
            rows.append(f"{m.display_name:<30} | {str(m.id):<20}")

        full_text = f"{header}\n{separator}\n" + "\n".join(rows)

        for page in pagify(full_text):
            await ctx.send(box(page))

    @serverbadgeset.command(name="scan")
    async def serverbadgeset_scan(self, ctx):
        """Manually scan all guild members and assign/remove the role.
        
        This process includes a delay between changes to avoid rate limits.
        """
        role_id = await self.config.guild(ctx.guild).role_id()
        if not role_id:
            return await ctx.send("No role configured. Please set a role first using `[p]serverbadgeset role`.")

        msg = await ctx.send("Scanning members... This may take a while depending on the number of updates required.")
        
        updates = 0
        checked = 0
        
        async with ctx.typing():
            for member in ctx.guild.members:
                if await self._check_and_assign(member):
                    updates += 1
                checked += 1
        
        await msg.edit(content=f"Scan complete. Checked {checked} members. Roles updated for {updates} members.")