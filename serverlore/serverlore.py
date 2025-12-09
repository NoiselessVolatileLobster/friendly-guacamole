import discord
import json
import time
import io
import logging
from typing import Union, List, Dict, Optional
import math

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import pagify, box
from redbot.core.utils.mod import is_mod_or_superior

log = logging.getLogger("red.serverlore")

class LoreView(discord.ui.View):
    def __init__(self, ctx, entries: List[Dict], target_id: int, cog: "ServerLore", user_name: Optional[str] = None):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.entries = entries
        self.target_id = target_id
        self.cog = cog
        self.user_name = user_name
        self.index = 0
        self.total = len(entries)
        
        # Determine if user is a mod/admin to show the delete button
        self.is_mod = False

    async def check_perms(self):
        # We check perms asynchronously before sending
        is_owner = await self.ctx.bot.is_owner(self.ctx.author)
        # is_mod_or_superior checks for Mod role, Admin role, or Guild Owner
        is_mod = await is_mod_or_superior(self.ctx.bot, self.ctx.author)
        self.is_mod = is_owner or is_mod
        
        # Remove delete button if not mod
        if not self.is_mod:
            # We filter children by custom_id to be safe
            for item in self.children:
                if getattr(item, "custom_id", "") == "delete_btn":
                    self.remove_item(item)
                    break

    def get_embed(self) -> discord.Embed:
        entry = self.entries[self.index]
        
        # Prepare data
        author_id = entry.get("author")
        content = entry.get("content", "No content.")
        timestamp = entry.get("date", 0)
        link = entry.get("link", None)
        
        title_text = f"Lore for {self.user_name}" if self.user_name else f"Lore for User ID: {self.target_id}"
        embed = discord.Embed(title=title_text, color=discord.Color.blue())
        embed.description = content
        
        embed.add_field(name="Author", value=f"<@{author_id}> ({author_id})")
        embed.add_field(name="Date", value=f"<t:{int(timestamp)}:F>")
        
        if link:
            embed.add_field(name="Link", value=f"[Jump to Message]({link})", inline=False)
            
        embed.set_footer(text=f"Entry {self.index + 1}/{self.total}")
        return embed

    def update_buttons(self):
        # We need to find the specific buttons to enable/disable them
        # Since we might have removed the delete button, we can't rely on fixed indices
        for item in self.children:
            if getattr(item, "label", "") == "◀":
                item.disabled = self.index == 0
            elif getattr(item, "label", "") == "▶":
                item.disabled = self.index == self.total - 1
        
        if self.total == 0:
            self.stop()

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="🗑️ Delete Entry", style=discord.ButtonStyle.danger, custom_id="delete_btn")
    async def delete_entry(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_mod:
            return await interaction.response.send_message("You do not have permission to delete lore.", ephemeral=True)

        deletion_success = False
        item_to_delete = None

        # Logic to remove from config
        async with self.cog.config.guild(self.ctx.guild).lore() as lore_data:
            str_id = str(self.target_id)
            if str_id in lore_data:
                # We fetch the specific item currently being viewed to ensure we delete the right one
                # incase the list shifted (rare race condition, but safe approach)
                item_to_delete = self.entries[self.index]
                
                # Check if this exact entry exists in the config list
                if item_to_delete in lore_data[str_id]:
                    lore_data[str_id].remove(item_to_delete)
                    log.info(f"Lore deleted by {self.ctx.author} for user {self.target_id}: {item_to_delete}")
                    deletion_success = True

        if deletion_success:
            # Logging logic
            log_channel_id = await self.cog.config.guild(self.ctx.guild).log_channel()
            if log_channel_id:
                log_channel = self.ctx.guild.get_channel(log_channel_id)
                if log_channel and log_channel.permissions_for(self.ctx.guild.me).send_messages:
                    target_member = self.ctx.guild.get_member(self.target_id)
                    target_text = f"{target_member} (`{self.target_id}`)" if target_member else f"User ID `{self.target_id}`"
                    
                    orig_author_id = item_to_delete.get("author", "Unknown")
                    orig_content = item_to_delete.get("content", "No content.")
                    
                    log_text = (
                        f"**Lore Deleted**\n"
                        f"**Target:** {target_text}\n"
                        f"**Deleted By:** {interaction.user} (`{interaction.user.id}`)\n"
                        f"**Original Author:** <@{orig_author_id}> (`{orig_author_id}`)\n"
                        f"**Content:** {orig_content}"
                    )
                    try:
                        await log_channel.send(log_text)
                    except discord.HTTPException as e:
                        log.error(f"Failed to send lore deletion log: {e}")

            # Update local list
            self.entries.pop(self.index)
            self.total = len(self.entries)

            if self.total == 0:
                # If no entries left, edit the message to say so and stop the view
                await interaction.response.edit_message(content="No lore entries remaining.", embed=None, view=None)
                self.stop()
            else:
                # Adjust index if we deleted the last item
                if self.index >= self.total:
                    self.index = self.total - 1
                self.update_buttons()
                # Update the message with the new list state
                await interaction.response.edit_message(embed=self.get_embed(), view=self)
            
            # Send confirmation as a followup (must be done AFTER the response)
            await interaction.followup.send(f"Entry deleted by {self.ctx.author.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message("Could not find that entry to delete. It may have already been removed.", ephemeral=True)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class AllLoreView(discord.ui.View):
    def __init__(self, ctx, lore_data: Dict):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.lore_data = lore_data
        self.user_ids = list(lore_data.keys())
        self.selected_user_id = None
        self.page = 0
        self.per_page = 10
        self.options_per_select = 25
        
        # User list pagination (for select menu)
        self.user_list_page = 0
        
        # Initialize UI
        self.update_ui()

    def get_embed(self) -> discord.Embed:
        if not self.selected_user_id:
            embed = discord.Embed(title="Server Lore - All Entries", color=discord.Color.gold())
            embed.description = "Please select a user from the dropdown menu to view their lore."
            embed.set_footer(text=f"Total Users with Lore: {len(self.user_ids)}")
            return embed
        
        entries = self.lore_data.get(self.selected_user_id, [])
        total_entries = len(entries)
        total_pages = math.ceil(total_entries / self.per_page)
        
        # Slice entries for current page
        start = self.page * self.per_page
        end = start + self.per_page
        current_entries = entries[start:end]
        
        # Get username
        try:
            member = self.ctx.guild.get_member(int(self.selected_user_id))
            username = member.display_name if member else f"User {self.selected_user_id}"
        except:
            username = f"User {self.selected_user_id}"

        embed = discord.Embed(title=f"Lore for {username}", color=discord.Color.blue())
        
        desc_lines = []
        for entry in current_entries:
            content = entry.get("content", "No content")
            link = entry.get("link")
            
            # Truncate content if too long for a single line summary
            if len(content) > 50:
                content = content[:47] + "..."
            
            line = f"• {content}"
            if link:
                line += f" ([Link]({link}))"
            desc_lines.append(line)
        
        if not desc_lines:
            embed.description = "No lore entries found."
        else:
            embed.description = "\n".join(desc_lines)
            
        if total_pages > 1:
            embed.set_footer(text=f"Page {self.page + 1} of {total_pages}")
            
        return embed

    def update_ui(self):
        self.clear_items()
        
        # --- SELECT MENU ---
        # Handle user list pagination for the dropdown
        start_user = self.user_list_page * self.options_per_select
        end_user = start_user + self.options_per_select
        current_users = self.user_ids[start_user:end_user]
        
        options = []
        for uid in current_users:
            try:
                member = self.ctx.guild.get_member(int(uid))
                label = member.display_name if member else f"User {uid}"
            except:
                label = f"User {uid}"
            
            # Label limit is 100 chars
            label = label[:100]
            options.append(discord.SelectOption(
                label=label, 
                value=uid, 
                default=(uid == self.selected_user_id)
            ))
        
        if options:
            select = discord.ui.Select(
                placeholder=f"Select User (Page {self.user_list_page + 1})", 
                options=options, 
                min_values=1, 
                max_values=1,
                row=0
            )
            select.callback = self.on_select
            self.add_item(select)
            
        # --- USER LIST PAGINATION BUTTONS ---
        # If we have more users than fit in one select, add buttons to cycle user list
        total_user_pages = math.ceil(len(self.user_ids) / self.options_per_select)
        if total_user_pages > 1:
            prev_users = discord.ui.Button(label="<< Prev Users", row=1, disabled=(self.user_list_page == 0), style=discord.ButtonStyle.secondary)
            prev_users.callback = self.on_prev_users
            self.add_item(prev_users)
            
            next_users = discord.ui.Button(label="Next Users >>", row=1, disabled=(self.user_list_page >= total_user_pages - 1), style=discord.ButtonStyle.secondary)
            next_users.callback = self.on_next_users
            self.add_item(next_users)

        # --- LORE PAGINATION BUTTONS ---
        # Only show if a user is selected and has enough lore
        if self.selected_user_id:
            entries = self.lore_data.get(self.selected_user_id, [])
            total_pages = math.ceil(len(entries) / self.per_page)
            
            if total_pages > 1:
                prev_lore = discord.ui.Button(label="◀ Lore", row=2, disabled=(self.page == 0), style=discord.ButtonStyle.primary)
                prev_lore.callback = self.on_prev_lore
                self.add_item(prev_lore)
                
                next_lore = discord.ui.Button(label="Lore ▶", row=2, disabled=(self.page >= total_pages - 1), style=discord.ButtonStyle.primary)
                next_lore.callback = self.on_next_lore
                self.add_item(next_lore)

    async def on_select(self, interaction: discord.Interaction):
        self.selected_user_id = interaction.data["values"][0]
        self.page = 0 # Reset lore page
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
        
    async def on_prev_users(self, interaction: discord.Interaction):
        self.user_list_page -= 1
        self.selected_user_id = None # Reset selection when changing user page list
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_next_users(self, interaction: discord.Interaction):
        self.user_list_page += 1
        self.selected_user_id = None # Reset selection when changing user page list
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_prev_lore(self, interaction: discord.Interaction):
        self.page -= 1
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_next_lore(self, interaction: discord.Interaction):
        self.page += 1
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class ConfirmationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.value = None

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

class ServerLore(commands.Cog):
    """Store and manage lore about server members."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        # Structure: { "USER_ID_STRING": [ {entry_dict}, {entry_dict} ] }
        self.config.register_guild(lore={}, log_channel=None)

    @commands.command()
    @commands.guild_only()
    async def newlore(self, ctx, user: discord.Member, *, message: str):
        """Create a new lore entry for a user."""
        # Send the confirmation message FIRST to capture its ID/Link
        bot_msg = await ctx.send(f"✅ New lore added for **{user.display_name}**.")
        
        entry = {
            "user": user.id,
            "author": ctx.author.id,
            "content": message,
            "date": time.time(),
            "type": "RegularNote",
            "link": bot_msg.jump_url
        }

        async with self.config.guild(ctx.guild).lore() as lore_data:
            str_id = str(user.id)
            if str_id not in lore_data:
                lore_data[str_id] = []
            lore_data[str_id].append(entry)

        # Logging logic
        log_channel_id = await self.config.guild(ctx.guild).log_channel()
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            # Ensure the channel exists and we can speak in it
            if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
                log_text = (
                    f"**New Lore Created**\n"
                    f"**Target:** {user} (`{user.id}`)\n"
                    f"**Author:** {ctx.author} (`{ctx.author.id}`)\n"
                    f"**Lore:** {message}\n"
                    f"**Link:** <{bot_msg.jump_url}>"
                )
                try:
                    await log_channel.send(log_text)
                except discord.HTTPException as e:
                    log.error(f"Failed to send lore log in {log_channel.name}: {e}")


    @commands.command()
    @commands.guild_only()
    async def seelore(self, ctx, user: Union[discord.Member, discord.User, int]):
        """
        View lore for a user.
        Mods can view lore for users who have left by using ID.
        """
        if isinstance(user, int):
            try:
                user_obj = await self.bot.fetch_user(user)
                user_id = user
                is_member = ctx.guild.get_member(user) is not None
            except:
                user_obj = None
                user_id = user
                is_member = False
        else:
            user_obj = user
            user_id = user.id
            is_member = ctx.guild.get_member(user_id) is not None

        # Check permissions regarding left users
        # is_mod_or_superior checks for Mod role, Admin role, or Guild Owner in Red's config
        is_mod = await is_mod_or_superior(self.bot, ctx.author) or await self.bot.is_owner(ctx.author)
        
        if not is_member and not is_mod:
            return await ctx.send("That user is no longer in this server.")

        lore_data = await self.config.guild(ctx.guild).lore()
        user_lore = lore_data.get(str(user_id), [])

        if not user_lore:
            return await ctx.send(f"No lore found for ID {user_id}.")

        user_name = user_obj.display_name if user_obj else None
        view = LoreView(ctx, user_lore, user_id, self, user_name=user_name)
        await view.check_perms()
        view.update_buttons()
        
        await ctx.send(embed=view.get_embed(), view=view)

    # --- Administrator Commands ---
    
    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def lorelogs(self, ctx, channel: discord.TextChannel = None):
        """
        Set the channel where new lore will be logged. 
        Run without a channel to disable logging.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"✅ Lore logs will now be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("✅ Lore logging has been disabled.")

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def seealllore(self, ctx):
        """Display all known lore for the server using an interactive menu."""
        lore_data = await self.config.guild(ctx.guild).lore()
        if not lore_data:
            return await ctx.send("No lore exists in this server.")

        view = AllLoreView(ctx, lore_data)
        await ctx.send(embed=view.get_embed(), view=view)

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def exportlore(self, ctx):
        """Export all lore to a JSON file."""
        lore_data = await self.config.guild(ctx.guild).lore()
        
        if not lore_data:
            return await ctx.send("There is no lore to export.")

        # Formatting JSON
        json_str = json.dumps(lore_data, indent=4)
        to_file = io.BytesIO(json_str.encode())
        
        await ctx.send(
            "Here is the exported lore data:",
            file=discord.File(to_file, filename="serverlore_export.json")
        )

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def importlore(self, ctx):
        """Import lore from a JSON file (attached to the command message)."""
        if not ctx.message.attachments:
            return await ctx.send("Please attach a valid JSON file to this command.")

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".json"):
            return await ctx.send("The attached file must be a .json file.")

        try:
            file_bytes = await attachment.read()
            data = json.loads(file_bytes)
        except json.JSONDecodeError:
            return await ctx.send("Invalid JSON format.")

        # Basic Validation of structure
        if not isinstance(data, dict):
             return await ctx.send("JSON root must be a dictionary.")
        
        # Save to config
        async with self.config.guild(ctx.guild).lore() as lore_data:
            count = 0
            for uid, entries in data.items():
                if not isinstance(entries, list):
                    continue
                
                # Ensure the user ID is a string for the key
                str_id = str(uid)
                if str_id not in lore_data:
                    lore_data[str_id] = []
                
                # Append entries
                lore_data[str_id].extend(entries)
                count += len(entries)

        await ctx.send(f"✅ Successfully imported {count} lore entries.")

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def deletelore(self, ctx, user_id: int):
        """Delete all lore for a specific User ID."""
        async with self.config.guild(ctx.guild).lore() as lore_data:
            str_id = str(user_id)
            if str_id in lore_data:
                del lore_data[str_id]
                await ctx.send(f"🗑️ All lore for User ID `{user_id}` has been deleted.")
            else:
                await ctx.send(f"No lore found for User ID `{user_id}`.")

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def resetalllore(self, ctx):
        """Delete ALL lore for the entire server. Warning: Irreversible."""
        
        warning_embed = discord.Embed(
            title="⚠️ DANGER ZONE",
            description="You are about to delete **ALL** lore data for this server. This cannot be undone.\n\nAre you sure?",
            color=discord.Color.red()
        )
        
        view = ConfirmationView()
        msg = await ctx.send(embed=warning_embed, view=view)
        await view.wait()
        
        if view.value is None:
            await msg.edit(content="Timed out.", embed=None, view=None)
        elif view.value:
            await self.config.guild(ctx.guild).lore.set({})
            await msg.edit(content="✅ **System Reset.** All lore has been wiped.", embed=None, view=None)
        else:
            await msg.edit(content="❌ Operation cancelled.", embed=None, view=None)