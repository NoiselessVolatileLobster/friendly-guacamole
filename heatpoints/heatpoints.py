import discord
import time
from redbot.core import commands, Config, checks

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
            "notification_channel": None # If None, sends in the active channel
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

    async def _send_reward_embed(self, channel, member, points):
        """
        Helper function to construct and send the embed.
        """
        guild = member.guild
        guild_data = await self.config.guild(guild).all()

        embed = discord.Embed(
            title=guild_data["embed_title"],
            description=guild_data["embed_description"].replace("{user}", member.mention).replace("{points}", str(points)),
            color=await self.bot.get_embed_color(channel)
        )

        if guild_data["embed_image"]:
            embed.set_image(url=guild_data["embed_image"])
        
        embed.set_footer(text=f"User: {member.display_name}", icon_url=member.display_avatar.url)

        try:
            await channel.send(content=member.mention, embed=embed)
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
        msg = (
            f"**Status:** {'Enabled' if data['enabled'] else 'Disabled'}\n"
            f"**Threshold:** {data['threshold']} points\n"
            f"**Title:** {data['embed_title']}\n"
            f"**Image:** {data['embed_image'] or 'None'}\n"
            f"**Description:** {data['embed_description']}"
        )
        await ctx.send(msg)

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