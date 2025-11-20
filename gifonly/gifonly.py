import discord
from redbot.core import commands, Config, checks
import re

class GifOnly(commands.Cog):
    """
    Enforce GIF-only conversation in specific channels.
    Supports uploaded files (Gboard, etc.) and common GIF links.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237645, force_registration=True)
        
        # Default configuration
        default_guild = {
            "channels": [],      # List of channel IDs
            "log_channel": None, # Channel ID for logging deletions
            "ignored_roles": []  # Optional: IDs of roles that bypass checks
        }
        self.config.register_guild(**default_guild)

        # Regex to find URLs in messages
        self.url_regex = re.compile(r'(https?://\S+)')
        
        # Common GIF providers that might not end in .gif
        self.gif_domains = [
            "tenor.com",
            "giphy.com",
            "imgur.com",
            "gfycat.com",
            "cdn.discordapp.com",
            "media.discordapp.net"
        ]

    async def is_gif(self, message: discord.Message) -> bool:
        """
        Logic to determine if a message contains a GIF.
        Checks attachments and URL patterns.
        """
        # 1. Check Attachments (Handles Gboard direct uploads, Discord uploads)
        if message.attachments:
            for attachment in message.attachments:
                # Check filename extension or content_type
                if attachment.filename.lower().endswith('.gif'):
                    return True
                if attachment.content_type == "image/gif":
                    return True
                # Some mobile keyboards upload as .mp4 (video) instead of gif
                if attachment.filename.lower().endswith('.mp4') or attachment.content_type == "video/mp4":
                    return True

        # 2. Check Content for Links
        content = message.content.lower()
        urls = self.url_regex.findall(content)

        for url in urls:
            # Check if link ends in .gif (most direct links)
            if url.endswith('.gif') or url.endswith('.gifv'):
                return True
            
            # Check if link is from a known GIF provider
            if any(domain in url for domain in self.gif_domains):
                return True

        return False

    async def log_deletion(self, message: discord.Message, guild_config):
        """
        Logs the deleted message to the configured log channel.
        """
        log_channel_id = guild_config["log_channel"]
        if not log_channel_id:
            return

        log_channel = message.guild.get_channel(log_channel_id)
        if not log_channel:
            return

        embed = discord.Embed(
            title="Non-GIF Message Deleted",
            description=f"**Author:** {message.author.mention} ({message.author.id})\n**Channel:** {message.channel.mention}",
            color=discord.Color.red()
        )
        
        if message.content:
            # Truncate content if too long
            content = (message.content[:1000] + '..') if len(message.content) > 1000 else message.content
            embed.add_field(name="Content", value=content, inline=False)
        
        if message.attachments:
            att_names = [a.filename for a in message.attachments]
            embed.add_field(name="Attachments", value=", ".join(att_names), inline=False)

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass # Bot doesn't have permission to send in log channel

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Fetch settings
        settings = await self.config.guild(message.guild).all()
        
        # Check if current channel is monitored
        if message.channel.id not in settings["channels"]:
            return

        # Check for admin/mod permissions (Optional: admins usually bypass)
        # If you want admins to be subject to the rules, remove this block.
        # if await self.bot.is_admin(message.author) or await self.bot.is_mod(message.author):
        #    return

        # Run GIF detection
        is_valid_gif = await self.is_gif(message)

        if not is_valid_gif:
            try:
                await message.delete()
                await self.log_deletion(message, settings)
                
                # Optional: Send a temp warning message
                warning = await message.channel.send(f"{message.author.mention}, only GIFs are allowed in this channel!", delete_after=5)
                
            except discord.Forbidden:
                print(f"GifOnly Error: Missing 'Manage Messages' permission in {message.guild.name}")
            except discord.NotFound:
                pass # Message already deleted

    # --- Admin Commands ---

    @commands.group()
    @checks.admin_or_permissions(manage_channels=True)
    async def gifonly(self, ctx):
        """Manage GIF-only channel settings."""
        pass

    @gifonly.command(name="add")
    async def gif_add(self, ctx, channel: discord.TextChannel):
        """Add a channel to the GIF-only enforcement list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id in channels:
                await ctx.send(f"{channel.mention} is already a GIF-only channel.")
            else:
                channels.append(channel.id)
                await ctx.send(f"{channel.mention} is now a GIF-only channel.")

    @gifonly.command(name="remove")
    async def gif_remove(self, ctx, channel: discord.TextChannel):
        """Remove a channel from the GIF-only enforcement list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id not in channels:
                await ctx.send(f"{channel.mention} is not in the GIF-only list.")
            else:
                channels.remove(channel.id)
                await ctx.send(f"{channel.mention} is no longer a GIF-only channel.")

    @gifonly.command(name="list")
    async def gif_list(self, ctx):
        """List all active GIF-only channels."""
        channel_ids = await self.config.guild(ctx.guild).channels()
        
        if not channel_ids:
            await ctx.send("There are no GIF-only channels set.")
            return

        channels = [ctx.guild.get_channel(c_id) for c_id in channel_ids]
        # Filter out deleted channels just in case
        valid_channels = [c.mention for c in channels if c is not None]
        
        embed = discord.Embed(title="GIF-Only Channels", description="\n".join(valid_channels), color=discord.Color.blue())
        await ctx.send(embed=embed)

    @gifonly.command(name="logchannel")
    async def gif_logchannel(self, ctx, channel: discord.TextChannel = None):
        """
        Set the channel where deleted messages are logged. 
        Leave blank to disable logging.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Deleted messages will now be logged to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Logging disabled.")