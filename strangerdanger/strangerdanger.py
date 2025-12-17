import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify
from typing import Optional, Union

class StrangerDanger(commands.Cog):
    """
    Audit server permissions for dangerous settings.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237498123, force_registration=True)
        
        default_guild = {
            "admin_roles": [],
            "mod_roles": [],
            "role_exceptions": {},  # "role_id": ["permission_name", ...]
        }
        self.config.register_guild(**default_guild)

        # Default dangerous permissions to flag if none provided
        self.dangerous_perms = [
            "administrator",
            "manage_guild",
            "manage_roles",
            "manage_channels",
            "ban_members",
            "kick_members",
            "manage_messages",
            "mention_everyone",
            "manage_webhooks"
        ]

    async def _get_all_permissions_names(self):
        """Returns a list of valid permission attribute names."""
        return [p[0] for p in discord.Permissions.all()]

    def _format_perm_name(self, name: str):
        """Normalizes input to snake_case permission names."""
        return name.lower().replace(" ", "_")

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def strangerdanger(self, ctx):
        """
        Permission auditing and configuration.
        """
        pass

    @strangerdanger.command(name="listpermissions")
    async def list_permissions(self, ctx):
        """
        Lists all available permission names that can be scanned.
        """
        perms = await self._get_all_permissions_names()
        formatted = ", ".join(sorted(perms))
        
        for page in pagify(formatted, delims=[", "], page_length=1900):
            await ctx.send(box(page, lang="css"))

    @strangerdanger.command(name="mod")
    async def set_mod(self, ctx, role: discord.Role):
        """
        Toggle a role as a trusted Moderator.
        
        Moderator roles are ignored during scans for most permissions.
        """
        async with self.config.guild(ctx.guild).mod_roles() as mods:
            if role.id in mods:
                mods.remove(role.id)
                await ctx.send(f"Role **{role.name}** removed from trusted Moderators.")
            else:
                mods.append(role.id)
                await ctx.send(f"Role **{role.name}** added to trusted Moderators.")

    @strangerdanger.command(name="admin")
    async def set_admin(self, ctx, role: discord.Role):
        """
        Toggle a role as a trusted Administrator.
        
        Administrator roles are completely ignored during scans.
        """
        async with self.config.guild(ctx.guild).admin_roles() as admins:
            if role.id in admins:
                admins.remove(role.id)
                await ctx.send(f"Role **{role.name}** removed from trusted Administrators.")
            else:
                admins.append(role.id)
                await ctx.send(f"Role **{role.name}** added to trusted Administrators.")

    @strangerdanger.command(name="exception")
    async def set_exception(self, ctx, role: discord.Role, permission: str):
        """
        Allow a specific role to have a specific permission without flagging it.
        
        Example: `[p]strangerdanger exception @DJs manage_channels`
        """
        perm_clean = self._format_perm_name(permission)
        valid_perms = await self._get_all_permissions_names()

        if perm_clean not in valid_perms:
            return await ctx.send(f"❌ `{perm_clean}` is not a valid Discord permission.")

        async with self.config.guild(ctx.guild).role_exceptions() as exceptions:
            str_id = str(role.id)
            if str_id not in exceptions:
                exceptions[str_id] = []
            
            if perm_clean in exceptions[str_id]:
                exceptions[str_id].remove(perm_clean)
                await ctx.send(f"Removed exception: **{role.name}** is no longer allowed `{perm_clean}`.")
                # Clean up empty keys
                if not exceptions[str_id]:
                    del exceptions[str_id]
            else:
                exceptions[str_id].append(perm_clean)
                await ctx.send(f"Added exception: **{role.name}** is now allowed `{perm_clean}`.")

    @strangerdanger.command(name="view")
    async def view_settings(self, ctx):
        """
        View current configuration (Mods, Admins, Exceptions).
        """
        data = await self.config.guild(ctx.guild).all()
        
        admin_names = [ctx.guild.get_role(rid).name for rid in data['admin_roles'] if ctx.guild.get_role(rid)]
        mod_names = [ctx.guild.get_role(rid).name for rid in data['mod_roles'] if ctx.guild.get_role(rid)]
        
        msg = f"**Trusted Admins:** {', '.join(admin_names) if admin_names else 'None'}\n"
        msg += f"**Trusted Mods:** {', '.join(mod_names) if mod_names else 'None'}\n"
        msg += "**Exceptions:**\n"
        
        if not data['role_exceptions']:
            msg += "None"
        else:
            for rid, perms in data['role_exceptions'].items():
                role = ctx.guild.get_role(int(rid))
                rname = role.name if role else "Deleted Role"
                msg += f"- **{rname}**: {', '.join(perms)}\n"

        await ctx.send(msg)

    @strangerdanger.command(name="scan")
    async def scan_permissions(self, ctx, permission: Optional[str] = None):
        """
        Scans channels and roles for dangerous permissions.
        
        If permission is provided, looks only for that specific one.
        Otherwise, looks for a default list of dangerous permissions.
        """
        guild = ctx.guild
        data = await self.config.guild(guild).all()
        
        trusted_roles = set(data['admin_roles'] + data['mod_roles'])
        exceptions = data['role_exceptions'] # Dict of "role_id": ["perm", "perm"]

        # Determine which permissions to scan for
        perms_to_scan = []
        if permission:
            clean_perm = self._format_perm_name(permission)
            valid = await self._get_all_permissions_names()
            if clean_perm not in valid:
                return await ctx.send(f"❌ `{clean_perm}` is not a valid permission name.")
            perms_to_scan.append(clean_perm)
        else:
            perms_to_scan = self.dangerous_perms

        await ctx.trigger_typing()
        
        issues = []

        # 1. SCAN GLOBAL ROLE PERMISSIONS
        for role in guild.roles:
            if role.managed or role.id in trusted_roles:
                continue

            # Check exceptions for this role
            role_exceptions = exceptions.get(str(role.id), [])

            for perm_name in perms_to_scan:
                # getattr returns True/False for the permission
                if getattr(role.permissions, perm_name, False):
                    # It has the perm. Is it excepted?
                    if perm_name not in role_exceptions:
                        issues.append(f"[GLOBAL ROLE] **{role.name}** has `{perm_name}`")

        # 2. SCAN CHANNEL OVERWRITES
        for channel in guild.channels:
            # Skip if we can't see it (unlikely for admin, but good practice)
            if not channel.permissions_for(guild.me).view_channel:
                continue

            for target, overwrite in channel.overwrites.items():
                # Target can be Role or Member
                is_role = isinstance(target, discord.Role)
                target_name = target.name
                target_id = target.id

                # Skip trusted
                if is_role:
                    if target_id in trusted_roles:
                        continue
                else:
                    # For members, we check if they have any trusted role?
                    # Usually explicit overwrites on members are risky regardless, 
                    # but if the user is an admin we skip.
                    if any(r.id in trusted_roles for r in target.roles):
                        continue

                # Check exceptions (Roles only)
                target_exceptions = []
                if is_role:
                    target_exceptions = exceptions.get(str(target_id), [])

                # Check the overwrite values
                # overwrite pair returns (allow, deny) objects
                allow, deny = overwrite.pair()
                
                for perm_name in perms_to_scan:
                    if getattr(allow, perm_name, False):
                        # Explicitly allowed in channel
                        if perm_name not in target_exceptions:
                            type_label = "ROLE" if is_role else "USER"
                            issues.append(f"[CHANNEL: {channel.name}] **{target_name}** ({type_label}) has `{perm_name}`")

        if not issues:
            await ctx.send("✅ Scan complete. No unexpected dangerous permissions found.")
        else:
            header = f"⚠️ **StrangerDanger Scan Report** ⚠️\nFound {len(issues)} potential issues:\n\n"
            full_text = "\n".join(issues)
            
            for page in pagify(full_text, delims=["\n"], page_length=1900):
                await ctx.send(header + page if page == full_text[:1900] else page)
                header = "" # Only print header on first page