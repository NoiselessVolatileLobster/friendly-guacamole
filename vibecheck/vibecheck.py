"""Module for the VibeCheck cog."""
import asyncio
import logging
import time
from collections import namedtuple
from typing import Tuple, Optional, Union
from datetime import datetime, timedelta, timezone

import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.vibecheck")

__all__ = ["UNIQUE_ID", "VibeCheck"]

UNIQUE_ID = 0x9C02DCC7
MemberInfo = namedtuple("MemberInfo", "id name vibes")
MemberRatioInfo = namedtuple("MemberRatioInfo", "id name ratio")


class VibeCheck(getattr(commands, "Cog", object)):
    """
    Allows you to get a vibe check on users. Members can give goodvibes and badvibes to others.
    Goodvibes add 1 vibe. Badvibes subtract 1 vibe.
    """

    def __init__(self, bot):
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)
        
        # Global vibes score & history
        self.conf.register_user(
            vibes=0,
            good_vibes_sent=0,
            bad_vibes_sent=0,
            interactions={},
            last_good_vibe=0,  # Timestamp of last usage
            last_bad_vibe=0,   # Timestamp of last usage
            new_member_xp_awarded=False  # Tracks if they already got their newbie bonus
        )
        
        # Guild settings
        self.conf.register_guild(
            log_channel_id=None,
            good_vibes_cooldown=3600,  # Default 60 minutes (in seconds)
            bad_vibes_cooldown=3600,   # Default 60 minutes (in seconds)
            
            # LevelUp Requirements
            req_level_good=0, # Level required to send good vibes
            req_level_bad=0,  # Level required to send bad vibes
            
            # Ratio Thresholds
            ratio_soft_threshold=None, # Threshold 1 (Reminder)
            ratio_hard_threshold=None, # Threshold 2 (Block + Warn)
            
            # New Member Kick Thresholds (Kick/Level 3)
            new_member_age_seconds=None, # How long (seconds) user is considered "new"
            new_member_score_threshold=None, # Score that triggers kick for new members
            new_member_kick_reason="VibeCheck: New member vibe score too low", # Reason for new member kick
            
            # New Member XP Reward Settings
            new_member_xp_minutes=0,       # 0 = Disabled
            new_member_xp_threshold=10,    # Score needed
            new_member_xp_amount=0,        # XP to give
            
            # WarnSystem Config
            warn_threshold=-100,       # Level 1
            warn_reason="VibeCheck: Low vibe score",
            
            kick_threshold=-100,       # Level 3
            kick_reason="VibeCheck: Very low vibe score",
            
            ban_threshold=-100,        # Level 5
            ban_reason="VibeCheck: Critically low vibe score"
        )

    # --- PUBLIC API ---

    async def get_vibe_score(self, user_id: int) -> int:
        """
        Public API method for other cogs to retrieve a user's vibe score.

        Args:
            user_id (int): The Discord ID of the user.

        Returns:
            int: The global vibe score of the user. Returns 0 if no data exists.
        """
        return await self.conf.user_from_id(user_id).vibes()

    async def get_vibe_ratio(self, user_id: int) -> int:
        """
        Public API method to retrieve a user's vibe ratio.
        Ratio = Good Vibes Sent - Bad Vibes Sent.

        Args:
            user_id (int): The Discord ID of the user.

        Returns:
            int: The vibe ratio.
        """
        user_data = await self.conf.user_from_id(user_id).all()
        return user_data.get("good_vibes_sent", 0) - user_data.get("bad_vibes_sent", 0)

    # --- COMMANDS: VIBES ACTIONS & INFO ---

    @commands.command(name="goodvibes")
    async def good_vibes(self, ctx: commands.Context, user: discord.User, amount: int):
        """Give someone good vibes"""
        
        # 1. Level Requirement Check
        allowed, req_level = await self._check_level_requirement(ctx, "good")
        if not allowed:
            return await ctx.send(
                f"You must be at least **Level {req_level}** to send good vibes.", 
                ephemeral=True
            )

        # 2. Check cooldown manually
        await self._check_cooldown(ctx, "good")
        
        if user and user.id == ctx.author.id:
            return await ctx.send(("You can't give good vibes to yourself!"), ephemeral=True)
        if user and user.bot:
            return await ctx.send(("Awe, I appreciate it, but you can't give ME good vibes!"), ephemeral=True)
        
        # Pass True for is_good because this is goodvibes
        await self._add_vibes(ctx.author, user, amount, is_good=True)
        
        # Update cooldown timestamp
        await self.conf.user(ctx.author).last_good_vibe.set(int(time.time()))
        
        await ctx.send("You sent good vibes to {}!".format(user.name))

    @commands.command(name="badvibes")
    async def bad_vibes(self, ctx: commands.Context, user: discord.Member, amount: int):
        """Give someone bad vibes"""
        
        # 1. Level Requirement Check
        allowed, req_level = await self._check_level_requirement(ctx, "bad")
        if not allowed:
            return await ctx.send(
                f"You must be at least **Level {req_level}** to send bad vibes.", 
                ephemeral=True
            )

        # --- RATIO THRESHOLD CHECK ---
        # We perform this check BEFORE executing the command logic
        user_data = await self.conf.user(ctx.author).all()
        current_ratio = user_data.get("good_vibes_sent", 0) - user_data.get("bad_vibes_sent", 0)
        
        guild_settings = await self.conf.guild(ctx.guild).all()
        soft_threshold = guild_settings.get("ratio_soft_threshold")
        hard_threshold = guild_settings.get("ratio_hard_threshold")

        # 2. Hard Threshold (Block & Warn)
        if hard_threshold is not None and current_ratio <= hard_threshold:
            # Trigger WarnSystem Level 1
            await self._trigger_warnsystem(
                ctx.guild, ctx.author, ctx.guild.me, 1, 
                f"Your viberatio is {current_ratio} and you're unable to send bad vibes"
            )
            # Block Command
            return await ctx.send(
                "You are not allowed to send bad vibes at this time", 
                ephemeral=True
            )

        # 3. Soft Threshold (Reminder)
        if soft_threshold is not None and current_ratio <= soft_threshold:
            await ctx.send(
                f"Your vibe ratio is {current_ratio}. Please remember to use [p]goodvibes too!", 
                ephemeral=True
            )
        # -----------------------------
        
        # Check cooldown manually
        await self._check_cooldown(ctx, "bad")
        
        if user and user.id == ctx.author.id:
            return await ctx.send(("You can't give bad vibes to yourself!"), ephemeral=True)
        if user and user.bot:
            return await ctx.send(("Now listen here, you little shit. You can't give ME bad vibes"), ephemeral=True)

        # Pass False for is_good because this is badvibes
        await self._add_vibes(ctx.author, user, -amount, is_good=False)
        
        # Update cooldown timestamp
        await self.conf.user(ctx.author).last_bad_vibe.set(int(time.time()))
        
        await ctx.send("You sent bad vibes to {}!".format(user.name))

    @commands.command(name="vibes")
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def get_vibes(self, ctx: commands.Context, user: discord.Member = None):
        """Check a user's vibes (Mods/Admins only)."""
        if user is None:
            user = ctx.author

        data = await self.conf.user(user).all()
        vibes = data.get("vibes", 0)
        good_sent = data.get("good_vibes_sent", 0)
        bad_sent = data.get("bad_vibes_sent", 0)
        ratio = good_sent - bad_sent

        embed = discord.Embed(
            title="VibeCheck",
            color=await ctx.embed_color(),
            description=f"**User ID:** `{user.id}`\n"
                        f"**Vibe Score:** {vibes}\n"
                        f"**Vibe Ratio:** {ratio}"
        )
        
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
            
        embed.set_footer(text=ctx.guild.name)
        
        await ctx.send(embed=embed)

    @commands.command(name="myvibes")
    @commands.guild_only()
    async def my_vibes(self, ctx: commands.Context):
        """Check your own vibe statistics."""
        user = ctx.author
        data = await self.conf.user(user).all()
        
        vibes = data.get("vibes", 0)
        good_sent = data.get("good_vibes_sent", 0)
        bad_sent = data.get("bad_vibes_sent", 0)
        ratio = good_sent - bad_sent
        
        embed = discord.Embed(
            title="My Vibes",
            color=await ctx.embed_color()
        )
        
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
            
        embed.add_field(name="**My Vibe Score:**", value=str(vibes), inline=False)
        embed.add_field(name="**My Vibe Ratio:**", value=str(ratio), inline=False)
        
        embed.set_footer(text=ctx.guild.name)
        
        await ctx.send(embed=embed, ephemeral=True)

    # --- COMMAND GROUP: VIBECHECKSET ---

    @commands.group(name="vibecheckset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def vibecheckset(self, ctx: commands.Context):
        """Configuration settings for VibeCheck."""
        pass

    @vibecheckset.command(name="board")
    async def vibe_board(self, ctx: commands.Context, top: int = 10):
        """
        Prints out the Vibes leaderboard.
        
        This displays the global vibe scores of users.
        """
        reverse = True
        if top == 0:
            top = 10
        elif top < 0:
            reverse = False
            top = -top
        
        members_sorted = sorted(
            await self._get_all_members(ctx.bot), key=lambda x: x.vibes, reverse=reverse
        )
        if len(members_sorted) < top:
            top = len(members_sorted)
        topten = members_sorted[:top]
        highscore = ""
        place = 1
        for member in topten:
            highscore += str(place).ljust(len(str(top)) + 1)
            highscore += "{} | ".format(member.name).ljust(18 - len(str(member.vibes)))
            highscore += str(member.vibes) + "\n"
            place += 1
        if highscore != "":
            for page in pagify(highscore, shorten_by=12):
                await ctx.send(box(page, lang="py"))
        else:
            await ctx.send("No one has any vibes 🙁")

    @vibecheckset.command(name="ratioboard")
    async def ratio_board(self, ctx: commands.Context):
        """
        Displays a table of user vibe ratios (Good Sent - Bad Sent), sorted from highest to lowest.
        """
        data = await self._get_all_members_ratios(ctx.bot)
        
        # Sort by ratio descending
        data_sorted = sorted(data, key=lambda x: x.ratio, reverse=True)
        
        if not data_sorted:
            return await ctx.send("No vibe activity recorded yet.")
        
        # Table Formatting
        id_width = 20 
        name_width = 25
        
        # Create Header
        header = f"{'User ID'.ljust(id_width)} | {'User Name'.ljust(name_width)} | Current Vibe Ratio\n"
        header += "-" * (id_width + name_width + 20) + "\n"
        
        msg = header
        
        for m in data_sorted:
            # Truncate name if too long to keep table structure
            name_display = (m.name[:name_width-3] + '...') if len(m.name) > name_width else m.name
            
            row = f"{str(m.id).ljust(id_width)} | {name_display.ljust(name_width)} | {m.ratio}\n"
            msg += row
            
        for page in pagify(msg, shorten_by=12):
            await ctx.send(box(page, lang="prolog"))

    @vibecheckset.command(name="ratio")
    async def vibe_ratio(self, ctx: commands.Context, user: discord.User):
        """
        Check a user's vibe ratio statistics.
        
        Shows their ratio (Good Sent - Bad Sent) and who they target the most.
        """
        data = await self.conf.user(user).all()
        
        good_sent = data.get("good_vibes_sent", 0)
        bad_sent = data.get("bad_vibes_sent", 0)
        ratio = good_sent - bad_sent
        interactions = data.get("interactions", {})

        # Calculate top receivers
        most_good_user = "None"
        most_good_count = 0
        most_bad_user = "None"
        most_bad_count = 0

        for target_id_str, stats in interactions.items():
            # Check for most good vibes sent
            if stats.get("good", 0) > most_good_count:
                most_good_count = stats.get("good", 0)
                target_user = self.bot.get_user(int(target_id_str))
                most_good_user = target_user.name if target_user else f"Unknown User ({target_id_str})"

            # Check for most bad vibes sent
            if stats.get("bad", 0) > most_bad_count:
                most_bad_count = stats.get("bad", 0)
                target_user = self.bot.get_user(int(target_id_str))
                most_bad_user = target_user.name if target_user else f"Unknown User ({target_id_str})"

        embed = discord.Embed(
            title=f"Vibe Ratio: {user.display_name}",
            color=discord.Color.blue()
        )
        
        # Add clickable username link if possible, otherwise just name
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        
        embed.add_field(name="Vibe Ratio", value=str(ratio), inline=False)
        embed.add_field(name="Good Vibes Sent", value=str(good_sent), inline=True)
        embed.add_field(name="Bad Vibes Sent", value=str(bad_sent), inline=True)
        
        embed.add_field(name="Most Good Vibes To", value=f"{most_good_user} ({most_good_count})", inline=True)
        embed.add_field(name="Most Bad Vibes To", value=f"{most_bad_user} ({most_bad_count})", inline=True)

        await ctx.send(embed=embed)

    @vibecheckset.command(name="reqlevel")
    async def set_req_level(self, ctx: commands.Context, option: str, level: int):
        """
        Set the minimum LevelUp level required to send vibes.
        
        Args:
            option: Either 'good' or 'bad'.
            level: The level required (0 to disable).
            
        Example: 
            [p]vibecheckset reqlevel bad 5 (Requires level 5 to send bad vibes)
        """
        if option.lower() not in ["good", "bad"]:
            return await ctx.send("Option must be either `good` or `bad`.")
        
        if level < 0:
            return await ctx.send("Level must be a positive integer.")
        
        if option.lower() == "good":
            await self.conf.guild(ctx.guild).req_level_good.set(level)
            await ctx.send(f"✅ Users now need to be **Level {level}** to send **Good Vibes**.")
        else:
            await self.conf.guild(ctx.guild).req_level_bad.set(level)
            await ctx.send(f"✅ Users now need to be **Level {level}** to send **Bad Vibes**.")

    @vibecheckset.command(name="ratiothreshold")
    async def set_ratio_thresholds(self, ctx: commands.Context, soft: int, hard: int):
        """
        Sets the soft and hard thresholds for vibe ratios (Good Sent - Bad Sent).
        
        Soft Threshold (Arg 1): If ratio drops below this, user gets an ephemeral reminder when sending bad vibes.
        Hard Threshold (Arg 2): If ratio drops below this, user is BLOCKED from sending bad vibes and warned (Level 1).
        """
        await self.conf.guild(ctx.guild).ratio_soft_threshold.set(soft)
        await self.conf.guild(ctx.guild).ratio_hard_threshold.set(hard)
        
        await ctx.send(
            f"✅ **Ratio Thresholds Updated**\n"
            f"**Soft Threshold:** {soft} (Reminds user)\n"
            f"**Hard Threshold:** {hard} (Blocks user + WarnSystem Lvl 1)"
        )

    @vibecheckset.command(name="newmember")
    async def set_new_member_threshold(self, ctx: commands.Context, minutes: int, threshold: int, *, reason: str = "VibeCheck: New member vibe score too low"):
        """
        Sets strict thresholds for new members (triggers WarnSystem Level 3 / Kick).
        """
        if minutes <= 0:
            await self.conf.guild(ctx.guild).new_member_age_seconds.set(None)
            await self.conf.guild(ctx.guild).new_member_score_threshold.set(None)
            return await ctx.send("New Member strict threshold has been **disabled**.")
            
        if threshold >= 0:
            return await ctx.send("The threshold must be a negative integer (e.g., `-10`).")
            
        await self.conf.guild(ctx.guild).new_member_age_seconds.set(minutes * 60)
        await self.conf.guild(ctx.guild).new_member_score_threshold.set(threshold)
        await self.conf.guild(ctx.guild).new_member_kick_reason.set(reason)
        
        await ctx.send(
            f"✅ **New Member Threshold Set**\n"
            f"Users who joined less than **{minutes} minutes** ago will trigger a **Level 3 Warning (Kick)** "
            f"if their vibe score drops to **{threshold}**.\n"
            f"Reason: *{reason}*"
        )

    @vibecheckset.command(name="newmemberxp")
    async def set_new_member_xp(self, ctx: commands.Context, minutes: int, threshold: int, xp: int):
        """
        Configure XP rewards for new members who reach a specific vibe score.
        """
        if minutes < 0 or xp < 0:
            return await ctx.send("Minutes and XP must be positive numbers.")

        if minutes == 0:
            await self.conf.guild(ctx.guild).new_member_xp_minutes.set(0)
            await ctx.send("New Member XP rewards have been **disabled**.")
            return

        await self.conf.guild(ctx.guild).new_member_xp_minutes.set(minutes)
        await self.conf.guild(ctx.guild).new_member_xp_threshold.set(threshold)
        await self.conf.guild(ctx.guild).new_member_xp_amount.set(xp)
        
        await ctx.send(
            f"✅ **New Member XP Configured**\n"
            f"Users who join and reach a vibe score of **{threshold}** within **{minutes} minutes** "
            f"will be granted **{xp} XP**."
        )

    @vibecheckset.command(name="logchannel")
    async def set_vibe_log_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Sets the channel where vibe change logs will be posted.
        Omit the channel to disable logging.
        """
        if channel is None:
            await self.conf.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("Vibe activity logging has been **disabled**.")
            return

        await self.conf.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Vibe activity will now be logged in {channel.mention}.")

    # --- COOLDOWN COMMANDS ---

    @vibecheckset.group(name="cooldown")
    async def vibe_cooldown(self, ctx: commands.Context):
        """Configure cooldowns for sending vibes."""
        pass

    @vibe_cooldown.command(name="goodvibes")
    async def set_good_vibes_cooldown(self, ctx: commands.Context, minutes: int):
        """Sets the cooldown for [p]goodvibes in minutes."""
        if minutes < 0:
            return await ctx.send("Cooldown cannot be negative.")
        
        seconds = minutes * 60
        await self.conf.guild(ctx.guild).good_vibes_cooldown.set(seconds)
        
        if minutes == 0:
            await ctx.send("Cooldown for **Good Vibes** has been disabled.")
        else:
            await ctx.send(f"Cooldown for **Good Vibes** set to **{minutes} minutes**.")

    @vibe_cooldown.command(name="badvibes")
    async def set_bad_vibes_cooldown(self, ctx: commands.Context, minutes: int):
        """Sets the cooldown for [p]badvibes in minutes."""
        if minutes < 0:
            return await ctx.send("Cooldown cannot be negative.")
        
        seconds = minutes * 60
        await self.conf.guild(ctx.guild).bad_vibes_cooldown.set(seconds)

        if minutes == 0:
            await ctx.send("Cooldown for **Bad Vibes** has been disabled.")
        else:
            await ctx.send(f"Cooldown for **Bad Vibes** set to **{minutes} minutes**.")

    # --- WARNSYSTEM COMMANDS ---

    @vibecheckset.command(name="warning")
    async def set_warning(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Low vibe score"):
        """Configure WarnSystem Level 1 (Warning)."""
        if threshold > 0:
            return await ctx.send("The threshold must be a negative integer (e.g., `-10`). Set to 0 to disable.")

        if threshold == 0:
            await self.conf.guild(ctx.guild).warn_threshold.set(None)
            await ctx.send("WarnSystem Level 1 triggers have been **disabled**.")
            return

        await self.conf.guild(ctx.guild).warn_threshold.set(threshold)
        await self.conf.guild(ctx.guild).warn_reason.set(reason)
        
        await ctx.send(
            f"WarnSystem Level 1 will now trigger at score **{threshold}**.\n"
            f"Reason: *{reason}*"
        )

    @vibecheckset.command(name="kick")
    async def set_kick(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Very low vibe score"):
        """Configure WarnSystem Level 3 (Kick)."""
        if threshold > 0:
            return await ctx.send("The threshold must be a negative integer (e.g., `-50`). Set to 0 to disable.")

        if threshold == 0:
            await self.conf.guild(ctx.guild).kick_threshold.set(None)
            await ctx.send("WarnSystem Level 3 triggers have been **disabled**.")
            return

        await self.conf.guild(ctx.guild).kick_threshold.set(threshold)
        await self.conf.guild(ctx.guild).kick_reason.set(reason)
        
        await ctx.send(
            f"WarnSystem Level 3 will now trigger at score **{threshold}**.\n"
            f"Reason: *{reason}*"
        )

    @vibecheckset.command(name="ban")
    async def set_ban(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Critically low vibe score"):
        """Configure WarnSystem Level 5 (Ban)."""
        if threshold > 0:
            return await ctx.send("The threshold must be a negative integer (e.g., `-100`). Set to 0 to disable.")

        if threshold == 0:
            await self.conf.guild(ctx.guild).ban_threshold.set(None)
            await ctx.send("WarnSystem Level 5 triggers have been **disabled**.")
            return

        await self.conf.guild(ctx.guild).ban_threshold.set(threshold)
        await self.conf.guild(ctx.guild).ban_reason.set(reason)
        
        await ctx.send(
            f"WarnSystem Level 5 will now trigger at score **{threshold}**.\n"
            f"Reason: *{reason}*"
        )

    @vibecheckset.command(name="view")
    async def view_settings(self, ctx: commands.Context):
        """Shows the current VibeCheck configuration for this server."""
        settings = await self.conf.guild(ctx.guild).all()
        
        warn_thresh = settings.get('warn_threshold')
        warn_thresh_str = f"{warn_thresh} ({settings.get('warn_reason')})" if warn_thresh is not None else "Disabled"
        
        kick_thresh = settings.get('kick_threshold')
        kick_thresh_str = f"{kick_thresh} ({settings.get('kick_reason')})" if kick_thresh is not None else "Disabled"
        
        ban_thresh = settings.get('ban_threshold')
        ban_thresh_str = f"{ban_thresh} ({settings.get('ban_reason')})" if ban_thresh is not None else "Disabled"
        
        # New Member
        new_mem_sec = settings.get('new_member_age_seconds')
        new_mem_score = settings.get('new_member_score_threshold')
        new_mem_reason = settings.get('new_member_kick_reason', "Default Reason")
        if new_mem_sec and new_mem_score:
            new_mem_str = f"Age < {new_mem_sec // 60}m & Score <= {new_mem_score} -> Kick\nReason: {new_mem_reason}"
        else:
            new_mem_str = "Disabled"
            
        # New Member XP
        new_mem_xp_min = settings.get('new_member_xp_minutes', 0)
        new_mem_xp_thr = settings.get('new_member_xp_threshold', 10)
        new_mem_xp_amt = settings.get('new_member_xp_amount', 0)
        if new_mem_xp_min > 0 and new_mem_xp_amt > 0:
            new_mem_xp_str = f"Reach {new_mem_xp_thr} vibes in {new_mem_xp_min}m -> +{new_mem_xp_amt} XP"
        else:
            new_mem_xp_str = "Disabled"

        # Log Channel
        log_id = settings.get('log_channel_id')
        if log_id is None:
            log_text = "Not Set (Disabled)"
        else:
            chan = ctx.guild.get_channel(log_id)
            log_text = chan.mention if chan else f"Deleted Channel ({log_id})"
            
        # Cooldowns
        good_cd = settings.get('good_vibes_cooldown', 3600)
        bad_cd = settings.get('bad_vibes_cooldown', 3600)
        
        good_cd_str = f"{good_cd // 60} mins" if good_cd > 0 else "None"
        bad_cd_str = f"{bad_cd // 60} mins" if bad_cd > 0 else "None"
        
        # Ratio Thresholds
        soft_rt = settings.get('ratio_soft_threshold')
        hard_rt = settings.get('ratio_hard_threshold')
        soft_rt_str = str(soft_rt) if soft_rt is not None else "Disabled"
        hard_rt_str = str(hard_rt) if hard_rt is not None else "Disabled"
        
        # Level Req
        req_good = settings.get('req_level_good', 0)
        req_bad = settings.get('req_level_bad', 0)
        req_good_str = f"Level {req_good}" if req_good > 0 else "None"
        req_bad_str = f"Level {req_bad}" if req_bad > 0 else "None"

        embed = discord.Embed(title=f"VibeCheck Settings for {ctx.guild.name}", color=discord.Color.blue())
        embed.add_field(name="Log Channel", value=log_text, inline=False)
        
        embed.add_field(name="Good Vibes Cooldown", value=good_cd_str, inline=True)
        embed.add_field(name="Bad Vibes Cooldown", value=bad_cd_str, inline=True)
        
        embed.add_field(name="Req Level (Good)", value=req_good_str, inline=True)
        embed.add_field(name="Req Level (Bad)", value=req_bad_str, inline=True)
        
        embed.add_field(name="Ratio Soft Threshold", value=soft_rt_str, inline=True)
        embed.add_field(name="Ratio Hard Threshold", value=hard_rt_str, inline=True)
        
        embed.add_field(name="New Member Threshold", value=new_mem_str, inline=False)
        embed.add_field(name="New Member XP Reward", value=new_mem_xp_str, inline=False)
        
        embed.add_field(name="WarnSystem Lvl 1", value=warn_thresh_str, inline=False)
        embed.add_field(name="WarnSystem Lvl 3", value=kick_thresh_str, inline=False)
        embed.add_field(name="WarnSystem Lvl 5", value=ban_thresh_str, inline=False)
        
        await ctx.send(embed=embed)

    @vibecheckset.command(name="resetuser")
    @checks.is_owner()
    async def reset_user(self, ctx: commands.Context, user: discord.User):
        """Resets a user's global vibes."""
        log.debug("Resetting %s's vibes", str(user))
        await self.conf.user(user).vibes.set(0)
        await ctx.send("{}'s vibes has been reset to 0.".format(user.name))
        
    @vibecheckset.command(name="resetratio")
    @checks.is_owner()
    async def reset_ratio(self, ctx: commands.Context, user: discord.User):
        """Resets a user's vibe ratio stats (good/bad sent & history)."""
        log.debug("Resetting %s's vibe ratio stats", str(user))
        
        # Reset specific fields, keep main score and cooldown timestamps
        await self.conf.user(user).good_vibes_sent.set(0)
        await self.conf.user(user).bad_vibes_sent.set(0)
        await self.conf.user(user).interactions.set({})
        
        await ctx.send("{}'s vibe ratio statistics have been reset.".format(user.name))

    @vibecheckset.command(name="resetall")
    @checks.is_owner()
    async def reset_all(self, ctx: commands.Context):
        """Resets vibes score AND ratio stats for ALL users."""
        
        confirmation_msg = await ctx.send(
            "⚠️ **WARNING:** This will reset vibes scores AND ratio stats for **EVERY USER** globally. "
            "React with a checkmark (✅) within 15 seconds to confirm."
        )
        
        try:
            await self.bot.wait_for(
                "reaction_add",
                check=lambda r, u: u == ctx.author and str(r.emoji) == "✅" and r.message.id == confirmation_msg.id,
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            await confirmation_msg.edit(content="Reset All Vibes command timed out. No scores were changed.")
            return
        
        await confirmation_msg.edit(content="Resetting all user scores and ratios... this may take a moment.")

        all_user_data = await self.conf.all_users()
        reset_count = 0
        
        # We iterate over everyone to ensure full cleanup
        for user_id, user_conf in all_user_data.items():
            user_obj = self.bot.get_user(user_id) 
            if user_obj:
                # Set specific fields to default
                await self.conf.user(user_obj).vibes.set(0)
                await self.conf.user(user_obj).good_vibes_sent.set(0)
                await self.conf.user(user_obj).bad_vibes_sent.set(0)
                await self.conf.user(user_obj).interactions.set({})
                await self.conf.user(user_obj).new_member_xp_awarded.set(False)
                reset_count += 1
                
        await ctx.send(f"✅ **Success!** Reset data for **{reset_count}** users globally.")
        
    @vibecheckset.command(name="prune")
    @checks.is_owner()
    async def prune(self, ctx: commands.Context):
        """Removes global vibe scores for users who are no longer in any of the bot's guilds."""
        
        confirmation_msg = await ctx.send(
            "⚠️ **WARNING:** This command will **permanently delete** the global vibe scores "
            "for any user who is no longer a member of *any* guild this bot shares. "
            "React with a checkmark (✅) within 15 seconds to confirm."
        )
        
        try:
            await self.bot.wait_for(
                "reaction_add",
                check=lambda r, u: u == ctx.author and str(r.emoji) == "✅" and r.message.id == confirmation_msg.id,
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            await confirmation_msg.edit(content="Prune Vibes command timed out. No scores were deleted.")
            return

        await confirmation_msg.edit(content="Scanning user data and pruning departed members... This may take a moment.")

        all_user_ids = (await self.conf.all_users()).keys()
        
        current_member_ids = set()
        for guild in self.bot.guilds:
            current_member_ids.update(member.id for member in guild.members)
            
        pruned_count = 0
        
        for user_id in all_user_ids:
            if user_id not in current_member_ids:
                await self.conf.user_from_id(user_id).clear()
                pruned_count += 1
                
        await ctx.send(f"✅ **Cleanup complete!** Successfully pruned vibe scores for **{pruned_count}** departed users.")

    # --- CORE LOGIC AND LISTENERS ---

    async def _check_cooldown(self, ctx: commands.Context, vibe_type: str):
        """
        Checks if a user is on cooldown for a specific vibe type.
        Raises CommandOnCooldown if they are.
        """
        if vibe_type == "good":
            cooldown_seconds = await self.conf.guild(ctx.guild).good_vibes_cooldown()
            last_used = await self.conf.user(ctx.author).last_good_vibe()
        else:
            cooldown_seconds = await self.conf.guild(ctx.guild).bad_vibes_cooldown()
            last_used = await self.conf.user(ctx.author).last_bad_vibe()

        if cooldown_seconds <= 0:
            return

        current_time = int(time.time())
        time_passed = current_time - last_used
        retry_after = cooldown_seconds - time_passed

        if retry_after > 0:
            raise commands.CommandOnCooldown(
                commands.Cooldown(1, cooldown_seconds), 
                retry_after, 
                commands.BucketType.user
            )

    async def _check_level_requirement(self, ctx: commands.Context, vibe_type: str) -> Tuple[bool, int]:
        """
        Checks if the author meets the LevelUp level requirement.
        
        Returns:
            Tuple[bool, int]: (Allowed, RequiredLevel)
        """
        if vibe_type == "good":
            req_level = await self.conf.guild(ctx.guild).req_level_good()
        else:
            req_level = await self.conf.guild(ctx.guild).req_level_bad()
            
        if req_level <= 0:
            return True, 0
            
        user_level = await self._get_user_level(ctx.guild, ctx.author)
        
        if user_level >= req_level:
            return True, req_level
        else:
            return False, req_level

    async def _get_user_level(self, guild: discord.Guild, member: discord.Member) -> int:
        """
        Attempts to retrieve a user's level from the LevelUp cog.
        Supports: Vertyco (Data/DB/API) and Standard Red (Config).
        """
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        
        uid_str = str(member.id)
        gid = guild.id
        
        # --- Method 1: Vertyco's New DB Structure (Pydantic) ---
        # Checks if the cog uses a 'db' object with specific attributes
        if hasattr(levelup, "db"):
            try:
                # Common path: levelup.db.get_conf(guild_id).users[user_id].level
                if hasattr(levelup.db, "get_conf"):
                    conf = levelup.db.get_conf(gid)
                    if conf:
                        # Pydantic models usually access users via dict-like get or attribute
                        users = getattr(conf, "users", {})
                        if isinstance(users, dict):
                            user_data = users.get(member.id) or users.get(uid_str)
                            if user_data:
                                # user_data might be an object or dict
                                if isinstance(user_data, dict):
                                    return int(user_data.get("level", 0))
                                else:
                                    return int(getattr(user_data, "level", 0))
            except Exception as e:
                log.debug(f"VibeCheck: Failed to read Vertyco DB: {e}")

        # --- Method 2: Vertyco's Old Data Cache (Dict) ---
        # Checks direct dictionary access used in older versions
        if hasattr(levelup, "data") and isinstance(levelup.data, dict):
            try:
                g_data = levelup.data.get(gid) or levelup.data.get(str(gid))
                if g_data:
                    users = g_data.get("users", {})
                    u_data = users.get(member.id) or users.get(uid_str)
                    if u_data:
                        return int(u_data.get("level", 0))
            except Exception as e:
                log.debug(f"VibeCheck: Failed to access Vertyco LevelUp data: {e}")

        # --- Method 3: Standard Red Config (Fallback) ---
        # Uses the official async Config API
        try:
            # FIX: Use .all() or specific value access, .get() does not exist on Config Group
            # We try to fetch just the level value directly for efficiency
            level = await levelup.config.guild(guild).users(uid_str).level()
            if level:
                return int(level)
        except AttributeError:
            # If 'users' group doesn't exist or structure is vastly different
            log.debug("VibeCheck: LevelUp Config structure mismatch.")
        except Exception as e:
            log.debug(f"VibeCheck: Config access failed: {e}")
            
        return 0

    async def _give_levelup_xp(self, guild: discord.Guild, member: discord.Member, amount: int):
        """
        Attempts to grant XP using the LevelUp cog.
        Supports common LevelUp implementations.
        """
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return

        try:
            # Try accessing via public API (common in some forks)
            if hasattr(levelup, "api") and hasattr(levelup.api, "add_xp"):
                await levelup.api.add_xp(guild.id, member.id, amount)
                
            # Try accessing standard method (common in original)
            elif hasattr(levelup, "add_xp"):
                import inspect
                sig = inspect.signature(levelup.add_xp)
                params = list(sig.parameters.keys())
                
                if params[0] == 'guild_id':
                    await levelup.add_xp(guild.id, member.id, amount)
                else:
                    await levelup.add_xp(member.id, amount)
        except Exception as e:
            log.error(f"Failed to grant LevelUp XP: {e}")

    async def _add_vibes(self, giver: discord.User, receiver: discord.User, amount: int, is_good: bool):
        """
        Handles the core logic for adding/subtracting vibes and triggering checks.
        """
        # 1. Update Receiver's Score
        receiver_settings = self.conf.user(receiver)
        current_vibes = await receiver_settings.vibes()
        new_vibes = current_vibes + amount
        await receiver_settings.vibes.set(new_vibes)

        # 2. Update Giver's Statistics (Sent Vibes & Interactions)
        async with self.conf.user(giver).all() as giver_data:
            if is_good:
                giver_data["good_vibes_sent"] = giver_data.get("good_vibes_sent", 0) + 1
                interaction_key = "good"
            else:
                giver_data["bad_vibes_sent"] = giver_data.get("bad_vibes_sent", 0) + 1
                interaction_key = "bad"

            interactions = giver_data.get("interactions", {})
            receiver_id_str = str(receiver.id)
            
            if receiver_id_str not in interactions:
                interactions[receiver_id_str] = {"good": 0, "bad": 0}
            
            interactions[receiver_id_str][interaction_key] += 1
            giver_data["interactions"] = interactions

        # 3. Find the Guild context and Member object for Receiver
        member_receiver = None
        target_guild = None
        
        for guild in self.bot.guilds:
            member = guild.get_member(receiver.id)
            if member:
                member_receiver = member
                target_guild = guild
                break 

        if not member_receiver or not target_guild:
            return 
            
        # 4. Check for New Member XP Reward
        if new_vibes > current_vibes: 
            guild_conf = self.conf.guild(target_guild)
            xp_minutes = await guild_conf.new_member_xp_minutes()
            
            if xp_minutes and xp_minutes > 0:
                xp_threshold = await guild_conf.new_member_xp_threshold()
                if current_vibes < xp_threshold <= new_vibes:
                    already_awarded = await receiver_settings.new_member_xp_awarded()
                    if not already_awarded:
                        if member_receiver.joined_at:
                            joined_at = member_receiver.joined_at
                            if joined_at.tzinfo is None:
                                joined_at = joined_at.replace(tzinfo=timezone.utc)
                            now = datetime.now(timezone.utc)
                            age_seconds = (now - joined_at).total_seconds()
                            
                            if age_seconds <= (xp_minutes * 60):
                                xp_amount = await guild_conf.new_member_xp_amount()
                                if xp_amount > 0:
                                    await self._give_levelup_xp(target_guild, member_receiver, xp_amount)
                                    await receiver_settings.new_member_xp_awarded.set(True)

        # 5. Run WarnSystem Integration Check
        if new_vibes < current_vibes:
            guild_conf = self.conf.guild(target_guild)
            
            ban_thresh = await guild_conf.ban_threshold()
            kick_thresh = await guild_conf.kick_threshold()
            warn_thresh = await guild_conf.warn_threshold()
            
            new_mem_age = await guild_conf.new_member_age_seconds()
            new_mem_thresh = await guild_conf.new_member_score_threshold()
            
            triggered = False
            
            # 1. Ban
            if ban_thresh is not None and new_vibes <= ban_thresh:
                reason = await guild_conf.ban_reason()
                await self._trigger_warnsystem(target_guild, member_receiver, giver, 5, f"{reason} (Score: {new_vibes})")
                triggered = True
            
            # 2. New Member Kick (If not banned)
            if not triggered and new_mem_age is not None and new_mem_thresh is not None and new_vibes <= new_mem_thresh:
                if member_receiver.joined_at:
                    joined_at = member_receiver.joined_at
                    if joined_at.tzinfo is None:
                        joined_at = joined_at.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    if (now - joined_at).total_seconds() < new_mem_age:
                        reason = await guild_conf.new_member_kick_reason()
                        await self._trigger_warnsystem(target_guild, member_receiver, giver, 3, f"{reason} (Score: {new_vibes})")
                        triggered = True

            # 3. Standard Kick
            if not triggered and kick_thresh is not None and new_vibes <= kick_thresh:
                reason = await guild_conf.kick_reason()
                await self._trigger_warnsystem(target_guild, member_receiver, giver, 3, f"{reason} (Score: {new_vibes})")
                triggered = True

            # 4. Standard Warn
            if not triggered and warn_thresh is not None and new_vibes <= warn_thresh:
                reason = await guild_conf.warn_reason()
                await self._trigger_warnsystem(target_guild, member_receiver, giver, 1, f"{reason} (Score: {new_vibes})")
                triggered = True
        
        # 6. Perform Logging
        await self._log_vibe_change(target_guild, giver, member_receiver, amount, current_vibes, new_vibes)
        
    async def _trigger_warnsystem(self, guild: discord.Guild, member: discord.Member, author: discord.User, level: int, reason: str):
        """
        Attempts to trigger Laggron's WarnSystem if loaded.
        """
        warn_cog = self.bot.get_cog("WarnSystem")
        if not warn_cog:
            log.warning("WarnSystem cog not found. VibeCheck warning not sent.")
            return

        try:
            # Attempt to use the warn method.
            if hasattr(warn_cog, "warn"):
                await warn_cog.warn(guild=guild, members=[member], author=author, reason=reason, level=level)
            elif hasattr(warn_cog, "api") and hasattr(warn_cog.api, "warn"):
                await warn_cog.api.warn(guild=guild, members=[member], author=author, reason=reason, level=level)
        except Exception as e:
            log.error(f"Failed to trigger WarnSystem: {e}")

    async def _log_vibe_change(self, guild: discord.Guild, giver: discord.User, receiver: discord.Member, amount: int, old_vibes: int, new_vibes: int):
        """Logs the vibe change and threshold breach events to the configured channel."""
        
        log_channel_id = await self.conf.guild(guild).log_channel_id()
        if log_channel_id is None:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
            
        emoji = "✨" if amount > 0 else "💀"
        action = "Good Vibes" if amount > 0 else "Bad Vibes"
        
        embed = discord.Embed(
            title=f"{emoji} Vibe Activity Log",
            color=discord.Color.green() if amount > 0 else discord.Color.red()
        )
        embed.add_field(name="Action", value=f"{action} ({abs(amount)})", inline=True)
        embed.add_field(name="Giver", value=f"{giver.mention} (`{giver.id}`)", inline=True)
        embed.add_field(name="Receiver", value=f"{receiver.mention} (`{receiver.id}`)", inline=True)
        embed.add_field(name="Old Score", value=old_vibes, inline=True)
        embed.add_field(name="New Score", value=new_vibes, inline=True)
        
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
                
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Clears a user's GLOBAL vibes score when they leave a guild."""
        
        user_data = await self.conf.user(member).all()
        if 'vibes' not in user_data or user_data.get('vibes') is None:
            return
        await self.conf.user(member).vibes.set(0)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Handles errors for commands in this cog, specifically custom cooldown messages."""
        
        if isinstance(error, commands.CommandOnCooldown):
            seconds = int(error.retry_after)
            
            if seconds >= 3600:
                time_unit = f"{seconds // 3600} hours"
            elif seconds >= 60:
                time_unit = f"{seconds // 60} minutes"
            else:
                time_unit = f"{seconds} seconds"

            await ctx.send(
                f"Slow down! You are on cooldown. Try again in **{time_unit}**.",
                ephemeral=True
            )
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send(f"Member not found: {str(error)}", ephemeral=True)
        else:
            raise error 
                
    async def _get_all_members(self, bot):
        """Get a list of members which have vibes."""
        ret = []
        for user_id, conf in (await self.conf.all_users()).items():
            vibes = conf.get("vibes")
            if not vibes:
                continue
            user = bot.get_user(user_id)
            if user is None:
                continue
            ret.append(MemberInfo(id=user_id, name=str(user), vibes=vibes))
        return ret
    
    async def _get_all_members_ratios(self, bot):
        """Get a list of members with calculated ratios."""
        ret = []
        for user_id, conf in (await self.conf.all_users()).items():
            good = conf.get("good_vibes_sent", 0)
            bad = conf.get("bad_vibes_sent", 0)
            
            if good == 0 and bad == 0:
                continue
                
            ratio = good - bad
            
            user = bot.get_user(user_id)
            if user is None:
                continue
                
            ret.append(MemberRatioInfo(id=user_id, name=str(user), ratio=ratio))
        return ret