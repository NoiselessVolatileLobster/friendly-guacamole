import discord
from redbot.core import commands
from redbot.core.utils.menus import confirm
from redbot.core.utils.chat_formatting import box

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
        
        current_channels = category.channels
        
        if not current_channels:
            return await ctx.send(f"The category **{category.name}** has no channels to sort.")

        sorted_channels = sorted(current_channels, key=lambda c: c.name.lower())

        changes = []
        old_order = [c.name for c in current_channels]
        new_order = [c.name for c in sorted_channels]
        
        if old_order == new_order:
            return await ctx.send(f"Channels in **{category.name}** are already in alphabetical order.")

        for old_index, channel in enumerate(current_channels):
            new_index = sorted_channels.index(channel)
            
            if old_index != new_index:
                old_pos_name = old_order[old_index]
                new_pos_name = new_order[new_index]
                changes.append(f"• `{old_pos_name}` (Current Pos: {old_index + 1}) -> Moves to Pos: {new_index + 1} (`{new_pos_name}`)")

        confirmation_msg = (
            f"### Channel Reordering Confirmation for **{category.name}**\n\n"
            "The following channels will be reordered alphabetically:\n"
            f"{box('\\n'.join(changes), lang='diff')}\n"
            "Do you want to apply these changes?"
        )
        
        if not await confirm(ctx, confirmation_msg):
            return await ctx.send("Channel reordering cancelled.")

        new_positions = []
        for index, channel in enumerate(sorted_channels):
            new_positions.append({"id": channel.id, "position": index})

        try:
            await category.edit(
                reason=f"Alphabetical sort requested by {ctx.author.name} ({ctx.author.id})",
                channel_positions=new_positions
            )
            await ctx.send(f"✅ Successfully reordered all {len(sorted_channels)} channels in **{category.name}** alphabetically.")
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to manage channels in this category.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ An error occurred while communicating with Discord: {e}")

# Standard Red-DiscordBot setup function
def setup(bot):
    """Entry point for the cog."""
    bot.add_cog(AlphabeticalSort(bot))