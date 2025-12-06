"""Module for the VibeCheck cog."""
import asyncio
import logging
import time
from collections import namedtuple
from typing import Tuple, Optional

import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.vibecheck")

__all__ = ["UNIQUE_ID", "VibeCheck"]

UNIQUE_ID = 0x9C02DCC7
MemberInfo = namedtuple("MemberInfo", "id name vibes")


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
            last_bad_vibe=0    # Timestamp of last usage
        )
        
        # Guild settings
        self.conf.register_guild(
            vibe_check_role_id=None,
            vibe_threshold=-10,
            log_channel_id=None,
            good_vibes_cooldown=3600,  # Default 60 minutes (in seconds)
            bad_vibes_cooldown=3600,   # Default 60 minutes (in seconds)
            
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
        
        # Check cooldown manually
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

    @commands.command()
    async def vibeboard(self, ctx: commands.Context, top: int = 10):
        """Prints out the Vibes leaderboard."""
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

    @commands.command(name="vibes")
    @commands.guild_only()
    async def get_vibes(self, ctx: commands.Context, user: discord.Member = None):
        """Check a user's vibes."""
        if user is None:
            user = ctx.author
        vibes = await self.conf.user(user).vibes()
        await ctx.send("{0} vibe score is: {1}".format(user.display_name, vibes))

    # --- COMMAND GROUP: VIBECHECKSET ---

    @commands.group(name="vibecheckset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def vibecheckset(self, ctx: commands.Context):
        """Configuration settings for VibeCheck."""
        pass

    @vibecheckset.command(name="ratio")
    async def vibe_ratio(self, ctx: commands.Context, user: discord.Member):
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

    @vibecheckset.command(name="role")
    @checks.admin_or_permissions(manage_roles=True)
    async def set_vibe_role(self, ctx: commands.Context, *, role: discord.Role = None):
        """Sets the role to be assigned when a user's vibes drop below threshold."""
        if role is None:
            await self.conf.guild(ctx.guild).vibe_check_role_id.set(None)
            await ctx.send("Automatic Vibe Check role assignment has been **disabled**.")
            return

        await self.conf.guild(ctx.guild).vibe_check_role_id.set(role.id)
        await ctx.send(f"The Vibe Check role has been set to **{role.name}**.")
            
    @vibecheckset.command(name="threshold")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_vibe_threshold(self, ctx: commands.Context, threshold: int):
        """Sets the negative vibes score threshold for assigning the Vibe Check role."""
        if threshold >= 0:
            return await ctx.send("The threshold must be a negative integer (e.g., `-15`).")
            
        await self.conf.guild(ctx.guild).vibe_threshold.set(threshold)
        await ctx.send(
            f"✅ The Vibe Check role threshold for this server is now set to **{threshold}**."
            f" Users will receive the role if their score drops to or below this value."
        )

    @vibecheckset.command(name="logchannel")
    @checks.admin_or_permissions(manage_guild=True)
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
    @checks.admin_or_permissions(manage_guild=True)
    async def vibe_cooldown(self, ctx: commands.Context):
        """Configure cooldowns for sending vibes."""
        pass

    @vibe_cooldown.command(name="goodvibes")
    async def set_good_vibes_cooldown(self, ctx: commands.Context, minutes: int):
        """
        Sets the cooldown for [p]goodvibes in minutes.
        Set to 0 to disable cooldowns.
        """
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
        """
        Sets the cooldown for [p]badvibes in minutes.
        Set to 0 to disable cooldowns.
        """
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
    @checks.admin_or_permissions(manage_guild=True)
    async def set_warning(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Low vibe score"):
        """
        Configure WarnSystem Level 1 (Warning).
        
        Usage: [p]vibecheckset warning <threshold> <reason>
        Example: [p]vibecheckset warning -50 Your vibe score is quite low
        
        Set threshold to 0 to disable.
        """
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
    @checks.admin_or_permissions(manage_guild=True)
    async def set_kick(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Very low vibe score"):
        """
        Configure WarnSystem Level 3 (Kick).
        
        Usage: [p]vibecheckset kick <threshold> <reason>
        Example: [p]vibecheckset kick -100 You have failed the vibe check
        
        Set threshold to 0 to disable.
        """
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
    @checks.admin_or_permissions(manage_guild=True)
    async def set_ban(self, ctx: commands.Context, threshold: int, *, reason: str = "VibeCheck: Critically low vibe score"):
        """
        Configure WarnSystem Level 5 (Ban).
        
        Usage: [p]vibecheckset ban <threshold> <reason>
        Example: [p]vibecheckset ban -200 Critical vibe failure
        
        Set threshold to 0 to disable.
        """
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
        
        # Thresholds
        threshold = settings.get('vibe_threshold')
        
        warn_thresh = settings.get('warn_threshold')
        warn_thresh_str = f"{warn_thresh} ({settings.get('warn_reason')})" if warn_thresh is not None else "Disabled"
        
        kick_thresh = settings.get('kick_threshold')
        kick_thresh_str = f"{kick_thresh} ({settings.get('kick_reason')})" if kick_thresh is not None else "Disabled"
        
        ban_thresh = settings.get('ban_threshold')
        ban_thresh_str = f"{ban_thresh} ({settings.get('ban_reason')})" if ban_thresh is not None else "Disabled"
        
        # Role
        role_id = settings.get('vibe_check_role_id')
        if role_id is None:
            role_text = "Not Set (Disabled)"
        else:
            role = ctx.guild.get_role(role_id)
            role_text = role.name if role else f"Deleted Role ({role_id})"
            
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

        embed = discord.Embed(title=f"VibeCheck Settings for {ctx.guild.name}", color=discord.Color.blue())
        embed.add_field(name="Role Threshold", value=str(threshold), inline=True)
        embed.add_field(name="Vibe Check Role", value=role_text, inline=True)
        embed.add_field(name="Log Channel", value=log_text, inline=False)
        
        embed.add_field(name="Good Vibes Cooldown", value=good_cd_str, inline=True)
        embed.add_field(name="Bad Vibes Cooldown", value=bad_cd_str, inline=True)
        
        embed.add_field(name="WarnSystem Lvl 1", value=warn_thresh_str, inline=False)
        embed.add_field(name="WarnSystem Lvl 3", value=kick_thresh_str, inline=False)
        embed.add_field(name="WarnSystem Lvl 5", value=ban_thresh_str, inline=False)
        
        await ctx.send(embed=embed)

    @vibecheckset.command(name="resetuser")
    @checks.is_owner()
    async def reset_user(self, ctx: commands.Context, user: discord.Member):
        """Resets a user's global vibes."""
        log.debug("Resetting %s's vibes", str(user))
        await self.conf.user(user).vibes.set(0)
        await ctx.send("{}'s vibes has been reset to 0.".format(user.name))

    @vibecheckset.command(name="resetall")
    @checks.is_owner()
    async def reset_all(self, ctx: commands.Context):
        """Resets the global vibes score for every user the bot knows."""
        
        confirmation_msg = await ctx.send(
            "⚠️ **WARNING:** This will reset the vibes score for **EVERY USER** globally. "
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
        
        await confirmation_msg.edit(content="Resetting all user vibes scores... this may take a moment.")

        all_user_data = await self.conf.all_users()
        reset_count = 0
        
        for user_id, user_conf in all_user_data.items():
            if user_conf.get("vibes") != 0:
                user_obj = self.bot.get_user(user_id) 
                if user_obj:
                    await self.conf.user(user_obj).vibes.set(0)
                    reset_count += 1
                
        await ctx.send(f"✅ **Success!** Reset the vibes score for **{reset_count}** users globally.")
        
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
        # Owners bypass cooldowns
        if await self.bot.is_owner(ctx.author):
            return

        # Fetch settings
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
            
        # 4. Run Role Assignment Check
        await self._vibe_check_role_assignment(member_receiver, new_vibes)
        
        # 5. Run WarnSystem Integration Check
        # Check if score CROSSED the threshold downwards (old > threshold >= new)
        guild_conf = self.conf.guild(target_guild)
        
        warn_thresh = await guild_conf.warn_threshold()
        if warn_thresh is not None and current_vibes > warn_thresh >= new_vibes:
            reason = await guild_conf.warn_reason()
            await self._trigger_warnsystem(
                target_guild, member_receiver, giver, 1, 
                f"{reason} (Score: {new_vibes})"
            )
            
        kick_thresh = await guild_conf.kick_threshold()
        if kick_thresh is not None and current_vibes > kick_thresh >= new_vibes:
            reason = await guild_conf.kick_reason()
            await self._trigger_warnsystem(
                target_guild, member_receiver, giver, 3, 
                f"{reason} (Score: {new_vibes})"
            )

        ban_thresh = await guild_conf.ban_threshold()
        if ban_thresh is not None and current_vibes > ban_thresh >= new_vibes:
            reason = await guild_conf.ban_reason()
            await self._trigger_warnsystem(
                target_guild, member_receiver, giver, 5, 
                f"{reason} (Score: {new_vibes})"
            )
        
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
                log.info(f"Triggered WarnSystem Level {level} for {member.display_name} via VibeCheck.")
            elif hasattr(warn_cog, "api") and hasattr(warn_cog.api, "warn"):
                await warn_cog.api.warn(guild=guild, members=[member], author=author, reason=reason, level=level)
                log.info(f"Triggered WarnSystem Level {level} for {member.display_name} via VibeCheck API.")
            else:
                log.error("WarnSystem found, but could not locate 'warn' method.")
                
        except Exception as e:
            log.error(f"Failed to trigger WarnSystem: {e}")

    async def _log_vibe_change(self, guild: discord.Guild, giver: discord.User, receiver: discord.Member, amount: int, old_vibes: int, new_vibes: int):
        """Logs the vibe change and threshold breach events to the configured channel."""
        
        log_channel_id = await self.conf.guild(guild).log_channel_id()
        if log_channel_id is None:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            log.warning(f"Log channel ID {log_channel_id} not found in guild {guild.name}.")
            return
            
        # Logging for the Vibe Change
        emoji = "✨" if amount > 0 else "💀"
        action = "Good Vibes" if amount > 0 else "Bad Vibes"
        
        embed = discord.Embed(
            title=f"{emoji} Vibe Activity Log",
            color=discord.Color.green() if amount > 0 else discord.Color.red()
        )
        embed.add_field(name="Action", value=f"{action} ({abs(amount)})", inline=True)
        embed.add_field(name="Giver", value=f"{giver.name} (`{giver.id}`)", inline=True)
        embed.add_field(name="Receiver", value=f"{receiver.mention} (`{receiver.id}`)", inline=True)
        embed.add_field(name="Old Score", value=old_vibes, inline=True)
        embed.add_field(name="New Score", value=new_vibes, inline=True)
        
        # Logging for Role Threshold Breach
        VIBE_THRESHOLD = await self.conf.guild(guild).vibe_threshold()
        
        threshold_breach_message = None
        
        if new_vibes <= VIBE_THRESHOLD < old_vibes:
            threshold_breach_message = f"**{receiver.mention}** has failed the Vibe Check! Score dropped to **{new_vibes}** (Threshold: {VIBE_THRESHOLD})."
            embed.color = discord.Color.dark_red()
        elif new_vibes > VIBE_THRESHOLD and old_vibes <= VIBE_THRESHOLD:
            threshold_breach_message = f"**{receiver.mention}** has passed the Vibe Check and recovered! Score is now **{new_vibes}** (Threshold: {VIBE_THRESHOLD})."
            embed.color = discord.Color.dark_green()
            
        try:
            await log_channel.send(embed=embed)
            
            if threshold_breach_message:
                await log_channel.send(threshold_breach_message)
                
        except discord.Forbidden:
            log.error(f"Bot lacks permissions to send messages in log channel {log_channel.name}.")
        except discord.HTTPException as e:
            log.error(f"HTTP error sending log message: {e}")

    async def _vibe_check_role_assignment(self, member: discord.Member, new_vibes: int):
        """Checks the vibes score and assigns/removes the Vibe Check role."""
        
        VIBE_THRESHOLD = await self.conf.guild(member.guild).vibe_threshold()
        VIBE_CHECK_ROLE_ID = await self.conf.guild(member.guild).vibe_check_role_id()
        
        if VIBE_CHECK_ROLE_ID is None:
            return

        vibe_role = member.guild.get_role(VIBE_CHECK_ROLE_ID)

        if vibe_role is None:
            log.warning(f"Configured Role ID {VIBE_CHECK_ROLE_ID} not found in guild {member.guild.name}.")
            return

        has_role = vibe_role in member.roles

        # Add Role: Score is less than or EQUAL TO threshold
        if new_vibes <= VIBE_THRESHOLD and not has_role:
            try:
                await member.add_roles(vibe_role, reason=f"Vibes score dropped to or below {VIBE_THRESHOLD} (Automatic Vibe Check).")
                log.info(f"Assigned Vibe Check role to {member.display_name}.")
            except discord.Forbidden:
                log.error(f"Bot lacks permissions to assign role {vibe_role.name} in {member.guild.name}. Check hierarchy.")
            except discord.HTTPException as e:
                log.error(f"HTTP error assigning role: {e}")

        # Remove Role: Score is strictly GREATER THAN threshold (recovered)
        elif new_vibes > VIBE_THRESHOLD and has_role:
            try:
                await member.remove_roles(vibe_role, reason=f"Vibes score recovered to above {VIBE_THRESHOLD}.")
                log.info(f"Removed Vibe Check role from {member.display_name}.")
            except discord.Forbidden:
                log.error(f"Bot lacks permissions to remove role {vibe_role.name} in {member.guild.name}. Check hierarchy.")
            except discord.HTTPException as e:
                log.error(f"HTTP error removing role: {e}")
                
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Clears a user's GLOBAL vibes score when they leave a guild."""
        
        user_data = await self.conf.user(member).all()
        
        if 'vibes' not in user_data or user_data.get('vibes') is None:
            return
            
        await self.conf.user(member).vibes.set(0)
        
        log.debug("Global vibes score for user %s cleared upon leaving guild %s.", 
                  str(member), member.guild.name)

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