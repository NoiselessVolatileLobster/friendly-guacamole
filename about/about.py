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
            row=4 # Push to bottom row
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


class About(commands.Cog):
    """A cog to show you information about yourself, the server, its channels and users.."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "location_roles": {},
            "dm_status_roles": {},
            "award_roles": [],
            "helper_roles": [],
            "egg_status_roles": {},
            "house_roles": {},
            "channel_categories": {},
            "first_day_channels": [],
            "first_day_title": "First Day Channels",
            "first_day_description": "Welcome! Here are some channels to get you started:",
            "first_day_thumbnail": "",
            "first_day_image": "",
            "new_member_config": {
                "ephemeral_role": None,
                "posted_intro_role": None,
                "no_intro_role": None,
                "general_only_role": None,
                "general_only_level": 0
            },
            "optin_roles": {}, # { "base_role_id": { "target_id": int, "days": int, "level": int } }
            "reward_roles": {} # { "reward_role_id": { "days": int, "level": int } }
        }
        self.config.register_guild(**default_guild)

    async def _process_member_status(self, ctx, member: discord.Member):
        """Helper function to generate the member status embed."""
        
        if member.joined_at is None:
            await ctx.send("I couldn't determine when that member joined this server.")
            return None

        # --- 1. Level Retrieval ---
        user_level = 0 # Default level
        level_str = ""
        levelup_cog = self.bot.get_cog("LevelUp")
        if levelup_cog:
            try:
                # Based on user info, get_level is async
                user_level = await levelup_cog.get_level(member)
                level_str = f"**Level {user_level}** • "
            except Exception:
                pass 

        # --- 2. Time Calculation (Line 1) ---
        now = datetime.now(timezone.utc)
        joined_at = member.joined_at
        delta = now - joined_at
        days_in_server = delta.days
        date_str = joined_at.strftime("%B %d, %Y")
        
        # Line 1: Level + Joined on...
        base_description = f"{level_str}Joined on {date_str} ({days_in_server} days ago)"

        # --- 3. Egg Status | House (Line 2) ---
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

        # --- 4. Location | DM Status (Line 3) ---
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

        # --- 5. Activity Status (Line 4) ---
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
        
        # --- 6. Awards (Line 5) ---
        award_roles_config = await self.config.guild(ctx.guild).award_roles()
        award_parts = []

        for role_id in award_roles_config:
            award_role = ctx.guild.get_role(int(role_id))
            if award_role and award_role in member.roles:
                award_parts.append(f"{award_role.name}")

        award_output = ""
        if award_parts:
            award_output = f"\n**Awards:** {', '.join(award_parts)}"

        # --- 7. Teams (Line 6) ---
        helper_roles_config = await self.config.guild(ctx.guild).helper_roles()
        helper_parts = []

        for role_id in helper_roles_config:
            helper_role = ctx.guild.get_role(int(role_id))
            if helper_role and helper_role in member.roles:
                helper_parts.append(f"{helper_role.name}")

        helper_output = ""
        if helper_parts:
            helper_output = f"\n**Teams:** {', '.join(helper_parts)}"

        # --- 8. New Member Section ---
        new_member_config = await self.config.guild(ctx.guild).new_member_config()
        nm_output = ""
        
        eph_rid = new_member_config.get("ephemeral_role")
        intro_rid = new_member_config.get("posted_intro_role")
        nointro_rid = new_member_config.get("no_intro_role")
        gen_level = new_member_config.get("general_only_level", 0)

        def has_role(r_id):
            if r_id is None: return False
            return member.get_role(int(r_id)) is not None

        is_ephemeral = has_role(eph_rid)
        has_posted_intro = has_role(intro_rid)
        has_no_intro = has_role(nointro_rid)

        if is_ephemeral:
            nm_output = "\n\n**New Member**\n💨Ephemeral Mode. Cannot see previous messages or reply to users"
        else:
            if user_level < gen_level:
                if has_no_intro:
                    nm_output = "\n\n**New Member**\n🗣️ Chat more and post an intro to unlock the rest of the server"
                elif has_posted_intro:
                    nm_output = "\n\n**New Member**\n🗣️ Chat more to unlock the rest of the server"
            else:
                if has_no_intro:
                    nm_output = "\n\n**New Member**\n🗣️ Post an intro to unlock the rest of the server"
                elif has_posted_intro:
                    nm_output = ""

        # --- 9. Role Progress Calculation ---
        optin_roles = await self.config.guild(ctx.guild).optin_roles()
        reward_roles = await self.config.guild(ctx.guild).reward_roles() 
        progress_lines = []

        # A. Opt-in Roles (Base -> Target)
        for base_role_id, data in optin_roles.items():
            base_role = ctx.guild.get_role(int(base_role_id))
            if not base_role: continue

            target_role_id = data.get("target_id")
            required_days = data.get("days", 0)
            required_level = data.get("level", 0)
            
            target_role = ctx.guild.get_role(int(target_role_id))
            if not target_role: continue

            # 1. Check if user already has the requested (target) role
            if target_role in member.roles:
                progress_lines.append(f"{target_role.mention} Unlocked!")
                continue

            # 2. If not, check if user has the request (base) role
            if base_role in member.roles:
                days_remaining = required_days - days_in_server
                level_met = user_level >= required_level
                days_met = days_remaining <= 0

                if not days_met and not level_met:
                    progress_lines.append(f"{base_role.mention}: Reach Level **{required_level}** and **{days_remaining}** days remaining")
                elif not days_met:
                    progress_lines.append(f"{base_role.mention}: **{days_remaining}** days remaining")
                elif not level_met:
                    progress_lines.append(f"{base_role.mention}: Reach Level **{required_level}**")
                else:
                    progress_lines.append(f"{base_role.mention}: Eligible! ✅")

        # B. Reward Roles (Direct Time/Level Check)
        for reward_role_id, data in reward_roles.items():
            reward_role = ctx.guild.get_role(int(reward_role_id))
            if not reward_role: continue
            
            required_days = data.get("days", 0)
            required_level = data.get("level", 0)
            
            if reward_role in member.roles:
                progress_lines.append(f"{reward_role.mention} Unlocked!")
            else:
                days_remaining = required_days - days_in_server
                level_met = user_level >= required_level
                days_met = days_remaining <= 0

                if not days_met and not level_met:
                    progress_lines.append(f"{reward_role.mention}: Reach Level **{required_level}** and **{days_remaining}** days remaining")
                elif not days_met:
                    progress_lines.append(f"{reward_role.mention}: **{days_remaining}** days remaining")
                elif not level_met:
                    progress_lines.append(f"{reward_role.mention}: Reach Level **{required_level}**")
                else:
                    progress_lines.append(f"{reward_role.mention}: Eligible! ✅")

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
            nm_output +      # New Member
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
                        
                        if member_count > 0:
                            location_lines.append(f"{emoji} **{role_name}**: {member_count}")
                            total_tracked += member_count
                    
                    if location_lines:
                        locations_output = "\n\n**Member Locations:**\n" + "\n".join(location_lines)
            except Exception as e:
                print(f"Error fetching WhereAreWe data: {e}")

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

    async def _display_first_day_info(self, ctx):
        """Displays the first day info embed."""
        settings = await self.config.guild(ctx.guild).all()
        channel_ids = settings['first_day_channels']
        description_text = settings['first_day_description']
        title_text = settings['first_day_title']
        thumb_url = settings['first_day_thumbnail']
        img_url = settings['first_day_image']
        
        if not channel_ids and not description_text:
            return await ctx.send("No First Day content has been configured.")

        lines = []
        for ch_id in channel_ids:
            channel = ctx.guild.get_channel(ch_id)
            if channel:
                lines.append(channel.mention)
        
        channel_list_str = "\n".join(lines) if lines else ""
        
        # Combine description and channels
        final_desc = f"{description_text}\n\n{channel_list_str}"

        embed = discord.Embed(
            title=title_text,
            description=final_desc,
            color=await ctx.embed_color()
        )
        
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)
        
        if img_url:
            embed.set_image(url=img_url)
        
        await ctx.send(embed=embed)

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
                f"`{p}about server` -> See information about this server.\n"
                f"`{p}about firstday` -> See the first day channels."
            )

        arg_lower = argument.lower()

        if arg_lower == "me":
            embed = await self._process_member_status(ctx, ctx.author)
            if embed:
                await ctx.send(embed=embed)
            return

        if arg_lower == "server":
            await self._display_server_info(ctx)
            return

        if arg_lower in ["channel", "channels"]:
            await self._display_channel_info(ctx)
            return

        if arg_lower in ["firstday", "first", "first day"]:
            await self._display_first_day_info(ctx)
            return

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
    async def aboutset(self, ctx):
        """Settings for the About cog."""
        pass

    @aboutset.command(name="debugactivity")
    async def aboutset_debugactivity(self, ctx, member: discord.Member):
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

    # NEW: New Member Configuration Group
    @aboutset.group(name="newmember")
    async def aboutset_newmember(self, ctx):
        """Manage 'New Member' section settings."""
        pass

    @aboutset_newmember.command(name="ephemeral")
    async def nm_ephemeral(self, ctx, role: discord.Role):
        """Set the Ephemeral role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["ephemeral_role"] = role.id
        await ctx.send(f"Ephemeral role set to **{role.name}**.")

    @aboutset_newmember.command(name="removeephemeral")
    async def nm_removeephemeral(self, ctx):
        """Unset the Ephemeral role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["ephemeral_role"] = None
        await ctx.send("Ephemeral role config cleared.")

    @aboutset_newmember.command(name="postedintro")
    async def nm_postedintro(self, ctx, role: discord.Role):
        """Set the 'Posted Intro' role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["posted_intro_role"] = role.id
        await ctx.send(f"Posted Intro role set to **{role.name}**.")

    @aboutset_newmember.command(name="removepostedintro")
    async def nm_removepostedintro(self, ctx):
        """Unset the 'Posted Intro' role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["posted_intro_role"] = None
        await ctx.send("Posted Intro role config cleared.")

    @aboutset_newmember.command(name="nointro")
    async def nm_nointro(self, ctx, role: discord.Role):
        """Set the 'No Intro' role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["no_intro_role"] = role.id
        await ctx.send(f"No Intro role set to **{role.name}**.")

    @aboutset_newmember.command(name="removenointro")
    async def nm_removenointro(self, ctx):
        """Unset the 'No Intro' role."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["no_intro_role"] = None
        await ctx.send("No Intro role config cleared.")

    @aboutset_newmember.command(name="general")
    async def nm_general(self, ctx, role: discord.Role, level: int):
        """Set the 'General Only' role and level threshold."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["general_only_role"] = role.id
            conf["general_only_level"] = level
        await ctx.send(f"General Only role set to **{role.name}** with required level **{level}**.")

    @aboutset_newmember.command(name="removegeneral")
    async def nm_removegeneral(self, ctx):
        """Unset the 'General Only' role and level."""
        async with self.config.guild(ctx.guild).new_member_config() as conf:
            conf["general_only_role"] = None
            conf["general_only_level"] = 0
        await ctx.send("General Only role/level config cleared.")

    # --- Channel/Category Management ---
    @aboutset.group(name="channel")
    async def aboutset_channel(self, ctx):
        """Manage channel categories for the navigator."""
        pass

    @aboutset_channel.command(name="add")
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

    @aboutset_channel.command(name="remove")
    async def channel_remove(self, ctx, category: discord.CategoryChannel):
        """Remove a category from the channel navigator."""
        async with self.config.guild(ctx.guild).channel_categories() as cats:
            if str(category.id) in cats:
                del cats[str(category.id)]
                await ctx.send(f"Removed **{category.name}** from tracking.")
            else:
                await ctx.send("That category is not currently tracked.")

    @aboutset_channel.command(name="list")
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

    # --- First Day Channel Management ---
    @aboutset.group(name="firstday")
    async def aboutset_firstday(self, ctx):
        """Manage First Day channels and embed."""
        pass

    @aboutset_firstday.command(name="add")
    async def firstday_add(self, ctx, channel: discord.TextChannel):
        """Add a channel to the First Day list."""
        async with self.config.guild(ctx.guild).first_day_channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)
                await ctx.send(f"Added {channel.mention} to First Day channels.")
            else:
                await ctx.send("That channel is already in the list.")

    @aboutset_firstday.command(name="remove")
    async def firstday_remove(self, ctx, channel: discord.TextChannel):
        """Remove a channel from the First Day list."""
        async with self.config.guild(ctx.guild).first_day_channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)
                await ctx.send(f"Removed {channel.mention} from First Day channels.")
            else:
                await ctx.send("That channel is not in the list.")

    @aboutset_firstday.command(name="list")
    async def firstday_list(self, ctx):
        """List First Day channels."""
        channel_ids = await self.config.guild(ctx.guild).first_day_channels()
        if not channel_ids:
            return await ctx.send("No First Day channels configured.")
        
        lines = []
        for ch_id in channel_ids:
            channel = ctx.guild.get_channel(ch_id)
            if channel:
                lines.append(channel.mention)
            else:
                lines.append(f"Deleted-Channel-{ch_id}")
        
        embed = discord.Embed(
            title="First Day Channels",
            description="\n".join(lines),
            color=await ctx.embed_color()
        )
        await ctx.send(embed=embed)

    @aboutset_firstday.command(name="description")
    async def firstday_description(self, ctx, *, text: str):
        """Set the description for the First Day embed."""
        await self.config.guild(ctx.guild).first_day_description.set(text)
        await ctx.send("First Day embed description updated.")

    @aboutset_firstday.command(name="title")
    async def firstday_title(self, ctx, *, text: str):
        """Set the title for the First Day embed."""
        await self.config.guild(ctx.guild).first_day_title.set(text)
        await ctx.send("First Day embed title updated.")

    @aboutset_firstday.command(name="thumbnail")
    async def firstday_thumbnail(self, ctx, url: str):
        """Set the thumbnail URL for the First Day embed. Use 'none' or 'clear' to remove."""
        if url.lower() in ["none", "clear"]:
            url = ""
        await self.config.guild(ctx.guild).first_day_thumbnail.set(url)
        await ctx.send("First Day embed thumbnail updated.")

    @aboutset_firstday.command(name="image")
    async def firstday_image(self, ctx, url: str):
        """Set the image URL for the First Day embed. Use 'none' or 'clear' to remove."""
        if url.lower() in ["none", "clear"]:
            url = ""
        await self.config.guild(ctx.guild).first_day_image.set(url)
        await ctx.send("First Day embed image updated.")

    # --- Location Role Management ---
    @aboutset.group(name="locations")
    async def aboutset_locations(self, ctx):
        """Manage location roles."""
        pass

    @aboutset_locations.command(name="add")
    async def locations_add(self, ctx, role: discord.Role, emoji: str):
        """Add location role."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            locations[str(role.id)] = emoji
        await ctx.send(f"Added location role **{role.name}** with {emoji}")

    @aboutset_locations.command(name="remove")
    async def locations_remove(self, ctx, role: discord.Role):
        """Remove location role."""
        async with self.config.guild(ctx.guild).location_roles() as locations:
            if str(role.id) in locations:
                del locations[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from locations.")
            else:
                await ctx.send("Role not found in locations.")

    @aboutset_locations.command(name="list")
    async def locations_list(self, ctx):
        """List location roles."""
        locations = await self.config.guild(ctx.guild).location_roles()
        if not locations:
            return await ctx.send("No location roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in locations.items()]
        await ctx.send(embed=discord.Embed(title="Location Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- DM Status Management ---
    @aboutset.group(name="dmstatus")
    async def aboutset_dmstatus(self, ctx):
        """Manage DM Status roles."""
        pass

    @aboutset_dmstatus.command(name="add")
    async def dmstatus_add(self, ctx, role: discord.Role, emoji: str):
        """Add DM status role."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            statuses[str(role.id)] = emoji
        await ctx.send(f"Added DM status role **{role.name}** with {emoji}")

    @aboutset_dmstatus.command(name="remove")
    async def dmstatus_remove(self, ctx, role: discord.Role):
        """Remove DM status role."""
        async with self.config.guild(ctx.guild).dm_status_roles() as statuses:
            if str(role.id) in statuses:
                del statuses[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from DM statuses.")
            else:
                await ctx.send("Role not found in DM statuses.")

    @aboutset_dmstatus.command(name="list")
    async def dmstatus_list(self, ctx):
        """List DM status roles."""
        statuses = await self.config.guild(ctx.guild).dm_status_roles()
        if not statuses:
            return await ctx.send("No DM status roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in statuses.items()]
        await ctx.send(embed=discord.Embed(title="DM Status Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Award Role Management ---
    @aboutset.group(name="award")
    async def aboutset_award(self, ctx):
        """Manage Award roles."""
        pass

    @aboutset_award.command(name="add")
    async def award_add(self, ctx, role: discord.Role):
        """Add award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id not in awards:
                awards.append(role.id)
                await ctx.send(f"Added **{role.name}** to awards.")
            else:
                await ctx.send("Role already in awards.")

    @aboutset_award.command(name="remove")
    async def award_remove(self, ctx, role: discord.Role):
        """Remove award role."""
        async with self.config.guild(ctx.guild).award_roles() as awards:
            if role.id in awards:
                awards.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from awards.")
            else:
                await ctx.send("Role not found in awards.")

    @aboutset_award.command(name="list")
    async def award_list(self, ctx):
        """List award roles."""
        awards = await self.config.guild(ctx.guild).award_roles()
        if not awards:
            return await ctx.send("No award roles configured.")
        lines = [ctx.guild.get_role(rid).name if ctx.guild.get_role(rid) else 'Deleted' for rid in awards]
        await ctx.send(embed=discord.Embed(title="Award Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Helper Role Management ---
    @aboutset.group(name="helper")
    async def aboutset_helper(self, ctx):
        """Manage Helper roles."""
        pass

    @aboutset_helper.command(name="add")
    async def helper_add(self, ctx, role: discord.Role):
        """Add helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id not in helpers:
                helpers.append(role.id)
                await ctx.send(f"Added **{role.name}** to helpers.")
            else:
                await ctx.send("Role already in helpers.")

    @aboutset_helper.command(name="remove")
    async def helper_remove(self, ctx, role: discord.Role):
        """Remove helper role."""
        async with self.config.guild(ctx.guild).helper_roles() as helpers:
            if role.id in helpers:
                helpers.remove(role.id)
                await ctx.send(f"Removed **{role.name}** from helpers.")
            else:
                await ctx.send("Role not found in helpers.")

    @aboutset_helper.command(name="list")
    async def helper_list(self, ctx):
        """List helper roles."""
        helpers = await self.config.guild(ctx.guild).helper_roles()
        if not helpers:
            return await ctx.send("No helper roles configured.")
        lines = [ctx.guild.get_role(rid).name if ctx.guild.get_role(rid) else 'Deleted' for rid in helpers]
        await ctx.send(embed=discord.Embed(title="Helper Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- House Role Management ---
    @aboutset.group(name="houseroles")
    async def aboutset_houseroles(self, ctx):
        """Manage House roles."""
        pass

    @aboutset_houseroles.command(name="add")
    async def houseroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add House role."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            house_roles[str(role.id)] = emoji
        await ctx.send(f"Added House role **{role.name}** with {emoji}")

    @aboutset_houseroles.command(name="remove")
    async def houseroles_remove(self, ctx, role: discord.Role):
        """Remove House role."""
        async with self.config.guild(ctx.guild).house_roles() as house_roles:
            if str(role.id) in house_roles:
                del house_roles[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from House roles.")
            else:
                await ctx.send("Role not found in House roles.")

    @aboutset_houseroles.command(name="list")
    async def houseroles_list(self, ctx):
        """List House roles."""
        house_roles = await self.config.guild(ctx.guild).house_roles()
        if not house_roles:
            return await ctx.send("No House roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in house_roles.items()]
        await ctx.send(embed=discord.Embed(title="House Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Egg Status Role Management ---
    @aboutset.group(name="eggroles")
    async def aboutset_eggroles(self, ctx):
        """Manage Egg Status roles."""
        pass

    @aboutset_eggroles.command(name="add")
    async def eggroles_add(self, ctx, role: discord.Role, emoji: str):
        """Add Egg Status role."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            egg_roles[str(role.id)] = emoji
        await ctx.send(f"Added Egg Status role **{role.name}** with {emoji}")

    @aboutset_eggroles.command(name="remove")
    async def eggroles_remove(self, ctx, role: discord.Role):
        """Remove Egg Status role."""
        async with self.config.guild(ctx.guild).egg_status_roles() as egg_roles:
            if str(role.id) in egg_roles:
                del egg_roles[str(role.id)]
                await ctx.send(f"Removed **{role.name}** from Egg Status roles.")
            else:
                await ctx.send("Role not found in Egg Status roles.")

    @aboutset_eggroles.command(name="list")
    async def eggroles_list(self, ctx):
        """List Egg Status roles."""
        egg_roles = await self.config.guild(ctx.guild).egg_status_roles()
        if not egg_roles:
            return await ctx.send("No Egg Status roles configured.")
        lines = [f"{emoji} {ctx.guild.get_role(int(rid)).name if ctx.guild.get_role(int(rid)) else 'Deleted'}" for rid, emoji in egg_roles.items()]
        await ctx.send(embed=discord.Embed(title="Egg Status Roles", description="\n".join(lines), color=await ctx.embed_color()))

    # --- Opt-in Role Management ---
    @aboutset.command(name="optin")
    async def aboutset_optin(self, ctx, base_role: discord.Role, target_role: discord.Role, days: int, level: int):
        """Set up an opt-in role path."""
        if days < 0 or level < 0:
             return await ctx.send("Days and Level must be non-negative.")

        async with self.config.guild(ctx.guild).optin_roles() as optins:
            optins[str(base_role.id)] = {
                "target_id": str(target_role.id),
                "days": days,
                "level": level
            }
        
        await ctx.send(
            f"Configured Opt-in Path:\n"
            f"User has **{base_role.name}** -> Waits **{days}** days & Reaches Level **{level}** -> Gets **{target_role.name}**"
        )

    @aboutset.command(name="optin_remove")
    async def aboutset_optin_remove(self, ctx, base_role: discord.Role):
        """Remove an opt-in role configuration."""
        async with self.config.guild(ctx.guild).optin_roles() as optins:
            if str(base_role.id) in optins:
                del optins[str(base_role.id)]
                await ctx.send(f"Removed opt-in configuration for **{base_role.name}**.")
            else:
                await ctx.send("That base role is not configured.")

    @aboutset.command(name="optin_list")
    async def aboutset_optin_list(self, ctx):
        """List configured opt-in role paths."""
        optins = await self.config.guild(ctx.guild).optin_roles()
        if not optins:
            return await ctx.send("No opt-in roles configured.")
        
        lines = []
        for base_id, data in optins.items():
            base_role = ctx.guild.get_role(int(base_id))
            base_name = base_role.mention if base_role else f"Deleted-Role-{base_id}"
            
            target_id = int(data.get("target_id", 0))
            target_role = ctx.guild.get_role(target_id)
            target_name = target_role.mention if target_role else f"Deleted-Role-{target_id}"
            
            days = data.get("days", 0)
            level = data.get("level", 0)
            
            lines.append(f"{base_name} -> {target_name} (Days: {days}, Level: {level})")
            
        await ctx.send(embed=discord.Embed(title="Opt-in Role Configurations", description="\n".join(lines), color=await ctx.embed_color()))

    # --- NEW: Reward Role Management ---
    @aboutset.command(name="reward")
    async def aboutset_reward(self, ctx, reward_role: discord.Role, days: int, level: int):
        """
        Set up a reward role (automatically checked for everyone).
        
        Arguments:
        - reward_role: The role given when requirements are met.
        - days: Days in server required.
        - level: Level required.
        """
        if days < 0 or level < 0:
             return await ctx.send("Days and Level must be non-negative.")

        async with self.config.guild(ctx.guild).reward_roles() as rewards:
            rewards[str(reward_role.id)] = {
                "days": days,
                "level": level
            }
        
        await ctx.send(
            f"Configured Reward Role:\n"
            f"User waits **{days}** days & Reaches Level **{level}** -> Gets **{reward_role.name}**"
        )

    @aboutset.command(name="reward_remove")
    async def aboutset_reward_remove(self, ctx, reward_role: discord.Role):
        """Remove a reward role configuration."""
        async with self.config.guild(ctx.guild).reward_roles() as rewards:
            if str(reward_role.id) in rewards:
                del rewards[str(reward_role.id)]
                await ctx.send(f"Removed reward configuration for **{reward_role.name}**.")
            else:
                await ctx.send("That reward role is not configured.")

    @aboutset.command(name="reward_list")
    async def aboutset_reward_list(self, ctx):
        """List configured reward roles."""
        rewards = await self.config.guild(ctx.guild).reward_roles()
        if not rewards:
            return await ctx.send("No reward roles configured.")
        
        lines = []
        for role_id, data in rewards.items():
            r_role = ctx.guild.get_role(int(role_id))
            r_name = r_role.mention if r_role else f"Deleted-Role-{role_id}"
            
            days = data.get("days", 0)
            level = data.get("level", 0)
            
            lines.append(f"{r_name} (Days: {days}, Level: {level})")
            
        await ctx.send(embed=discord.Embed(title="Reward Role Configurations", description="\n".join(lines), color=await ctx.embed_color()))