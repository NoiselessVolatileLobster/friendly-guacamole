import discord
from redbot.core import commands, Config
from datetime import datetime, timezone

class AboutMe(commands.Cog):
    """A cog to show how long you have been in the server and track role progress."""

    def __init__(self, bot):
        self.bot = bot
        # Initialize the Config to save data
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        # Default structure: A dictionary where Key = Role ID (string), Value = Days (int)
        default_guild = {
            "role_targets": {}
        }
        self.config.register_guild(**default_guild)

    @commands.command()
    @commands.guild_only()
    async def aboutme(self, ctx):
        """Check how long you have been in this server and see role progress."""
        
        member = ctx.author
        
        if member.joined_at is None:
            return await ctx.send("I couldn't determine when you joined this server.")

        # 1. Calculate basic time info
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days_in_server = delta.days

        date_str = joined_at.strftime("%B %d, %Y")

        # 2. Create the Base Embed
        embed = discord.Embed(
            title=ctx.guild.name,
            description=f"Joined on {date_str}.\nThat was **{days_in_server}** days ago!",
            color=await ctx.embed_color()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # 3. Check for Role Targets
        # Fetch the dictionary of {role_id: target_days}
        role_targets = await self.config.guild(ctx.guild).role_targets()
        
        progress_lines = []

        # Loop through the user's current roles
        for role in member.roles:
            role_id_str = str(role.id)
            
            # If this role is in our database
            if role_id_str in role_targets:
                target_days = role_targets[role_id_str]
                days_remaining = target_days - days_in_server

                # Only show if there is time remaining (greater than 0)
                if days_remaining > 0:
                    progress_lines.append(
                        f"{role.mention}: **{days_remaining}** days remaining to unlock"
                    )
        
        # If we found any relevant roles, add them to the embed
        if progress_lines:
            # Join all lines with a newline character
            embed.add_field(
                name="Channel Unlock Progress", 
                value="\n".join(progress_lines), 
                inline=False
            )

        await ctx.send(embed=embed)

    # --- Configuration Commands ---

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
        Add a target number of days to a role.
        Usage: [p]aboutmeset roles add @Role 365
        """
        if days < 1:
            return await ctx.send("Please enter a positive number of days.")

        async with self.config.guild(ctx.guild).role_targets() as targets:
            targets[str(role.id)] = days
        
        await ctx.send(f"Added configuration: Users with **{role.name}** will see a countdown to **{days}** days.")

    @aboutmeset_roles.command(name="remove")
    async def roles_remove(self, ctx, role: discord.Role):
        """
        Remove a role from the tracking list.
        Usage: [p]aboutmeset roles remove @Role
        """
        async with self.config.guild(ctx.guild).role_targets() as targets:
            role_id = str(role.id)
            if role_id in targets:
                del targets[role_id]
                await ctx.send(f"Removed configuration for **{role.name}**.")
            else:
                await ctx.send("That role is not currently configured.")

    @aboutmeset_roles.command(name="list")
    async def roles_list(self, ctx):
        """List all configured roles and their day targets."""
        targets = await self.config.guild(ctx.guild).role_targets()
        
        if not targets:
            return await ctx.send("No roles are currently configured.")

        lines = []
        for role_id, days in targets.items():
            role = ctx.guild.get_role(int(role_id))
            if role:
                lines.append(f"{role.mention}: {days} days")
            else:
                # Handle case where role was deleted from server but exists in config
                lines.append(f"*(Deleted Role {role_id})*: {days} days")

        embed = discord.Embed(
            title="AboutMe Role Configurations",
            description="\n".join(lines),
            color=await ctx.embed_color()
        )
        await ctx.send(embed=embed)