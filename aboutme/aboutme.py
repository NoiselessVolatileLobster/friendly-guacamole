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
            "role_buddies": {}  
        }
        self.config.register_guild(**default_guild)

    @commands.command()
    @commands.guild_only()
    async def aboutme(self, ctx):
        """Check how long you have been in this server and see role progress."""
        
        member = ctx.author
        
        if member.joined_at is None:
            return await ctx.send("I couldn't determine when you joined this server.")

        # --- 1. Time Calculation ---
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days_in_server = delta.days
        date_str = joined_at.strftime("%B %d, %Y")

        # --- 2. Build Embed ---
        embed = discord.Embed(
            title=ctx.guild.name,
            description=f"Joined on {date_str}.\nThat was **{days_in_server}** days ago!",
            color=await ctx.embed_color()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        # Line added with hardcoded placeholder <id:customize>
        embed.description += (
            f"\n\n---\n"
            f"Don't forget to visit <id:customize> to request more roles!"
        )


        # --- 3. Check Role Progress ---
        role_targets = await self.config.guild(ctx.guild).role_targets()
        role_buddies = await self.config.guild(ctx.guild).role_buddies()
        progress_lines = []

        # Iterate through all CONFIGURED base roles
        for base_id_str, target_days in role_targets.items():
            
            base_role = ctx.guild.get_role(int(base_id_str))
            if not base_role: continue

            # A. Check possession of the Base Role
            has_base_role = base_role in member.roles
            
            # B. Check possession of any Buddy Role
            buddy_role_ids = role_buddies.get(base_id_str, [])
            has_buddy_role = False
            for b_id_str in buddy_role_ids:
                buddy_role_obj = ctx.guild.get_role(int(b_id_str))
                if buddy_role_obj and buddy_role_obj in member.roles:
                    has_buddy_role = True
                    break 

            # C. Decide whether to display this path status
            mention = base_role.mention 
            
            # Only display if the user is actively involved (has the base role) OR has completed the path (has the buddy role)
            if not has_base_role and not has_buddy_role:
                continue 

            # --- Status Logic Flow ---
            if days_in_server < target_days:
                # Time not met.
                if has_buddy_role:
                    # User has reward role, but hasn't met the time requirement yet.
                    progress_lines.append(f"{mention}: Locked 🔒 - Days not met")
                elif has_base_role:
                    # Standard countdown
                    remaining = target_days - days_in_server
                    progress_lines.append(f"{mention}: **{remaining}** days remaining to unlock")
            
            else:
                # Time requirement met.
                if has_buddy_role:
                    # Time met + Has ANY Reward Role = Unlocked (SUCCESS!)
                    progress_lines.append(f"{mention}: Unlocked ✅")
                elif has_base_role:
                    # Time met + Missing ALL Reward Roles = Ready for promotion
                    progress_lines.append(f"{mention}: Level up to unlock!")

        if progress_lines:
            embed.add_field(
                name="Role Progress", 
                value="\n".join(progress_lines), 
                inline=False
            )

        await ctx.send(embed=embed)

    # --- Configuration Commands (Unchanged) ---

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def aboutmeset(self, ctx):
        """Settings for the AboutMe cog."""
        pass

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

    @aboutmeset_roles.command(name="remove")
    async def roles_remove(self, ctx, role: discord.Role):
        """
        Stop tracking a role completely (removes day target and all buddy links).
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

        await ctx.send(f"Removed configuration for **{role.name}**.")

    @aboutmeset_roles.command(name="list")
    async def roles_list(self, ctx):
        """List all configured roles, days, and linked buddy roles."""
        targets = await self.config.guild(ctx.guild).role_targets()
        buddies = await self.config.guild(ctx.guild).role_buddies()
        
        if not targets:
            return await ctx.send("No roles are currently configured.")

        lines = []
        for role_id, days in targets.items():
            role = ctx.guild.get_role(int(role_id))
            role_name = role.mention if role else f"Deleted-Role-{role_id}"
            
            buddy_text = ""
            if role_id in buddies:
                buddy_names = []
                for buddy_id_str in buddies[role_id]:
                    buddy_role = ctx.guild.get_role(int(buddy_id_str))
                    buddy_name = buddy_role.mention if buddy_role else "Unknown Role"
                    buddy_names.append(buddy_name)
                    
                buddy_text = f" ➡️ Buddies: {', '.join(buddy_names)}"

            lines.append(f"{role_name}: **{days}** days{buddy_text}")

        embed = discord.Embed(
            title="AboutMe Configurations",
            description="\n".join(lines),
            color=await ctx.embed_color()
        )
        await ctx.send(embed=embed)