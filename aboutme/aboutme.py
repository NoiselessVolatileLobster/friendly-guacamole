import discord
from redbot.core import commands, Config
from datetime import datetime, timezone

class AboutMe(commands.Cog):
    """A cog to show how long you have been in the server and track role progress."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "role_targets": {}, 
            "role_buddies": {},
            "location_roles": {},
            "dm_status_roles": {},
            "award_roles": [],
            "helper_roles": [],
            "egg_status_roles": {},
            "house_roles": {},
            "role_target_overrides": {} 
        }
        self.config.register_guild(**default_guild)

    async def _process_member_status(self, ctx, member: discord.Member):
        """Helper function to generate the member status embed."""
        
        if member.joined_at is None:
            return await ctx.send("I couldn't determine when that member joined this server.")

        # --- 1. Time Calculation (Line 1) ---
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days_in_server = delta.days
        date_str = joined_at.strftime("%B %d, %Y")
        
        base_description = f"Joined on {date_str} ({days_in_server} days ago)"

        # --- 2. Egg Status | House (Line 2) ---
        # Egg Status
        egg_roles_config = await self.config.guild(ctx.guild).egg_status_roles()
        egg_parts = []
        for role_id_str, emoji in egg_roles_config.items():
            role_id = int(role_id_str)
            egg_role = ctx.guild.get_role(role_id)
            if egg_role and egg_role in member.roles:
                egg_parts.append(f"{emoji} {egg_role.name}")

        # House
        house_roles_config = await self.config.guild(ctx.guild).house_roles()
        house_parts = []
        for role_id_str, emoji in house_roles_config.items():
            role_id = int(role_id_str)
            house_role = ctx.guild.get_role(role_id)
            if house_role and house_role in member.roles:
                house_parts.append(f"{emoji} {house_role.name}")

        line_2_components = []
        if egg_parts:
            line_2_components.append(", ".join(egg_parts))
        if house_parts:
            line_2_components.append(", ".join(house_parts))
            
        line_2_output = ""
        if line_2_components:
            line_2_output = f"\n{' | '.join(line_2_components)}"

        # --- 3. Location | DM Status (Line 3) ---
        # Location
        location_roles_config = await self.config.guild(ctx.guild).location_roles()
        location_parts = []
        for role_id_str, emoji in location_roles_config.items():
            role_id = int(role_id_str)
            location_role = ctx.guild.get_role(role_id)
            if location_role and location_role in member.roles:
                location_parts.append(f"{emoji} {location_role.name}")

        # DM Status
        dm_status_config = await self.config.guild(ctx.guild).dm_status_roles()
        dm_status_parts = []
        for role_id_str, emoji in dm_status_config.items():
            role_id = int(role_id_str)
            dm_role = ctx.guild.get_role(role_id)
            if dm_role and dm_role in member.roles:
                dm_status_parts.append(f"{emoji} {dm_role.name}")

        line_3_components = []
        if location_parts:
            line_3_components.append(", ".join(location_parts))
        if dm_status_parts:
            line_3_components.append(", ".join(dm_status_parts))

        line_3_output = ""
        if line_3_components:
            line_3_output = f"\n{' | '.join(line_3_components)}"

        # --- 4. Awards (Line 4) ---
        award_roles_config = await self.config.guild(ctx.guild).award_roles()
        award_parts = []

        for role_id in award_roles_config:
            award_role = ctx.guild.get_role(int(role_id))
            if award_role and award_role in member.roles:
                award_parts.append(f"{award_role.name}")

        award_output = ""
        if award_parts:
            award_output = f"\n**Awards:** {', '.join(award_parts)}"

        # --- 5. Teams (Line 5) ---
        helper_roles_config = await self.config.guild(ctx.guild).helper_roles()
        helper_parts = []

        for role_id in helper_roles_config:
            helper_role = ctx.guild.get_role(int(role_id))
            if helper_role and helper_role in member.roles:
                helper_parts.append(f"{helper_role.name}")

        helper_output = ""
        if helper_parts:
            helper_output = f"\n**Teams:** {', '.join(helper_parts)}"

        # --- Role Progress Calculation ---
        role_targets = await self.config.guild(ctx.guild).role_targets()
        role_buddies = await self.config.guild(ctx.guild).role_buddies()
        role_target_overrides = await self.config.guild(ctx.guild).role_target_overrides()
        
        progress_lines = []

        for base_id_str, target_days in role_targets.items():
            base_role = ctx.guild.get_role(int(base_id_str))
            if not base_role: continue

            # Check for Target Override
            target_override_id = role_target_overrides.get(base_id_str)
            if target_override_id:
                target_override_role = ctx.guild.get_role(int(target_override_id))
                if target_override_role and target_override_role in member.roles:
                    # User has the superior target role - Checkmark removed as requested
                    progress_lines.append(f"{target_override_role.mention} Unlocked!")
                    continue 

            # Standard Base Role Logic
            has_base_role = base_role in member.roles
            
            buddy_role_ids = role_buddies.get(base_id_str, [])
            has_buddy_role = False
            for b_id_str in buddy_role_ids:
                buddy_role_obj = ctx.guild.get_role(int(b_id_str))
                if buddy_role_obj and buddy_role_obj in member.roles:
                    has_buddy_role = True
                    break 

            mention = base_role.mention 
            
            if not has_base_role and not has_buddy_role:
                continue 

            if days_in_server < target_days:
                if has_buddy_role:
                    progress_lines.append(f"{mention}: Locked 🔒 - Days not met")
                elif has_base_role:
                    remaining = target_days - days_in_server
                    progress_lines.append(f"{mention}: **{remaining}** days remaining to unlock")
            else:
                if has_buddy_role:
                    progress_lines.append(f"{mention}: Unlocked ✅")
                elif has_base_role:
                    progress_lines.append(f"{mention}: Level up to unlock!")

        # Format Role Progress
        role_progress_output = ""
        if progress_lines:
            role_progress_output = "\n\n**Role Progress**\n" + "\n".join(progress_lines)

        # --- Build Final Description ---
        final_description = (
            base_description + 
            line_2_output +  # Egg | House
            line_3_output +  # Location | DM Status
            award_output +   # Awards
            helper_output +  # Teams
            role_progress_output
            
        )

        embed = discord.Embed(
            title=f"About {member.display_name} in {ctx.guild.name}",
            description=final_description,
            color=await ctx.embed_color()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        return embed

    # ------------------------------------------------------------------
    # USER COMMANDS
    # ------------------------------------------------------------------

    @commands.command()
    @commands.guild_only()
    async def about(self, ctx, member: discord.Member):
        """Check how long a specific user has been in this server and see their role progress."""
        embed = await self._process_member_status(ctx, member)
        if embed:
            await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    async def aboutme(self, ctx):
        """Check how long you have been in this server and see role progress."""
        embed = await self._process_member_status(ctx, ctx.author)
        if embed:
            await ctx.send(embed=embed)
            
    # ------------------------------------------------------------------
    # ADMIN COMMANDS
    # ------------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def aboutmeset(self, ctx):
        """Settings for the AboutMe cog."""
        pass

    # ------------------------------------------------------------------
    # Location Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="locations")
    async def aboutmeset_locations(self, ctx):
        """Manage location roles and their corresponding emojis."""
        pass

    @aboutmeset_locations.command(name="add")
    async def locations_add(self, ctx, role: discord.Role, emoji: str):
        """Add a location role and associate an emoji with it."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            role_id_str = str(role.id)
            locations[role_id_str] = emoji
            
        await ctx.send(f"Configured **{role.name}** as a location role with emoji: {emoji}")

    @aboutmeset_locations.command(name="remove")
    async def locations_remove(self, ctx, role: discord.Role):
        """Remove a location role from tracking."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            role_id_str = str(role.id)
            if role_id_str in locations:
                del locations[role_id_str]
                await ctx.send(f"Removed **{role.name}** from location role tracking.")
            else:
                await ctx.send(f"**{role.name}** is not currently tracked as a location role.")

    @aboutmeset_locations.command(name="list")
    async def locations_list(self, ctx):
        """List all configured location roles."""
        locations = await self.config.guild(ctx.guild).location_roles()
        if not locations:
            return await ctx.send("No location roles are currently configured.")

        lines = []
        for role_id_str, emoji in locations.items():
            role = ctx.guild.get_role(int(role_id_str))
            role_name = role.mention if role else f"Deleted-Role-{role_id_str}"
            lines.append(f"{emoji} {role_name}")

        embed = discord.Embed(title="Configured Location Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # DM Status Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="dmstatus")
    async def aboutmeset_dmstatus(self, ctx):
        """Manage DM Status roles and their corresponding emojis."""
        pass

    @aboutmeset_dmstatus.command(name="add")
    async def dmstatus_add(self, ctx, role: discord.Role, emoji: str):
        """Add a DM Status role and associate an emoji with it."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            role_id_str = str(role.id)
            statuses[role_id_str] = emoji
            
        await ctx.send(f"Configured **{role.name}** as a DM Status role with emoji: {emoji}")

    @aboutmeset_dmstatus.command(name="remove")
    async def dmstatus_remove(self, ctx, role: discord.Role):
        """Remove a DM Status role from tracking."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            role_id_str = str(role.id)
            if role_id_str in statuses:
                del statuses[role_id_str]
                await ctx.send(f"Removed **{role.name}** from DM Status role tracking.")
            else:
                await ctx.send(f"**{role.name}** is not currently tracked as a DM Status role.")

    @aboutmeset_dmstatus.command(name="list")
    async def dmstatus_list(self, ctx):
        """List all configured DM Status roles."""
        statuses = await self.config.guild(ctx.guild).dm_status_roles()
        if not statuses:
            return await ctx.send("No DM Status roles are currently configured.")

        lines = []
        for role_id_str, emoji in statuses.items():
            role = ctx.guild.get_role(int(role_id_str))
            role_name = role.mention if role else f"Deleted-Role-{role_id_str}"
            lines.append(f"{emoji} {role_name}")

        embed = discord.Embed(title="Configured DM Status Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Award Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="award")
    async def aboutmeset_award(self, ctx):
        """Manage Award roles (displayed in the Awards section)."""
        pass

    @aboutmeset_award.command(name="add")
    async def award_add(self, ctx, role: discord.Role):
        """Add an Award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id not in awards:
                awards.append(role.id)
                await ctx.send(f"Added **{role.name}** to Award roles.")
            else:
                await ctx.send(f"**{role.name}** is already an Award role.")

    @aboutmeset_award.command(name="remove")
    async def award_remove(self, ctx, role: discord.Role):
        """Remove an Award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id in awards:
                awards.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from Award roles.")
            else:
                await ctx.send(f"**{role.name}** is not currently configured as an Award role.")

    @aboutmeset_award.command(name="list")
    async def award_list(self, ctx):
        """List all configured Award roles."""
        awards = await self.config.guild(ctx.guild).award_roles()
        if not awards:
            return await ctx.send("No Award roles are currently configured.")

        lines = []
        for role_id in awards:
            role = ctx.guild.get_role(role_id)
            role_name = role.mention if role else f"Deleted-Role-{role_id}"
            lines.append(role_name)

        embed = discord.Embed(title="Configured Award Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Helper Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="helper")
    async def aboutmeset_helper(self, ctx):
        """Manage Helper roles (displayed in the Helper section)."""
        pass

    @aboutmeset_helper.command(name="add")
    async def helper_add(self, ctx, role: discord.Role):
        """Add a Helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id not in helpers:
                helpers.append(role.id)
                await ctx.send(f"Added **{role.name}** to Helper roles.")
            else:
                await ctx.send(f"**{role.name}** is already a Helper role.")

    @aboutmeset_helper.command(name="remove")
    async def helper_remove(self, ctx, role: discord.Role):
        """Remove a Helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id in helpers:
                helpers.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from Helper roles.")
            else:
                await ctx.send(f"**{role.name}** is not currently configured as a Helper role.")

    @aboutmeset_helper.command(name="list")
    async def helper_list(self, ctx):
        """List all configured Helper roles."""
        helpers = await self.config.guild(ctx.guild).helper_roles()
        if not helpers:
            return await ctx.send("No Helper roles are currently configured.")

        lines = []
        for role_id in helpers:
            role = ctx.guild.get_role(role_id)
            role_name = role.mention if role else f"Deleted-Role-{role_id}"
            lines.append(role_name)

        embed = discord.Embed(title="Configured Helper Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)


    # ------------------------------------------------------------------
    # House Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="houseroles")
    async def aboutmeset_houseroles(self, ctx):
        """Manage House roles and their corresponding emojis."""
        pass

    @aboutmeset_houseroles.command(name="add")
    async def houseroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add an House Status role and associate an emoji with it."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            role_id_str = str(role.id)
            house_roles[role_id_str] = emoji
            
        await ctx.send(f"Configured **{role.name}** as an House role with emoji: {emoji}")

    @aboutmeset_houseroles.command(name="remove")
    async def houseroles_remove(self, ctx, role: discord.Role):
        """Remove an House role."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            role_id_str = str(role.id)
            if role_id_str in house_roles:
                del house_roles[role_id_str]
                await ctx.send(f"Removed **{role.name}** from Houses roles.")
            else:
                await ctx.send(f"**{role.name}** is not currently configured as an House role.")

    @aboutmeset_houseroles.command(name="list")
    async def houseroles_list(self, ctx):
        """List all configured House roles."""
        house_roles = await self.config.guild(ctx.guild).house_roles()
        if not house_roles:
            return await ctx.send("No House roles are currently configured.")

        lines = []
        for role_id_str, emoji in house_roles.items():
            role = ctx.guild.get_role(int(role_id_str))
            role_name = role.mention if role else f"Deleted-Role-{role_id_str}"
            lines.append(f"{emoji} {role_name}")

        embed = discord.Embed(title="Configured House Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Egg Status Role Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="eggroles")
    async def aboutmeset_eggroles(self, ctx):
        """Manage Egg Status roles and their corresponding emojis."""
        pass

    @aboutmeset_eggroles.command(name="add")
    async def eggroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add an Egg Status role and associate an emoji with it."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            role_id_str = str(role.id)
            egg_roles[role_id_str] = emoji
            
        await ctx.send(f"Configured **{role.name}** as an Egg Status role with emoji: {emoji}")

    @aboutmeset_eggroles.command(name="remove")
    async def eggroles_remove(self, ctx, role: discord.Role):
        """Remove an Egg Status role."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            role_id_str = str(role.id)
            if role_id_str in egg_roles:
                del egg_roles[role_id_str]
                await ctx.send(f"Removed **{role.name}** from Egg Status roles.")
            else:
                await ctx.send(f"**{role.name}** is not currently configured as an Egg Status role.")

    @aboutmeset_eggroles.command(name="list")
    async def eggroles_list(self, ctx):
        """List all configured Egg Status roles."""
        egg_roles = await self.config.guild(ctx.guild).egg_status_roles()
        if not egg_roles:
            return await ctx.send("No Egg Status roles are currently configured.")

        lines = []
        for role_id_str, emoji in egg_roles.items():
            role = ctx.guild.get_role(int(role_id_str))
            role_name = role.mention if role else f"Deleted-Role-{role_id_str}"
            lines.append(f"{emoji} {role_name}")

        embed = discord.Embed(title="Configured Egg Status Roles", description="\n".join(lines), color=await ctx.embed_color())
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Existing Role Progress Management
    # ------------------------------------------------------------------

    @aboutmeset.group(name="roles")
    async def aboutmeset_roles(self, ctx):
        """Manage role targets."""
        pass

    @aboutmeset_roles.command(name="add")
    async def roles_add(self, ctx, role: discord.Role, days: int):
        """
        Set the day target for a role.
        Usage: [p]aboutmeset roles add @BaseRole 30
        """
        if days < 1:
            return await ctx.send("Please enter a positive number of days.")

        async with self.config.guild(ctx.guild).role_targets() as targets:
            targets[str(role.id)] = days
        
        await ctx.send(f"Configured **{role.name}** with a target of **{days}** days.")

    @aboutmeset_roles.command(name="link")
    async def roles_link(self, ctx, base_role: discord.Role, buddy_role: discord.Role):
        """
        Link a reward (buddy) role to a base role. Can be used multiple times.
        Usage: [p]aboutmeset roles link @BaseRole @RewardRole
        """
        base_id = str(base_role.id)
        buddy_id = str(buddy_role.id)

        targets = await self.config.guild(ctx.guild).role_targets()
        if base_id not in targets:
            return await ctx.send(f"**{base_role.name}** is not configured yet. Use `[p]aboutmeset roles add` first.")

        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if base_id not in buddies:
                buddies[base_id] = []
            
            if buddy_id in buddies[base_id]:
                return await ctx.send(f"**{buddy_role.name}** is already linked to **{base_role.name}**.")

            buddies[base_id].append(buddy_id)
            
        await ctx.send(f"Linked **{buddy_role.name}** as a buddy role for **{base_role.name}**.")

    @aboutmeset_roles.command(name="unlink")
    async def roles_unlink(self, ctx, base_role: discord.Role, buddy_role: discord.Role):
        """
        Remove a specific buddy role from the base role's list.
        Usage: [p]aboutmeset roles unlink @BaseRole @RewardRole
        """
        base_id = str(base_role.id)
        buddy_id = str(buddy_role.id)

        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if base_id not in buddies or buddy_id not in buddies[base_id]:
                return await ctx.send(f"**{buddy_role.name}** is not currently linked to **{base_role.name}**.")

            buddies[base_id].remove(buddy_id)
            
            if not buddies[base_id]:
                del buddies[base_id]
                
            await ctx.send(f"Unlinked **{buddy_role.name}** from **{base_role.name}**.")

    @aboutmeset_roles.command(name="linktarget")
    async def roles_linktarget(self, ctx, base_role: discord.Role, target_role: discord.Role):
        """
        Link a 'target' role to a base role. 
        If the user has the target role, the base role display is replaced by 'Unlocked!'.
        Usage: [p]aboutmeset roles linktarget @BaseRole @TargetRole
        """
        base_id = str(base_role.id)
        target_id = str(target_role.id)
        
        # Check if base role is configured
        targets = await self.config.guild(ctx.guild).role_targets()
        if base_id not in targets:
             return await ctx.send(f"**{base_role.name}** is not configured as a base role yet.")
             
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            overrides[base_id] = target_id
            
        await ctx.send(f"Linked **{target_role.name}** as a target override for **{base_role.name}**.")

    @aboutmeset_roles.command(name="unlinktarget")
    async def roles_unlinktarget(self, ctx, base_role: discord.Role):
        """
        Remove the target role link from a base role.
        Usage: [p]aboutmeset roles unlinktarget @BaseRole
        """
        base_id = str(base_role.id)
        
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            if base_id in overrides:
                del overrides[base_id]
                await ctx.send(f"Removed target override for **{base_role.name}**.")
            else:
                await ctx.send(f"**{base_role.name}** does not have a target override linked.")

    @aboutmeset_roles.command(name="remove")
    async def roles_remove(self, ctx, role: discord.Role):
        """
        Stop tracking a role completely (removes day target, buddy links, and target overrides).
        """
        role_id = str(role.id)
        
        async with self.config.guild(ctx.guild).role_targets() as targets:
            if role_id in targets:
                del targets[role_id]
            else:
                return await ctx.send("That role is not currently configured.")

        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if role_id in buddies:
                del buddies[role_id]

        # NEW: Remove from target overrides as well
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            if role_id in overrides:
                del overrides[role_id]

        await ctx.send(f"Removed configuration for **{role.name}**.")

    @aboutmeset_roles.command(name="list")
    async def roles_list(self, ctx):
        """List all configured roles, days, linked buddy roles, and target overrides."""
        targets = await self.config.guild(ctx.guild).role_targets()
        buddies = await self.config.guild(ctx.guild).role_buddies()
        target_overrides = await self.config.guild(ctx.guild).role_target_overrides()
        
        if not targets:
            return await ctx.send("No roles are currently configured.")

        lines = []
        for role_id, days in targets.items():
            role = ctx.guild.get_role(int(role_id))
            role_name = role.mention if role else f"Deleted-Role-{role_id}"
            
            # Buddy Text
            buddy_text = ""
            if role_id in buddies:
                buddy_names = []
                for buddy_id_str in buddies[role_id]:
                    buddy_role = ctx.guild.get_role(int(buddy_id_str))
                    buddy_name = buddy_role.mention if buddy_role else "Unknown Role"
                    buddy_names.append(buddy_name)
                buddy_text = f" ➡️ Buddies: {', '.join(buddy_names)}"
            
            # Target Override Text
            target_text = ""
            if role_id in target_overrides:
                t_id = target_overrides[role_id]
                t_role = ctx.guild.get_role(int(t_id))
                t_name = t_role.mention if t_role else f"Unknown-Role-{t_id}"
                target_text = f" 🎯 Target: {t_name}"

            lines.append(f"{role_name}: **{days}** days{buddy_text}{target_text}")

        embed = discord.Embed(
            title="AboutMe Configurations",
            description="\n".join(lines),
            color=await ctx.embed_color()
        )
        await ctx.send(embed=embed)