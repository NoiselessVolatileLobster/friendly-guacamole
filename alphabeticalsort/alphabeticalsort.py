import discord
import asyncio
from redbot.core import commands
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions

class AlphabeticalSort(commands.Cog):
    """Sorts channels in a specified category alphabetically."""

    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @commands.command(name="sortcategory")
    async def sort_channels_by_name(self, ctx: commands.Context, category: discord.CategoryChannel):
        """
        Looks at channels in a category, arranges them alphabetically,
        and asks for confirmation before applying.
        """
        
        # Gather channels by type (Discord sorts these groups independently)
        # We use isinstance to be safe across different discord.py versions
        text_channels = [c for c in category.channels if isinstance(c, discord.TextChannel)]
        voice_channels = [c for c in category.channels if isinstance(c, discord.VoiceChannel)]
        stage_channels = [c for c in category.channels if isinstance(c, discord.StageChannel)]
        
        all_channels = text_channels + voice_channels + stage_channels
        
        if not all_channels:
            return await ctx.send(f"The category **{category.name}** has no channels to sort.")

        # Helper function to calculate updates for a specific list of channels
        def get_sort_updates(channels):
            if not channels:
                # FIXED: Return an empty dict {} for updates, not an empty list []
                return {}, []
            
            # 1. Current state
            # We need the positions to ensure we stay within the category's "block"
            existing_positions = sorted([c.position for c in channels])
            
            # 2. Desired state (Alphabetical)
            sorted_channels = sorted(channels, key=lambda c: c.name.lower())
            
            updates = {} # Map channel -> new_position
            changes_log = []
            
            for i, channel in enumerate(sorted_channels):
                # Assign the i-th alphabetical channel to the i-th available position
                new_pos = existing_positions[i]
                
                if channel.position != new_pos:
                    updates[channel] = new_pos
                    changes_log.append(f"• {channel.name}")

            return updates, changes_log

        # Calculate updates for each group
        text_updates, text_log = get_sort_updates(text_channels)
        voice_updates, voice_log = get_sort_updates(voice_channels)
        stage_updates, stage_log = get_sort_updates(stage_channels)
        
        # Combine all updates
        master_updates = {**text_updates, **voice_updates, **stage_updates}
        
        if not master_updates:
            return await ctx.send(f"Channels in **{category.name}** are already in alphabetical order.")

        # 3. Create the confirmation message
        log_lines = []
        if text_log:
            log_lines.append("--- Text Channels Reordered ---")
            log_lines.extend(text_log)
        if voice_log:
            log_lines.append("\n--- Voice Channels Reordered ---")
            log_lines.extend(voice_log)
        if stage_log:
            log_lines.append("\n--- Stage Channels Reordered ---")
            log_lines.extend(stage_log)

        # Truncate log if it's too long for Discord
        log_text = '\n'.join(log_lines)
        if len(log_text) > 1000:
             log_text = log_text[:1000] + "\n... (and more)"

        changes_box = box(log_text, lang='diff')
        
        confirmation_template = (
            "### Channel Reordering Confirmation for **{category_name}**\n\n"
            "The following channels will be moved to new positions to ensure alphabetical order:\n"
            "{changes_box}\n\n"
            "React with ✅ to apply these changes or ❌ to cancel."
        )

        confirmation_msg = confirmation_template.format(
            category_name=category.name,
            changes_box=changes_box
        )
        
        # 4. Confirmation using Reactions
        msg = await ctx.send(confirmation_msg)
        start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
        
        pred = ReactionPredicate.yes_or_no(msg, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
        except asyncio.TimeoutError:
            await msg.delete()
            return await ctx.send("Confirmation timed out. Channel reordering cancelled.")

        if pred.result is False:
            await msg.delete()
            return await ctx.send("Channel reordering cancelled.")

        # 5. Apply the changes
        try:
            await msg.delete()
            progress_msg = await ctx.send("🔄 Applying sorting changes... (This may take a moment due to Discord rate limits)")
            
            # Bulk update positions
            await ctx.guild.edit_channel_positions(master_updates)
            
            await progress_msg.edit(content=f"✅ Successfully sorted channels in **{category.name}**.")
            
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to manage channels in this category.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ An error occurred while communicating with Discord: {e}")

# Standard Red-DiscordBot setup function
async def setup(bot):
    """Entry point for the cog."""
    await bot.add_cog(AlphabeticalSort(bot))