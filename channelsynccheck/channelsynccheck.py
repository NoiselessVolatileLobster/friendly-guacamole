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
            """
            Compares overwrites between a category and a channel.
            Forces a detailed comparison even if one side appears empty.
            """
            cat_overwrites = category.overwrites
            chan_overwrites = channel.overwrites
            
            diffs = []
            
            # Helper: If an overwrite is None or 'Empty' (all Neutral), return a clean default dict.
            # Otherwise, return the dict of the existing overwrite.
            def get_clean_perms(overwrite):
                # If it's None, create a fresh Overwrite object (all None/Neutral)
                if overwrite is None:
                    return dict(discord.PermissionOverwrite())
                
                # If it exists, check if it's effectively empty (all None)
                # We still convert it to a dict to compare keys
                p_dict = dict(overwrite)
                if all(v is None for v in p_dict.values()):
                    # It's an empty overwrite object, treat it as default
                    return dict(discord.PermissionOverwrite())
                
                return p_dict

            # Get all roles/members involved in either set of overwrites
            all_targets = set(cat_overwrites.keys()) | set(chan_overwrites.keys())
            
            for target in all_targets:
                if isinstance(target, discord.Member):
                    continue

                cat_perms_obj = cat_overwrites.get(target)
                chan_perms_obj = chan_overwrites.get(target)

                # Convert both to dictionaries. 
                # If one is missing/empty, it becomes a dict of all 'None' (Neutral).
                c_p_dict = get_clean_perms(cat_perms_obj)
                ch_p_dict = get_clean_perms(chan_perms_obj)
                
                # Now we compare specific values
                target_diffs = []
                
                # Iterate over all permission flags (using one of the dicts as reference)
                for perm_name, cat_val in c_p_dict.items():
                    chan_val = ch_p_dict.get(perm_name)
                    
                    # We only care if they are DIFFERENT.
                    # Since we converted None/Empty overwrites to default dicts,
                    # logic like (None == None) holds true.
                    if cat_val != chan_val:
                        def fmt_val(v):
                            return "✅" if v is True else "❌" if v is False else "Nr" 
                        
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