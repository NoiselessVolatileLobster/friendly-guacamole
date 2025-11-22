import discord
from redbot.core import commands, Config
from datetime import datetime, timezone
import json
from typing import Literal

class ChannelNavigatorView(discord.ui.View):
    """View for interactive channel navigation."""
    def __init__(self, ctx, config_data):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.config_data = config_data
        self.guild = ctx.guild
        
        self.setup_buttons()

    def setup_buttons(self):
        # 1. Add Green Buttons for Public Categories
        sorted_items = sorted(self.config_data.items(), key=lambda x: x[1]['label'])

        for cat_id, data in sorted_items:
            if data['type'] == 'public':
                button = discord.ui.Button(
                    style=discord.ButtonStyle.success,
                    label=data['label'],
                    custom_id=f"public_{cat_id}"
                )
                button.callback = self.make_callback_public(cat_id, data['label'])
                self.add_item(button)

        # 2. Add Red "Secret" Button
        secret_btn = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Secret",
            custom_id="secret_btn",
            row=4 # Push to bottom row if possible
        )
        secret_btn.callback = self.secret_callback
        self.add_item(secret_btn)

        # 3. Add Grey "Voice" Button
        voice_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Voice",
            custom_id="voice_btn",
            row=4
        )
        voice_btn.callback = self.voice_callback
        self.add_item(voice_btn)

    def make_callback_public(self, cat_id, label):
        """Factory to create specific callbacks for loop variables."""
        async def callback(interaction: discord.Interaction):
            category = self.guild.get_channel(int(cat_id))
            
            if not category:
                return await interaction.response.send_message("This category no longer exists.", ephemeral=True)
            
            channels_list = []
            # Filter for text-like channels that can be mentioned
            for channel in category.channels:
                if isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel)):
                     channels_list.append(channel.mention)
            
            desc = "\n".join(channels_list) if channels_list else "No channels found."
            
            embed = discord.Embed(
                title=f"Category: {label}",
                description=desc,
                color=discord.Color.green()
            )
            # Edit the original message with the new embed, keep the view
            await interaction.response.edit_message(embed=embed, view=self)
        
        return callback

    async def secret_callback(self, interaction: discord.Interaction):
        count = 0
        for cat_id, data in self.config_data.items():
            if data['type'] == 'secret':
                category = self.guild.get_channel(int(cat_id))
                if category:
                    count += len(category.channels)
        
        embed = discord.Embed(
            title="Secret Channels",
            description=f"There are currently **{count}** secret channels.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def voice_callback(self, interaction: discord.Interaction):
        voice_lines = []
        
        for cat_id, data in self.config_data.items():
            category = self.guild.get_channel(int(cat_id))
            if category:
                for channel in category.voice_channels:
                    voice_lines.append(f"{channel.mention} ({channel.name})")
                
                for channel in category.stage_channels:
                    voice_lines.append(f"{channel.mention} ({channel.name})")

        desc = "\n".join(voice_lines) if voice_lines else "No voice channels found in tracked categories."

        embed = discord.Embed(
            title="Voice Channels",
            description=desc,
            color=discord.Color.light_grey()
        )
        await interaction.response.edit_message(embed=embed, view=self)


class AboutMe(commands.Cog):
    """A cog to show you information about yourself, the server, its channels and users.."""

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
            "role_target_overrides": {},
            "channel_categories": {} # {category_id: {'type': 'public'|'secret', 'label': 'Name'}}
        }
        self.config.register_guild(**default_guild)

    async def _process_member_status(self, ctx, member: discord.Member):
        """Helper function to generate the member status embed."""
        
        if member.joined_at is None:
            await ctx.send("I couldn't determine when that member joined this server.")
            return None

        # --- 1. Time Calculation (Line 1) ---
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days_in_server = delta.days
        date_str = joined_at.strftime("%B %d, %Y")
        
        # Line 1: Joined on...
        base_description = f"Joined on {date_str} ({days_in_server} days ago)"

        # --- 2. Egg Status | House (Line 2) ---
        egg_roles_config = await self.config.guild(ctx.guild).egg_status_roles()
        egg_parts = []
        for role_id_str, emoji in egg_roles_config.items():
            role_id = int(role_id_str)
            egg_role = ctx.guild.get_role(role_id)
            if egg_role and egg_role in member.roles:
                egg_parts.append(f"{emoji} {egg_role.name}")

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
        location_roles_config = await self.config.guild(ctx.guild).location_roles()
        location_parts = []
        for role_id_str, emoji in location_roles_config.items():
            role_id = int(role_id_str)
            location_role = ctx.guild.get_role(role_id)
            if location_role and location_role in member.roles:
                location_parts.append(f"{emoji} {location_role.name}")

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

        # --- 4. Activity Status (Line 4) ---
        activity_output = ""
        ouija_cog = self.bot.get_cog("OuijaPoke")
        
        if ouija_cog and hasattr(ouija_cog, "get_member_activity_state"):
            try:
                status_data = await ouija_cog.get_member_activity_state(member)
                status = status_data.get('status', 'unknown')
                is_hibernating = status_data.get('is_hibernating', False)
                days_inactive = status_data.get('days_inactive')

                if is_hibernating:
                    emoji = "💤"
                    status_text = "Hibernating"
                    activity_output = f"\n{emoji}{status_text}" 
                elif days_inactive is None:
                    emoji = "❓"
                    status_text = "Unknown"
                    last_seen_text = " (unknown last seen date)"
                    activity_output = f"\n{emoji}{status_text}{last_seen_text}"
                else:
                    emoji_map = {
                        "active": "✅",
                        "poke_eligible": "👉",
                        "summon_eligible": "👻",
                        "unknown": "❓"
                    }
                    emoji = emoji_map.get(status, "❓")
                    status_text = status.capitalize().replace('_', ' ')
                    last_seen_text = f" (last seen {days_inactive} days ago)"
                    activity_output = f"\n{emoji}{status_text}{last_seen_text}"
                
            except Exception as e:
                print(f"Warning: Could not get OuijaPoke activity for {member.name}. Error: {e}")
        
        # --- 5. Awards (Line 5) ---
        award_roles_config = await self.config.guild(ctx.guild).award_roles()
        award_parts = []

        for role_id in award_roles_config:
            award_role = ctx.guild.get_role(int(role_id))
            if award_role and award_role in member.roles:
                award_parts.append(f"{award_role.name}")

        award_output = ""
        if award_parts:
            award_output = f"\n**Awards:** {', '.join(award_parts)}"

        # --- 6. Teams (Line 6) ---
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
            
            if not has_base_role:
                continue 

            if days_in_server < target_days:
                remaining = target_days - days_in_server
                progress_lines.append(f"{mention}: **{remaining}** days remaining to unlock")
            else:
                if has_buddy_role:
                    progress_lines.append(f"{mention}: Unlocked ✅")
                else:
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
            activity_output + # Activity Status
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

    async def _display_server_info(self, ctx):
        """Displays detailed server information embed."""
        guild = ctx.guild
        
        # 1. Channel Counts from Config
        categories_config = await self.config.guild(guild).channel_categories()
        public_count = 0
        secret_count = 0
        
        for cat_id, data in categories_config.items():
            category = guild.get_channel(int(cat_id))
            if not category:
                continue
            
            # Count text-based channels (Text, News, Forum) excluding Voice/Stage
            # This logic ensures we count "readable" channels for the Public/Secret stats
            c_count = 0
            for c in category.channels:
                if isinstance(c, (discord.TextChannel, discord.ForumChannel)):
                    c_count += 1
            
            if data['type'] == 'public':
                public_count += c_count
            elif data['type'] == 'secret':
                secret_count += c_count

        # Global Voice Count
        voice_count = len(guild.voice_channels) + len(guild.stage_channels)

        # 2. Data Preparation
        desc_text = guild.description if guild.description else ""
        
        created_ts = int(guild.created_at.timestamp())
        created_str = f"<t:{created_ts}:D> (<t:{created_ts}:R>)"
        
        member_count = guild.member_count
        role_count = len(guild.roles)
        emoji_count = len(guild.emojis)
        boost_count = guild.premium_subscription_count

        # 3. Build Description
        description = (
            f"{desc_text}\n\n"
            f"**Founded:** {created_str}\n"
            f"**Members:** {member_count}\n"
            f"**Channels:**\n"
            f" • Public: {public_count}\n"
            f" • Secret: {secret_count}\n"
            f" • Voice: {voice_count}\n"
            f"**Roles:** {role_count}\n"
            f"**Emojis:** {emoji_count}\n"
            f"**Boosts:** {boost_count}"
        )

        # 4. Add Member Locations from WhereAreWe Cog
        wherearewe_cog = self.bot.get_cog("WhereAreWe")
        locations_output = ""
        if wherearewe_cog and hasattr(wherearewe_cog, "get_tracked_role_member_counts"):
            try:
                # Note: get_tracked_role_member_counts is async, so we await it
                location_data = await wherearewe_cog.get_tracked_role_member_counts(guild)
                
                if location_data:
                    location_lines = []
                    total_tracked = 0
                    for item in location_data:
                        role_name = item['role_name']
                        member_count = item['member_count']
                        emoji = item['emoji']
                        
                        # Only display if count is not zero
                        if member_count > 0:
                            location_lines.append(f"{emoji} **{role_name}**: {member_count}")
                            total_tracked += member_count
                    
                    if location_lines:
                        locations_output = "\n\n**Member Locations:**\n" + "\n".join(location_lines)
                        # Optional: Add total count if desired
                        # locations_output += f"\n*Total Tracked: {total_tracked}*"
            except Exception as e:
                print(f"Error fetching WhereAreWe data: {e}")
                # Fail silently or log error, don't break the whole embed

        # Append locations to description
        description += locations_output

        embed = discord.Embed(
            title=guild.name,
            description=description,
            color=await ctx.embed_color()
        )
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        await ctx.send(embed=embed)

    async def _display_channel_info(self, ctx):
        """Displays interactive channel navigator view."""
        categories_config = await self.config.guild(ctx.guild).channel_categories()
        
        if not categories_config:
            return await ctx.send("No channels have been configured by the admins yet.")

        view = ChannelNavigatorView(ctx, categories_config)
        
        # Default embed (Landing page)
        embed = discord.Embed(
            title="Channel Navigator", 
            description="Select a category below to view channels.", 
            color=discord.Color.dark_theme()
        )
        embed.set_footer(text="Navigate using the buttons below.")
        
        await ctx.send(embed=embed, view=view)

    # ------------------------------------------------------------------
    # USER COMMANDS
    # ------------------------------------------------------------------

    @commands.command()
    @commands.guild_only()
    async def about(self, ctx, *, argument: str = None):
        """
        Check information about me, a user, the server, or channels.
        """
        
        if argument is None:
            p = ctx.clean_prefix
            return await ctx.send(
                f"`{p}about me` -> See information about yourself.\n"
                f"`{p}about @user` -> See information about another user.\n"
                f"`{p}about channel` -> See information about channels in this server.\n"
                f"`{p}about server` -> See information about this server."
            )

        arg_lower = argument.lower()

        # Case 1: "me"
        if arg_lower == "me":
            embed = await self._process_member_status(ctx, ctx.author)
            if embed:
                await ctx.send(embed=embed)
            return

        # Case 2: "server"
        if arg_lower == "server":
            await self._display_server_info(ctx)
            return

        # Case 3: "channel" or "channels"
        if arg_lower in ["channel", "channels"]:
            await self._display_channel_info(ctx)
            return

        # Case 4: Member (Mention, ID, or Name)
        try:
            converter = commands.MemberConverter()
            member = await converter.convert(ctx, argument)
            embed = await self._process_member_status(ctx, member)
            if embed:
                await ctx.send(embed=embed)
        except commands.BadArgument:
            p = ctx.clean_prefix
            await ctx.send(f"Could not find that user or recognize the command argument. Options are: `me`, `server`, `channel`, or a member. Try `{p}about` for help.")

    # ------------------------------------------------------------------
    # ADMIN COMMANDS
    # ------------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def aboutmeset(self, ctx):
        """Settings for the AboutMe cog."""
        pass

    @aboutmeset.command(name="debugactivity")
    async def aboutmeset_debugactivity(self, ctx, member: discord.Member):
        """[ADMIN] Displays raw OuijaPoke activity data."""
        ouija_cog = self.bot.get_cog("OuijaPoke")
        if not ouija_cog or not hasattr(ouija_cog, "get_member_activity_state"):
            return await ctx.send("OuijaPoke cog not loaded or incompatible.")
        try:
            status_data = await ouija_cog.get_member_activity_state(member)
            formatted_data = json.dumps(status_data, indent=4)
            await ctx.send(f"Raw Data for **{member.display_name}**:\n```json\n{formatted_data}\n```")
        except Exception as e:
            await ctx.send(f"Error: `{e}`")

    # --- Channel/Category Management ---
    @aboutmeset.group(name="channel")
    async def aboutmeset_channel(self, ctx):
        """Manage channel categories for the navigator."""
        pass

    @aboutmeset_channel.command(name="add")
    async def channel_add(self, ctx, category: discord.CategoryChannel, type: Literal["public", "secret"], *, label: str):
        """
        Add a category to the channel navigator.
        Type must be 'public' or 'secret'. Label is the button text.
        """
        async with self.config.guild(ctx.guild).channel_categories() as cats:
            cats[str(category.id)] = {
                "type": type.lower(),
                "label": label
            }
        await ctx.send(f"Added category **{category.name}** as `{type}` with label **{label}**.")

    @aboutmeset_channel.command(name="remove")
    async def channel_remove(self, ctx, category: discord.CategoryChannel):
        """Remove a category from the channel navigator."""
        async with self.config.guild(ctx.guild).channel_categories() as cats:
            if str(category.id) in cats:
                del cats[str(category.id)]
                await ctx.send(f"Removed **{category.name}** from tracking.")
            else:
                await ctx.send("That category is not currently tracked.")

    @aboutmeset_channel.command(name="list")
    async def channel_list(self, ctx):
        """List configured channel categories."""
        cats = await self.config.guild(ctx.guild).channel_categories()
        if not cats:
            return await ctx.send("No channel categories configured.")
        
        msg = ""
        for cat_id, data in cats.items():
            cat_obj = ctx.guild.get_channel(int(cat_id))
            cat_name = cat_obj.name if cat_obj else "Unknown/Deleted"
            msg += f"**{data['label']}** ({cat_name}) - Type: `{data['type']}`\n"
        
        await ctx.send(embed=discord.Embed(title="Tracked Channel Categories", description=msg, color=discord.Color.blue()))

    # --- Location Role Management ---
    @aboutmeset.group(name="locations")
    async def aboutmeset_locations(self, ctx):
        """Manage location roles."""
        pass

    @aboutmeset_locations.command(name="add")
    async def locations_add(self, ctx, role: discord.Role, emoji: str):
        """Add location role."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            locations[str(role.id)] = emoji
        await ctx.send(f"Added location role **{role.name}** with {emoji}")

    @aboutmeset_locations.command(name="remove")
    async def locations_remove(self, ctx, role: discord.Role):
        """Remove location role."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            if str(role.id) in locations:
                del locations[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from locations.")
            else:
                await ctx.send("Role not found in locations.")

    @aboutmeset_locations.command(name="list")
    async def locations_list(self, ctx):
        """List location roles."""
        locations = await self.config.guild(ctx.guild).location_roles()
        if not locations:
            return await ctx.send("No location roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in locations.items()]
        await ctx.send(embed=discord.Embed(title="Location Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- DM Status Management ---
    @aboutmeset.group(name="dmstatus")
    async def aboutmeset_dmstatus(self, ctx):
        """Manage DM Status roles."""
        pass

    @aboutmeset_dmstatus.command(name="add")
    async def dmstatus_add(self, ctx, role: discord.Role, emoji: str):
        """Add DM status role."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            statuses[str(role.id)] = emoji
        await ctx.send(f"Added DM status role **{role.name}** with {emoji}")

    @aboutmeset_dmstatus.command(name="remove")
    async def dmstatus_remove(self, ctx, role: discord.Role):
        """Remove DM status role."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            if str(role.id) in statuses:
                del statuses[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from DM statuses.")
            else:
                await ctx.send("Role not found in DM statuses.")

    @aboutmeset_dmstatus.command(name="list")
    async def dmstatus_list(self, ctx):
        """List DM status roles."""
        statuses = await self.config.guild(ctx.guild).dm_status_roles()
        if not statuses:
            return await ctx.send("No DM status roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in statuses.items()]
        await ctx.send(embed=discord.Embed(title="DM Status Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Award Role Management ---
    @aboutmeset.group(name="award")
    async def aboutmeset_award(self, ctx):
        """Manage Award roles."""
        pass

    @aboutmeset_award.command(name="add")
    async def award_add(self, ctx, role: discord.Role):
        """Add award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id not in awards:
                awards.append(role.id)
                await ctx.send(f"Added **{role.name}** to awards.")
            else:
                await ctx.send("Role already in awards.")

    @aboutmeset_award.command(name="remove")
    async def award_remove(self, ctx, role: discord.Role):
        """Remove award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id in awards:
                awards.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from awards.")
            else:
                await ctx.send("Role not found in awards.")

    @aboutmeset_award.command(name="list")
    async def award_list(self, ctx):
        """List award roles."""
        awards = await self.config.guild(ctx.guild).award_roles()
        if not awards:
            return await ctx.send("No award roles configured.")
        lines = [ctx.guild.get_role(rid).name if ctx.guild.get_role(rid) else 'Deleted' for rid in awards]
        await ctx.send(embed=discord.Embed(title="Award Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Helper Role Management ---
    @aboutmeset.group(name="helper")
    async def aboutmeset_helper(self, ctx):
        """Manage Helper roles."""
        pass

    @aboutmeset_helper.command(name="add")
    async def helper_add(self, ctx, role: discord.Role):
        """Add helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id not in helpers:
                helpers.append(role.id)
                await ctx.send(f"Added **{role.name}** to helpers.")
            else:
                await ctx.send("Role already in helpers.")

    @aboutmeset_helper.command(name="remove")
    async def helper_remove(self, ctx, role: discord.Role):
        """Remove helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id in helpers:
                helpers.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from helpers.")
            else:
                await ctx.send("Role not found in helpers.")

    @aboutmeset_helper.command(name="list")
    async def helper_list(self, ctx):
        """List helper roles."""
        helpers = await self.config.guild(ctx.guild).helper_roles()
        if not helpers:
            return await ctx.send("No helper roles configured.")
        lines = [ctx.guild.get_role(rid).name if ctx.guild.get_role(rid) else 'Deleted' for rid in helpers]
        await ctx.send(embed=discord.Embed(title="Helper Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- House Role Management ---
    @aboutmeset.group(name="houseroles")
    async def aboutmeset_houseroles(self, ctx):
        """Manage House roles."""
        pass

    @aboutmeset_houseroles.command(name="add")
    async def houseroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add House role."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            house_roles[str(role.id)] = emoji
        await ctx.send(f"Added House role **{role.name}** with {emoji}")

    @aboutmeset_houseroles.command(name="remove")
    async def houseroles_remove(self, ctx, role: discord.Role):
        """Remove House role."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            if str(role.id) in house_roles:
                del house_roles[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from House roles.")
            else:
                await ctx.send("Role not found in House roles.")

    @aboutmeset_houseroles.command(name="list")
    async def houseroles_list(self, ctx):
        """List House roles."""
        house_roles = await self.config.guild(ctx.guild).house_roles()
        if not house_roles:
            return await ctx.send("No House roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in house_roles.items()]
        await ctx.send(embed=discord.Embed(title="House Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Egg Status Role Management ---
    @aboutmeset.group(name="eggroles")
    async def aboutmeset_eggroles(self, ctx):
        """Manage Egg Status roles."""
        pass

    @aboutmeset_eggroles.command(name="add")
    async def eggroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add Egg Status role."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            egg_roles[str(role.id)] = emoji
        await ctx.send(f"Added Egg Status role **{role.name}** with {emoji}")

    @aboutmeset_eggroles.command(name="remove")
    async def eggroles_remove(self, ctx, role: discord.Role):
        """Remove Egg Status role."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            if str(role.id) in egg_roles:
                del egg_roles[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from Egg Status roles.")
            else:
                await ctx.send("Role not found in Egg Status roles.")

    @aboutmeset_eggroles.command(name="list")
    async def eggroles_list(self, ctx):
        """List Egg Status roles."""
        egg_roles = await self.config.guild(ctx.guild).egg_status_roles()
        if not egg_roles:
            return await ctx.send("No Egg Status roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in egg_roles.items()]
        await ctx.send(embed=discord.Embed(title="Egg Status Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Role Progress Management ---
    @aboutmeset.group(name="roles")
    async def aboutmeset_roles(self, ctx):
        """Manage role targets."""
        pass

    @aboutmeset_roles.command(name="add")
    async def roles_add(self, ctx, role: discord.Role, days: int):
        """Add role target."""
        async with self.config.guild(ctx.guild).role_targets() as targets:
            targets[str(role.id)] = days
        await ctx.send(f"Configured **{role.name}** with target of **{days}** days.")

    @aboutmeset_roles.command(name="link")
    async def roles_link(self, ctx, base_role: discord.Role, buddy_role: discord.Role):
        """Link buddy role."""
        base_id = str(base_role.id)
        targets = await self.config.guild(ctx.guild).role_targets()
        if base_id not in targets:
            return await ctx.send("Base role not configured yet.")
        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if base_id not in buddies:
                buddies[base_id] = []
            buddies[base_id].append(str(buddy_role.id))
        await ctx.send(f"Linked **{buddy_role.name}** to **{base_role.name}**.")

    @aboutmeset_roles.command(name="unlink")
    async def roles_unlink(self, ctx, base_role: discord.Role, buddy_role: discord.Role):
        """Unlink buddy role."""
        base_id = str(base_role.id)
        buddy_id = str(buddy_role.id)
        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if base_id in buddies and buddy_id in buddies[base_id]:
                buddies[base_id].remove(buddy_id)
                await ctx.send(f"Unlinked **{buddy_role.name}** from **{base_role.name}**.")
            else:
                await ctx.send("Link not found.")

    @aboutmeset_roles.command(name="linktarget")
    async def roles_linktarget(self, ctx, base_role: discord.Role, target_role: discord.Role):
        """Link target override."""
        base_id = str(base_role.id)
        targets = await self.config.guild(ctx.guild).role_targets()
        if base_id not in targets:
            return await ctx.send("Base role not configured.")
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            overrides[base_id] = str(target_role.id)
        await ctx.send(f"Linked target **{target_role.name}** to **{base_role.name}**.")

    @aboutmeset_roles.command(name="unlinktarget")
    async def roles_unlinktarget(self, ctx, base_role: discord.Role):
        """Unlink target override."""
        base_id = str(base_role.id)
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            if base_id in overrides:
                del overrides[base_id]
                await ctx.send(f"Removed target override for **{base_role.name}**.")
            else:
                await ctx.send("Target link not found.")

    @aboutmeset_roles.command(name="remove")
    async def roles_remove(self, ctx, role: discord.Role):
        """Remove role config."""
        role_id = str(role.id)
        async with self.config.guild(ctx.guild).role_targets() as targets:
            if role_id in targets:
                del targets[role_id]
            else:
                return await ctx.send("Role not configured.")
        async with self.config.guild(ctx.guild).role_buddies() as buddies:
            if role_id in buddies:
                del buddies[role_id]
        async with self.config.guild(ctx.guild).role_target_overrides() as overrides:
            if role_id in overrides:
                del overrides[role_id]
        await ctx.send(f"Removed config for **{role.name}**.")

    @aboutmeset_roles.command(name="list")
    async def roles_list(self, ctx):
        """List role configs."""
        targets = await self.config.guild(ctx.guild).role_targets()
        buddies = await self.config.guild(ctx.guild).role_buddies()
        overrides = await self.config.guild(ctx.guild).role_target_overrides()
        if not targets:
            return await ctx.send("No roles configured.")
        lines = []
        for rid, days in targets.items():
            role = ctx.guild.get_role(int(rid))
            rname = role.mention if role else "Deleted"
            btext = ""
            if rid in buddies:
                bnames = [ctx.guild.get_role(int(bid)).mention if ctx.guild.get_role(int(bid)) else "Unknown" for bid in buddies[rid]]
                btext = f" ➡️ Buddies: {', '.join(bnames)}"
            ttext = ""
            if rid in overrides:
                trole = ctx.guild.get_role(int(overrides[rid]))
                tname = trole.mention if trole else "Unknown"
                ttext = f" 🎯 Target: {tname}"
            lines.append(f"{rname}: **{days}** days{btext}{ttext}")
        await ctx.send(embed=discord.Embed(title="Role Configs", description="\n".join(lines), color=await ctx.embed_color()))