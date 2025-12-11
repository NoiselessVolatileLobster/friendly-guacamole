import discord
from redbot.core import commands, checks

class ChannelPager(discord.ui.View):
    """
    A View that handles iterating through channels within a specific category.
    Includes Previous/Next buttons and a "Back to Categories" button.
    """
    def __init__(self, ctx, all_data, category_name):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.all_data = all_data # The master dictionary of all scan results
        self.category_name = category_name
        self.channels = all_data[category_name] # List of channel data dicts for this category
        self.index = 0

    def _get_embed(self):
        """Builds the embed for the current channel page."""
        channel_data = self.channels[self.index]
        current_step = self.index + 1
        total_steps = len(self.channels)

        embed = discord.Embed(
            title=f"Category: {self.category_name}",
            color=discord.Color.orange()
        )
        embed.description = (
            f"**Channel:** {channel_data['name']} ({channel_data['type']})\n"
            f"**Status:** {current_step}/{total_steps}\n\n"
            f"```yaml\n{channel_data['diff']}\n```"
        )
        embed.set_footer(text="Use the buttons below to navigate.")
        return embed

    def _update_buttons(self):
        """Enable/Disable buttons based on current index."""
        self.children[0].disabled = (self.index == 0) # Previous
        self.children[1].disabled = (self.index == len(self.channels) - 1) # Next

    @discord.ui.button(label="< Previous", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._get_embed(), view=self)

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._get_embed(), view=self)

    @discord.ui.button(label="Back to Categories", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Go back to the main Category Selector view
        view = CategorySelectView(self.ctx, self.all_data)
        await interaction.response.edit_message(embed=view.get_initial_embed(), view=view)

class CategorySelect(discord.ui.Select):
    """The dropdown menu for selecting a category."""
    def __init__(self, ctx, all_data):
        self.ctx = ctx
        self.all_data = all_data
        
        # Create options from the data keys (Category Names)
        # Note: Select menus max out at 25 options.
        options = []
        for cat_name in list(all_data.keys())[:25]:
            # Add the count of unsynced channels to the description
            count = len(all_data[cat_name])
            options.append(discord.SelectOption(
                label=cat_name[:100], 
                description=f"{count} unsynced channels",
                value=cat_name
            ))

        super().__init__(placeholder="Select a Category to inspect...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        # Transition to the Pager View
        view = ChannelPager(self.ctx, self.all_data, selected_category)
        view._update_buttons()
        await interaction.response.edit_message(embed=view._get_embed(), view=view)

class CategorySelectView(discord.ui.View):
    """The initial view that holds the Category Dropdown."""
    def __init__(self, ctx, all_data):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.all_data = all_data
        self.add_item(CategorySelect(ctx, all_data))

    def get_initial_embed(self):
        total_issues = sum(len(v) for v in self.all_data.values())
        embed = discord.Embed(
            title="Channel Sync Report",
            description=f"Found **{total_issues}** channels across **{len(self.all_data)}** categories that are out of sync.\n\nPlease select a Category below to view specific permission differences.",
            color=discord.Color.red()
        )
        return embed

class ChannelSyncCheck(commands.Cog):
    """
    Checks for channels that are out of sync with their category permissions.
    """

    def __init__(self, bot):
        self.bot = bot

    def _get_perm_diff(self, category, channel):
        """Deep diff logic."""
        cat_overwrites = category.overwrites
        chan_overwrites = channel.overwrites
        diffs = []
        all_targets = set(cat_overwrites.keys()) | set(chan_overwrites.keys())
        
        for target in all_targets:
            if isinstance(target, discord.Member): continue # Skip member-specific overrides for brevity

            cat_perms = cat_overwrites.get(target)
            chan_perms = chan_overwrites.get(target)

            if cat_perms is None and chan_perms is not None:
                diffs.append(f"• {target.name}: Added in Channel")
                continue
            elif cat_perms is not None and chan_perms is None:
                diffs.append(f"• {target.name}: Missing in Channel")
                continue

            c_p_dict = dict(cat_perms)
            ch_p_dict = dict(chan_perms)
            target_diffs = []
            
            for perm_name, cat_val in c_p_dict.items():
                chan_val = ch_p_dict.get(perm_name)
                if cat_val != chan_val:
                    def fmt_val(v): return "✅" if v is True else "❌" if v is False else "Nr"
                    target_diffs.append(f"{perm_name}: {fmt_val(cat_val)} -> {fmt_val(chan_val)}")

            if target_diffs:
                diffs.append(f"• {target.name}: " + ", ".join(target_diffs))
                
        return diffs

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def channelsync(self, ctx):
        """
        Interactive menu to view unsynced channels.
        """
        loading_msg = await ctx.send("Scanning server permissions... this may take a moment.")

        # Data Structure:
        # { "Category Name": [ {"name": "channel", "type": "text", "diff": "string"} ] }
        results = {}

        for category in ctx.guild.categories:
            cat_results = []
            for channel in category.channels:
                if not channel.permissions_synced:
                    differences = self._get_perm_diff(category, channel)
                    if differences:
                        cat_results.append({
                            "name": channel.name,
                            "type": str(channel.type),
                            "diff": "\n".join(differences)
                        })
            
            if cat_results:
                results[category.name] = cat_results

        await loading_msg.delete()

        if not results:
            await ctx.send("✅ All channels are synced with their categories!")
            return

        # Initialize the View
        view = CategorySelectView(ctx, results)
        await ctx.send(embed=view.get_initial_embed(), view=view)