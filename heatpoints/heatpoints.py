import discord
import time
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import pagify, box

class HeatPoints(commands.Cog):
    """
    Track user activity 'Heatpoints' and reward them upon reaching a threshold.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8475619231, force_registration=True)

        # Default configuration
        default_guild = {
            "threshold": 100,
            "enabled": True,
            "embed_title": "Heat Level Reached!",
            "embed_description": "Congratulations! You have reached the activity threshold.",
            "embed_image": "",  # Empty string means no image
            "notification_channel": None, # If None, sends in the active channel
            "use_active_channel": True # If True, overrides notification_channel to use the message channel
        }

        default_member = {
            "heatpoints": 0,
            "last_point_timestamp": 0.0,
            "has_triggered_reward": False # Ensures we don't spam the embed if they keep talking
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        """
        Listener to add points when a user sends a message.
        """
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Check if system is enabled for this guild
        if not await self.config.guild(message.guild).enabled():
            return

        member = message.author
        guild = message.guild
        current_time = time.time()

        member_data = await self.config.member(member).all()
        last_point_time = member_data["last_point_timestamp"]

        # Check if 1 hour (3600 seconds) has passed since the last point
        if current_time - last_point_time >= 3600:
            # Grant point
            new_points = member_data["heatpoints"] + 1
            
            # Save new state
            async with self.config.member(member).all() as u_data:
                u_data["heatpoints"] = new_points
                u_data["last_point_timestamp"] = current_time

            # Check threshold logic
            threshold = await self.config.guild(guild).threshold()
            
            # Only trigger if they hit the threshold EXACTLY or crossed it without having triggered it before.
            # This prevents spamming the embed every hour after they pass the number.
            if new_points >= threshold and not member_data["has_triggered_reward"]:
                await self._send_reward_embed(message.channel, member, new_points)
                await self.config.member(member).has_triggered_reward.set(True)

    async def _send_reward_embed(self, source_channel, member, points):
        """
        Helper function to construct and send the embed.
        """
        guild = member.guild
        guild_data = await self.config.guild(guild).all()

        # Determine destination channel
        dest_channel = source_channel
        
        # If we are NOT forced to use the active channel, and a notification channel is set
        if not guild_data["use_active_channel"] and guild_data["notification_channel"]:
            found_channel = guild.get_channel(guild_data["notification_channel"])
            if found_channel:
                dest_channel = found_channel

        embed = discord.Embed(
            title=guild_data["embed_title"],
            description=guild_data["embed_description"].replace("{user}", member.mention).replace("{points}", str(points)),
            color=await self.bot.get_embed_color(dest_channel)
        )

        if guild_data["embed_image"]:
            embed.set_image(url=guild_data["embed_image"])
        
        embed.set_footer(text=f"User: {member.display_name}", icon_url=member.display_avatar.url)

        try:
            await dest_channel.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            # Bot lacks permissions to send embed in this channel
            pass
        except discord.HTTPException:
            # Malformed embed data (e.g. bad image URL)
            pass

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def heatset(self, ctx):
        """
        Configuration commands for HeatPoints.
        """
        pass

    @heatset.command()
    async def threshold(self, ctx, amount: int):
        """
        Set the number of heatpoints required to trigger the embed.
        """
        if amount < 1:
            await ctx.send("Threshold must be at least 1.")
            return
        
        await self.config.guild(ctx.guild).threshold.set(amount)
        await ctx.send(f"Heatpoint threshold set to **{amount}**.")

    @heatset.command()
    async def toggle(self, ctx):
        """
        Enable or disable the HeatPoints system.
        """
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        status = "disabled" if current else "enabled"
        await ctx.send(f"HeatPoints system is now **{status}**.")

    @heatset.command()
    async def channel(self, ctx, channel: discord.TextChannel = None):
        """
        Set a specific channel for reward embeds.
        
        If no channel is specified, the specific channel setting is cleared.
        """
        if channel is None:
            await self.config.guild(ctx.guild).notification_channel.set(None)
            await ctx.send("Specific notification channel cleared. Embeds will post in the active channel.")
        else:
            await self.config.guild(ctx.guild).notification_channel.set(channel.id)
            # Automatically switch to using the specific channel
            await self.config.guild(ctx.guild).use_active_channel.set(False)
            await ctx.send(f"Reward embeds will now be sent to {channel.mention}.")

    @heatset.command()
    async def toggleorigin(self, ctx):
        """
        Toggle whether embeds are posted in the channel where the user reached the threshold.
        
        True = Always post in the active channel (Origin).
        False = Post in the specific channel configured via `[p]heatset channel` (if set).
        """
        current = await self.config.guild(ctx.guild).use_active_channel()
        new_state = not current
        await self.config.guild(ctx.guild).use_active_channel.set(new_state)
        
        state_str = "Active Channel (Origin)" if new_state else "Configured Specific Channel"
        await ctx.send(f"Embed destination mode set to: **{state_str}**.")

    @heatset.command()
    async def configembed(self, ctx, title: str, image_url: str, *, description: str):
        """
        Configure the reward embed.
        
        Syntax: [p]heatset configembed "Title in Quotes" "Image URL or None" Description goes here
        
        Note: Use "None" (without quotes) for the image URL if you don't want an image.
        You can use {user} and {points} placeholders in the description.
        """
        
        valid_image = image_url if image_url.lower() != "none" else ""
        
        # Validate URL roughly
        if valid_image and not (valid_image.startswith("http") or valid_image.startswith("https")):
            await ctx.send("Warning: Image URL should start with http:// or https://. Saving anyway, but it may not render.")

        async with self.config.guild(ctx.guild).all() as g_data:
            g_data["embed_title"] = title
            g_data["embed_image"] = valid_image
            g_data["embed_description"] = description

        # Send a preview
        embed = discord.Embed(
            title=title,
            description=description.replace("{user}", ctx.author.mention).replace("{points}", "100"),
            color=await self.bot.get_embed_color(ctx.channel)
        )
        if valid_image:
            embed.set_image(url=valid_image)
        
        await ctx.send("Configuration saved! Here is a preview:", embed=embed)

    @heatset.command()
    async def view(self, ctx):
        """
        View current settings.
        """
        data = await self.config.guild(ctx.guild).all()
        
        channel_name = "None"
        if data['notification_channel']:
            chan = ctx.guild.get_channel(data['notification_channel'])
            channel_name = chan.mention if chan else "Deleted Channel"
            
        mode = "Active Channel (Origin)" if data['use_active_channel'] else f"Specific Channel: {channel_name}"

        msg = (
            f"**Status:** {'Enabled' if data['enabled'] else 'Disabled'}\n"
            f"**Threshold:** {data['threshold']} points\n"
            f"**Mode:** {mode}\n"
            f"**Title:** {data['embed_title']}\n"
            f"**Image:** {data['embed_image'] or 'None'}\n"
            f"**Description:** {data['embed_description']}"
        )
        await ctx.send(msg)

    @heatset.command()
    async def list(self, ctx):
        """
        List all users and their current heatpoints (High to Low).
        """
        all_members = await self.config.all_members(ctx.guild)
        
        # Create a list of tuples: (Member Name, Points)
        # We only include members currently in the guild with > 0 points
        leaderboard = []
        for member_id, data in all_members.items():
            points = data.get("heatpoints", 0)
            if points > 0:
                member = ctx.guild.get_member(member_id)
                if member:
                    leaderboard.append((member.display_name, points))

        if not leaderboard:
            await ctx.send("No users have recorded heatpoints yet.")
            return

        # Sort by points descending
        leaderboard.sort(key=lambda x: x[1], reverse=True)

        # Build the output string
        output = "User Heatpoints:\n\n"
        for name, points in leaderboard:
            output += f"{name}: {points}\n"

        # Pagify and send to avoid message length limits
        for page in pagify(output):
            await ctx.send(box(page))

    @commands.command()
    @commands.guild_only()
    async def myheat(self, ctx):
        """
        Check your current heatpoints.
        """
        points = await self.config.member(ctx.author).heatpoints()
        threshold = await self.config.guild(ctx.guild).threshold()
        
        await ctx.send(f"🔥 **{ctx.author.display_name}**, you have **{points}** heatpoints. (Goal: {threshold})")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def resetheat(self, ctx, member: discord.Member):
        """
        Reset a specific user's heatpoints and reward status.
        """
        await self.config.member(member).clear()
        await ctx.send(f"Heatpoints reset for {member.display_name}.")