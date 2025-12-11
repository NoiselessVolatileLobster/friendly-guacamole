import discord
from redbot.core import commands, checks
from redbot.core.utils.chat_formatting import box, pagify

class ChannelSyncCheck(commands.Cog):
    """
    Checks for channels that are out of sync with their category permissions.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def channelsync(self, ctx):
        """
        Checks all categories and lists channels with permissions out of sync.
        
        This will scan every category in the server. If a channel within a category
        does not match the category's permission overwrites (is not synced), it will be listed.
        """
        await ctx.typing()

        data = []
        
        # Iterate through all categories in the guild
        for category in ctx.guild.categories:
            unsynced_channels = []
            
            # Check text channels, voice channels, and stage channels in the category
            for channel in category.channels:
                # permissions_synced returns True if the channel permissions match the category
                if not channel.permissions_synced:
                    unsynced_channels.append(channel)

            if unsynced_channels:
                data.append((category, unsynced_channels))

        if not data:
            await ctx.send("All channels are currently synced with their categories.")
            return

        # Build the output string
        output_lines = []
        header = f"{'Category / Channel':<40} | {'Status':<15}"
        output_lines.append(header)
        output_lines.append("-" * len(header))

        for category, channels in data:
            # Add Category Header
            output_lines.append(f"[{category.name}]")
            
            for channel in channels:
                # formatting the channel name nicely
                c_name = f"  #{channel.name}" if isinstance(channel, discord.TextChannel) else f"  🔊 {channel.name}"
                
                # Check specifics of why (simple distinct check)
                status = "Out of Sync"
                
                output_lines.append(f"{c_name:<40} | {status:<15}")
            
            output_lines.append("") # Empty line between categories

        full_text = "\n".join(output_lines)

        # Pagify ensures if the list is huge, it splits into multiple messages
        for page in pagify(full_text):
            await ctx.send(box(page, lang="ini"))