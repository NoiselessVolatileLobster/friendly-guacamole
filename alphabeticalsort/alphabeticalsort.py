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

        # 1. Arrange them alphabetically
        sorted_channels = sorted(current_channels, key=lambda c: c.name.lower())

        # 2. Determine and display changes
        changes = []
        old_order = [c.name for c in current_channels]
        new_order = [c.name for c in sorted_channels]
        
        if old_order == new_order:
            return await ctx.send(f"Channels in **{category.name}** are already in alphabetical order.")

        # Generate the list of changes for confirmation
        for old_index, channel in enumerate(current_channels):
            new_index = sorted_channels.index(channel)
            
            if old_index != new_index:
                old_pos_name = old_order[old_index]
                new_pos_name = new_order[new_index]
                changes.append("• `{}` (Current Pos: {}) -> Moves to Pos: {} (`{}`)".format(
                    old_pos_name, 
                    old_index + 1, 
                    new_index + 1, 
                    new_pos_name
                ))

        # 3. Create the confirmation message using str.format()
        
        # Prepare the boxed content (list of changes) separately
        changes_box = box('\n'.join(changes), lang='diff')
        
        # Build the final message using str.format()
        confirmation_template = (
            "### Channel Reordering Confirmation for **{category_name}**\n\n"
            "The following channels will be reordered alphabetically:\n"
            "{changes_box}\n"
            "Do you want to apply these changes?"
        )

        confirmation_msg = confirmation_template.format(
            category_name=category.name,
            changes_box=changes_box
        )
        
        # 4. Confirmation and Application
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