import discord
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import box, humanize_list, pagify
from datetime import datetime, timedelta, timezone
import asyncio

class Bonk(commands.Cog):
    """
    Go to Horny Jail.
    
    Bonk users, track their bonks, and send them to jail with WarnSystem integration.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        default_guild = {
            "bonk_threshold": 5,
            "jail_role_id": None,
            "jail_time_hours": 1,
            "jail_warnings": {},  # Format: {"jail_count_str": {"level": int, "reason": str}}
            "log_channel_id": None,
        }

        default_member = {
            "bonk_count": 0,
            "jail_count": 0,
            "jail_release_timestamp": 0,
            "bonks_sent": 0,
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        self.jail_check_loop = self.bot.loop.create_task(self.check_jail_sentences())

    def cog_unload(self):
        if self.jail_check_loop:
            self.jail_check_loop.cancel()

    async def check_jail_sentences(self):
        """Background loop to check for users who have served their time."""
        await self.bot.wait_until_ready()
        while True:
            try:
                # We check every minute
                current_time = datetime.now(timezone.utc).timestamp()
                
                all_members = await self.config.all_members()
                
                for guild_id, members in all_members.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                        
                    jail_role_id = await self.config.guild(guild).jail_role_id()
                    if not jail_role_id:
                        continue
                        
                    role = guild.get_role(jail_role_id)
                    if not role:
                        continue

                    for member_id, data in members.items():
                        release_time = data.get("jail_release_timestamp", 0)
                        
                        if release_time != 0 and current_time >= release_time:
                            # Time to release
                            member = guild.get_member(member_id)
                            if member:
                                try:
                                    await member.remove_roles(role, reason="Served time in Horny Jail")
                                except discord.Forbidden:
                                    pass # Bot lacks permissions
                                except discord.HTTPException:
                                    pass
                            
                            # Reset timestamp so we don't check again
                            await self.config.member_from_ids(guild_id, member_id).jail_release_timestamp.set(0)

            except Exception as e:
                print(f"Error in Bonk jail loop: {e}")
            
            await asyncio.sleep(60)

    @app_commands.command(name="bonk", description="Bonk a user. If they get bonked enough, they go to jail.")
    @app_commands.describe(user="The user to bonk")
    async def bonk_slash(self, interaction: discord.Interaction, user: discord.Member):
        """Slash command to anonymously bonk a user."""
        if user.bot:
            await interaction.response.send_message("You cannot bonk a bot!", ephemeral=True)
            return
        
        if user.id == interaction.user.id:
            await interaction.response.send_message("You cannot bonk yourself!", ephemeral=True)
            return

        guild = interaction.guild
        member_conf = self.config.member(user)
        bonker_conf = self.config.member(interaction.user)
        guild_conf = self.config.guild(guild)

        # Track sender stats
        await bonker_conf.bonks_sent.set(await bonker_conf.bonks_sent() + 1)

        # Increment Bonk on receiver
        current_bonks = await member_conf.bonk_count() + 1
        threshold = await guild_conf.bonk_threshold()
        
        await member_conf.bonk_count.set(current_bonks)
        
        message = f"You have bonked {user.mention}. They are at {current_bonks}/{threshold} bonks."
        
        # Logging
        log_channel_id = await guild_conf.log_channel_id()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(title="🔨 Bonk Log", color=discord.Color.orange())
                embed.add_field(name="Bonker", value=interaction.user.mention, inline=True)
                embed.add_field(name="Bonked", value=user.mention, inline=True)
                embed.add_field(name="Count", value=f"{current_bonks}/{threshold}", inline=True)
                embed.timestamp = datetime.now(timezone.utc)
                try:
                    await log_channel.send(embed=embed)
                except discord.Forbidden:
                    pass

        # Check Threshold
        if current_bonks >= threshold:
            # Send to Jail
            await member_conf.bonk_count.set(0)
            current_jails = await member_conf.jail_count() + 1
            await member_conf.jail_count.set(current_jails)
            
            # Apply Role
            role_id = await guild_conf.jail_role_id()
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await user.add_roles(role, reason="Too many bonks. Horny Jail.")
                    except discord.Forbidden:
                        message += "\n\nI tried to jail them, but I don't have permission to manage roles."
            
            # Set Timer
            hours = await guild_conf.jail_time_hours()
            release_dt = datetime.now(timezone.utc) + timedelta(hours=hours)
            await member_conf.jail_release_timestamp.set(release_dt.timestamp())
            
            message += f"\n\n**JAILED!** User has been sent to Horny Jail for {hours} hour(s)."

            # WarnSystem Integration
            jail_warnings = await guild_conf.jail_warnings()
            jail_key = str(current_jails)
            
            if jail_key in jail_warnings:
                warn_data = jail_warnings[jail_key]
                level = warn_data["level"]
                reason = warn_data["reason"]
                
                warn_cog = self.bot.get_cog("WarnSystem")
                if warn_cog:
                    try:
                        # Warning comes from the bot
                        await warn_cog.api.warn(user, self.bot.user, reason, level)
                        message += f"\nWarnSystem warning applied (Level {level})."
                    except Exception as e:
                        message += f"\nFailed to apply warning: {e}"
                else:
                    message += "\n(WarnSystem cog not found, skipping warning)"

        await interaction.response.send_message(message, ephemeral=True)

    @commands.command(name="bonkstats")
    async def bonkstats(self, ctx):
        """Show the Bonk Dashboard: Top senders, receivers, and jailbirds."""
        members = await self.config.all_members(ctx.guild)
        
        if not members:
            return await ctx.send("No bonk stats available yet.")

        # Sorting helper
        def get_top(data_dict, key, limit=3):
            # Sort by the specific key in reverse (descending)
            sorted_data = sorted(data_dict.items(), key=lambda x: x[1].get(key, 0), reverse=True)
            # Filter out zeros and take top N
            return [(m_id, d.get(key, 0)) for m_id, d in sorted_data if d.get(key, 0) > 0][:limit]

        most_bonked = get_top(members, "bonk_count")
        most_sent = get_top(members, "bonks_sent")
        most_jailed = get_top(members, "jail_count")

        embed = discord.Embed(title="📊 Bonk Dashboard", color=discord.Color.gold())

        def format_list(stats_list):
            if not stats_list:
                return "None yet!"
            lines = []
            for i, (m_id, count) in enumerate(stats_list, 1):
                user = ctx.guild.get_member(m_id)
                name = user.display_name if user else f"User {m_id}"
                lines.append(f"{i}. **{name}**: {count}")
            return "\n".join(lines)

        embed.add_field(name="🤕 Most Bonked (Current)", value=format_list(most_bonked), inline=False)
        embed.add_field(name="🔨 Top Bonkers", value=format_list(most_sent), inline=False)
        embed.add_field(name="🚔 Most Jailed (All time)", value=format_list(most_jailed), inline=False)

        await ctx.send(embed=embed)

    @commands.group(name="bonkset")
    @commands.admin_or_permissions(administrator=True)
    async def bonkset(self, ctx):
        """Configuration settings for Bonk."""
        pass

    @bonkset.command(name="threshold")
    async def bonkset_threshold(self, ctx, count: int):
        """Set how many bonks are required to send someone to jail."""
        if count < 1:
            return await ctx.send("Threshold must be at least 1.")
        await self.config.guild(ctx.guild).bonk_threshold.set(count)
        await ctx.send(f"Bonk threshold set to {count}.")

    @bonkset.command(name="role")
    async def bonkset_role(self, ctx, role: discord.Role):
        """Set the Horny Jail role."""
        await self.config.guild(ctx.guild).jail_role_id.set(role.id)
        await ctx.send(f"Jail role set to {role.name}.")

    @bonkset.command(name="time")
    async def bonkset_time(self, ctx, hours: int):
        """Set how many hours a user stays in jail."""
        if hours < 1:
            return await ctx.send("Time must be at least 1 hour.")
        await self.config.guild(ctx.guild).jail_time_hours.set(hours)
        await ctx.send(f"Jail time set to {hours} hours.")

    @bonkset.command(name="logchannel")
    async def bonkset_logchannel(self, ctx, channel: discord.TextChannel = None):
        """Set the logging channel for bonks. Leave empty to disable."""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"Bonk logging channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("Bonk logging disabled.")

    @bonkset.command(name="list")
    async def bonkset_list(self, ctx):
        """List all users with bonk stats."""
        members = await self.config.all_members(ctx.guild)
        
        if not members:
            return await ctx.send("No stats recorded.")
            
        lines = []
        lines.append(f"{'User':<30} | {'Bonked':<8} | {'Jailed':<8} | {'Sent':<8}")
        lines.append("-" * 65)
        
        for m_id, data in members.items():
            # Only show if they have at least one stat
            b_count = data.get("bonk_count", 0)
            j_count = data.get("jail_count", 0)
            s_count = data.get("bonks_sent", 0)
            
            if b_count == 0 and j_count == 0 and s_count == 0:
                continue
                
            user = ctx.guild.get_member(m_id)
            name = str(user) if user else f"ID: {m_id}"
            
            lines.append(f"{name:<30} | {b_count:<8} | {j_count:<8} | {s_count:<8}")

        if len(lines) == 2: # Only header exists
            return await ctx.send("No active stats found.")

        msg = "\n".join(lines)
        for page in pagify(msg):
            await ctx.send(box(page, lang="text"))

    @bonkset.group(name="warning")
    async def bonkset_warning(self, ctx):
        """Configure WarnSystem warnings based on Jail count."""
        pass

    @bonkset_warning.command(name="add")
    async def warning_add(self, ctx, jail_count: int, level: int, *, reason: str):
        """
        Add a warning trigger.
        
        Example: [p]bonkset warning add 3 2 Repeat Offender
        (At 3rd jail visit, give Level 2 warning with reason 'Repeat Offender')
        """
        if jail_count < 1:
            return await ctx.send("Jail count must be at least 1.")
        
        async with self.config.guild(ctx.guild).jail_warnings() as warnings:
            warnings[str(jail_count)] = {"level": level, "reason": reason}
        
        await ctx.send(f"Configuration saved: Upon entering jail for the {jail_count}th time, user gets Level {level} warning.")

    @bonkset_warning.command(name="remove")
    async def warning_remove(self, ctx, jail_count: int):
        """Remove a warning trigger for a specific jail count."""
        async with self.config.guild(ctx.guild).jail_warnings() as warnings:
            key = str(jail_count)
            if key in warnings:
                del warnings[key]
                await ctx.send(f"Removed warning trigger for jail count {jail_count}.")
            else:
                await ctx.send("No warning configured for that jail count.")

    @bonkset.command(name="view")
    async def bonkset_view(self, ctx):
        """View all current Bonk settings."""
        conf = await self.config.guild(ctx.guild).all()
        
        role_msg = "Not Set"
        if conf['jail_role_id']:
            role = ctx.guild.get_role(conf['jail_role_id'])
            role_msg = role.mention if role else "Role Deleted/Invalid"

        log_msg = "Not Set"
        if conf['log_channel_id']:
            chan = ctx.guild.get_channel(conf['log_channel_id'])
            log_msg = chan.mention if chan else "Channel Deleted/Invalid"

        embed = discord.Embed(title="Bonk Settings", color=discord.Color.red())
        embed.add_field(name="Bonk Threshold", value=str(conf['bonk_threshold']), inline=True)
        embed.add_field(name="Jail Time", value=f"{conf['jail_time_hours']} Hours", inline=True)
        embed.add_field(name="Jail Role", value=role_msg, inline=False)
        embed.add_field(name="Log Channel", value=log_msg, inline=False)
        
        warn_text = ""
        if conf['jail_warnings']:
            for count, data in conf['jail_warnings'].items():
                warn_text += f"**{count} Jails:** Level {data['level']} (Reason: {data['reason']})\n"
        else:
            warn_text = "None configured."
            
        embed.add_field(name="Warning Triggers", value=warn_text, inline=False)
        
        await ctx.send(embed=embed)