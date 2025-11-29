import discord
import asyncio
import io
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from redbot.core import commands, Config, checks
from discord.ext import tasks

class AutoDelete(commands.Cog):
    """Automatically delete messages older than a specific threshold."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237498123, force_registration=True)
        
        default_guild = {
            "log_channel": None,
            "channels": {}  # Format: {channel_id: days_int}
        }
        self.config.register_guild(**default_guild)
        
        # Start the background task
        self.cleanup_loop.start()

    def cog_unload(self):
        self.cleanup_loop.cancel()

    @tasks.loop(hours=1)
    async def cleanup_loop(self):
        """Background task to check for old messages."""
        await self.bot.wait_until_ready()
        
        all_guilds = await self.config.all_guilds()
        
        for guild_id, data in all_guilds.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            log_channel_id = data.get("log_channel")
            watched_channels = data.get("channels", {})

            if not log_channel_id or not watched_channels:
                continue

            log_channel = guild.get_channel(log_channel_id)
            if not log_channel:
                continue

            # Iterate through configured channels
            for channel_id, days in watched_channels.items():
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue

                # Permission check
                if not channel.permissions_for(guild.me).manage_messages:
                    continue

                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                
                try:
                    # bulk=False is required for messages older than 14 days
                    deleted_messages = await channel.purge(
                        limit=None, 
                        before=cutoff, 
                        bulk=False,
                        reason="AutoDelete: Message older than threshold."
                    )
                except discord.HTTPException as e:
                    # In case of rate limits or other discord errors, skip and try next hour
                    print(f"AutoDelete Error in {guild.name}: {e}")
                    continue

                if deleted_messages:
                    await self.generate_log(log_channel, channel, deleted_messages)
                    # heavy operation sleep to prevent clogging the bot
                    await asyncio.sleep(2) 

    async def generate_log(self, log_channel: discord.TextChannel, source_channel: discord.TextChannel, messages: list):
        """Generates a text file and sends it to the log channel."""
        if not messages:
            return

        text_output = f"AutoDelete Log for #{source_channel.name} ({source_channel.id})\n"
        text_output += f"Date: {datetime.now(timezone.utc)}\n"
        text_output += f"Total Messages Deleted: {len(messages)}\n"
        text_output += "-" * 40 + "\n\n"

        # Messages are returned newest first usually, let's reverse to read chronologically
        for msg in reversed(messages):
            created_at = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{msg.author} ({msg.author.id})"
            content = msg.content if msg.content else "[No Text Content / Attachment / Embed]"
            
            text_output += f"[{created_at}] {author}:\n{content}\n\n"

        # Create file in memory
        f = io.BytesIO(text_output.encode("utf-8"))
        file_name = f"autodelete_{source_channel.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        try:
            await log_channel.send(
                content=f"Deleted **{len(messages)}** messages from {source_channel.mention}.",
                file=discord.File(f, filename=file_name)
            )
        except discord.Forbidden:
            pass # Can't send to log channel
        except discord.HTTPException:
            pass # File too big or other error

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def autodelete(self, ctx):
        """Manage auto-deletion settings."""
        pass

    @autodelete.command(name="logchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where deletion logs will be uploaded."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}. \n*Note: No messages will be deleted if this is not set.*")

    @autodelete.command(name="set")
    async def set_channel_config(self, ctx, channel: discord.TextChannel, days: int):
        """Configure a channel to auto-delete messages older than X days."""
        if days < 1:
            return await ctx.send("Days must be at least 1.")

        async with self.config.guild(ctx.guild).channels() as channels:
            channels[str(channel.id)] = days

        await ctx.send(f"Messages in {channel.mention} older than **{days} days** will now be automatically deleted.")

    @autodelete.command(name="remove")
    async def remove_channel_config(self, ctx, channel: discord.TextChannel):
        """Stop auto-deleting messages in a specific channel."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if str(channel.id) in channels:
                del channels[str(channel.id)]
                await ctx.send(f"Stopped auto-deletion for {channel.mention}.")
            else:
                await ctx.send("That channel is not currently configured for auto-deletion.")

    @autodelete.command(name="show", aliases=["settings", "list"])
    async def show_settings(self, ctx):
        """Show current auto-delete settings."""
        data = await self.config.guild(ctx.guild).all()
        log_channel_id = data.get("log_channel")
        channels = data.get("channels", {})

        if log_channel_id:
            log_chan = ctx.guild.get_channel(log_channel_id)
            log_str = log_chan.mention if log_chan else "Deleted Channel"
        else:
            log_str = "Not Set (Bot will not delete anything)"

        if not channels:
            chan_str = "No channels configured."
        else:
            chan_str = ""
            for cid, days in channels.items():
                c = ctx.guild.get_channel(int(cid))
                name = c.mention if c else "Deleted Channel"
                chan_str += f"{name}: **{days} days**\n"

        embed = discord.Embed(title="AutoDelete Settings", color=discord.Color.red())
        embed.add_field(name="Log Channel", value=log_str, inline=False)
        embed.add_field(name="Watched Channels", value=chan_str, inline=False)
        
        await ctx.send(embed=embed)

    @autodelete.command(name="runnow")
    async def force_run(self, ctx):
        """Manually trigger the deletion task now."""
        await ctx.send("Triggering cleanup task manually...")
        # We start the task function manually, but not as a loop
        try:
            await self.cleanup_loop()
        except Exception as e:
            await ctx.send(f"Error during manual run: {e}")
        else:
            await ctx.send("Manual cleanup finished.")

    @cleanup_loop.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()