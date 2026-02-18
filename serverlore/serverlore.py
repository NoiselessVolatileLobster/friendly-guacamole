import discord
import json
import time
import io
import logging
import math
from typing import Union, List, Dict, Optional

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import pagify, box
from redbot.core.utils.mod import is_mod_or_superior

log = logging.getLogger("red.noiseless.serverlore")

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
        
        self.is_mod = False

    async def check_perms(self):
        is_owner = await self.ctx.bot.is_owner(self.ctx.author)
        is_mod = await is_mod_or_superior(self.ctx.bot, self.ctx.author)
        self.is_mod = is_owner or is_mod
        
        if not self.is_mod:
            for item in self.children:
                if getattr(item, "custom_id", "") == "delete_btn":
                    self.remove_item(item)
                    break

    def get_embed(self) -> discord.Embed:
        if not self.entries:
            return discord.Embed(title="No Lore", description="No entries found.", color=discord.Color.red())

        entry = self.entries[self.index]
        
        author_id = entry.get("author")
        content = entry.get("content", "No content.")
        timestamp = entry.get("date", 0)
        link = entry.get("link", None)
        
        title_text = f"Lore for {self.user_name}" if self.user_name else f"Lore for User ID: {self.target_id}"
        embed = discord.Embed(title=title_text, color=discord.Color.blue())
        embed.description = content
        
        embed.add_field(name="Author", value=f"<@{author_id}>", inline=True)
        embed.add_field(name="Date", value=f"<t:{int(timestamp)}:F>", inline=True)
        
        if link:
            embed.add_field(name="Context", value=f"[Jump to Message]({link})", inline=False)
            
        embed.set_footer(text=f"Entry {self.index + 1}/{self.total}")
        return embed

    def update_buttons(self):
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

        async with self.cog.config.guild(self.ctx.guild).lore() as lore_data:
            str_id = str(self.target_id)
            if str_id in lore_data:
                item_to_delete = self.entries[self.index]
                if item_to_delete in lore_data[str_id]:
                    lore_data[str_id].remove(item_to_delete)
                    if not lore_data[str_id]:
                        del lore_data[str_id]
                    deletion_success = True

        if deletion_success:
            await self.cog._log_deletion(self.ctx, self.target_id, item_to_delete, interaction.user)

            self.entries.pop(self.index)
            self.total = len(self.entries)

            if self.total == 0:
                await interaction.response.edit_message(content="No lore entries remaining.", embed=None, view=None)
                self.stop()
            else:
                if self.index >= self.total:
                    self.index = self.total - 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.get_embed(), view=self)
            
            await interaction.followup.send(f"Entry deleted by {self.ctx.author.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message("Could not find that entry to delete.", ephemeral=True)

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
        self.user_ids = [uid for uid, entries in lore_data.items() if entries]
        self.selected_user_id = None
        self.page = 0
        self.per_page = 10
        self.options_per_select = 25
        self.user_list_page = 0
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
        
        start = self.page * self.per_page
        end = start + self.per_page
        current_entries = entries[start:end]
        
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
            
        total_user_pages = math.ceil(len(self.user_ids) / self.options_per_select)
        if total_user_pages > 1:
            prev_users = discord.ui.Button(label="<< Users", row=1, disabled=(self.user_list_page == 0), style=discord.ButtonStyle.secondary)
            prev_users.callback = self.on_prev_users
            self.add_item(prev_users)
            
            next_users = discord.ui.Button(label="Users >>", row=1, disabled=(self.user_list_page >= total_user_pages - 1), style=discord.ButtonStyle.secondary)
            next_users.callback = self.on_next_users
            self.add_item(next_users)

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
        self.page = 0
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
        
    async def on_prev_users(self, interaction: discord.Interaction):
        self.user_list_page -= 1
        self.selected_user_id = None
        self.update_ui()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_next_users(self, interaction: discord.Interaction):
        self.user_list_page += 1
        self.selected_user_id = None
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
        self.config.register_guild(lore={}, log_channel=None)

    async def _log_creation(self, ctx, user, message, jump_url):
        log_channel_id = await self.config.guild(ctx.guild).log_channel()
        if not log_channel_id:
            return
            
        log_channel = ctx.guild.get_channel(log_channel_id)
        if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
            embed = discord.Embed(
                title="New Lore Created", 
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Target", value=f"{user} (`{user.id}`)", inline=True)
            embed.add_field(name="Author", value=f"{ctx.author} (`{ctx.author.id}`)", inline=True)
            embed.add_field(name="Lore", value=message, inline=False)
            embed.add_field(name="Link", value=f"[Jump]({jump_url})", inline=False)
            
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException as e:
                log.error(f"Failed to send lore log in {log_channel.name}: {e}")

    async def _log_deletion(self, ctx, target_id, item_to_delete, deleter):
        log_channel_id = await self.config.guild(ctx.guild).log_channel()
        if not log_channel_id:
            return

        log_channel = ctx.guild.get_channel(log_channel_id)
        if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
            target_member = ctx.guild.get_member(target_id)
            target_text = f"{target_member} (`{target_id}`)" if target_member else f"User ID `{target_id}`"
            
            orig_author_id = item_to_delete.get("author", "Unknown")
            orig_content = item_to_delete.get("content", "No content.")
            
            embed = discord.Embed(
                title="Lore Deleted", 
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Target", value=target_text, inline=True)
            embed.add_field(name="Deleted By", value=f"{deleter} (`{deleter.id}`)", inline=True)
            embed.add_field(name="Original Author", value=f"<@{orig_author_id}>", inline=True)
            embed.add_field(name="Original Content", value=orig_content, inline=False)

            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException as e:
                log.error(f"Failed to send lore deletion log: {e}")

    @commands.command()
    @commands.guild_only()
    async def newlore(self, ctx, user: discord.Member, *, message: str):
        """Create a new lore entry for a user."""
        # 1. Store data
        entry = {
            "user": user.id,
            "author": ctx.author.id,
            "content": message,
            "date": time.time(),
            "type": "RegularNote",
            "link": ctx.message.jump_url
        }

        async with self.config.guild(ctx.guild).lore() as lore_data:
            str_id = str(user.id)
            if str_id not in lore_data:
                lore_data[str_id] = []
            lore_data[str_id].append(entry)

        # 2. Send Feedback Embed (Requested Format)
        embed = discord.Embed(title="Lore Added", color=discord.Color.green())
        embed.description = f"Lore successfully added for {user.mention}."
        
        embed.add_field(name="Target User", value=f"{user.mention}\n(`{user.id}`)", inline=True)
        embed.add_field(name="Added By", value=f"{ctx.author.mention}\n(`{ctx.author.id}`)", inline=True)
        embed.add_field(name="Lore Content", value=message, inline=False)
        embed.set_footer(text="Use [p]seelore to view all entries.")
        
        await ctx.send(embed=embed)

        # 3. Log to admin channel
        await self._log_creation(ctx, user, message, ctx.message.jump_url)

    @commands.command()
    @commands.guild_only()
    async def seelore(self, ctx, user: Union[discord.Member, discord.User, int] = None):
        """
        View lore for a user. Defaults to yourself.
        """
        if user is None:
            user = ctx.author

        if isinstance(user, int):
            user_id = user
            user_obj = None
            is_member = False
            try:
                user_obj = await self.bot.fetch_user(user_id)
            except:
                pass
        else:
            user_obj = user
            user_id = user.id
            is_member = ctx.guild.get_member(user_id) is not None

        # Check permissions regarding left users
        is_mod = await is_mod_or_superior(self.bot, ctx.author) or await self.bot.is_owner(ctx.author)
        
        if not is_member and not is_mod and user_id != ctx.author.id:
             return await ctx.send("That user is no longer in this server.")

        lore_data = await self.config.guild(ctx.guild).lore()
        user_lore = lore_data.get(str(user_id), [])

        if not user_lore:
            return await ctx.send(f"No lore found for {user_obj.display_name if user_obj else user_id}.")

        user_name = user_obj.display_name if user_obj else None
        view = LoreView(ctx, user_lore, user_id, self, user_name=user_name)
        await view.check_perms()
        view.update_buttons()
        
        await ctx.send(embed=view.get_embed(), view=view)

    @commands.group()
    @checks.admin_or_permissions(administrator=True)
    async def serverloreset(self, ctx):
        """Manage ServerLore settings."""
        pass

    @serverloreset.command(name="view")
    async def settings_view(self, ctx):
        """View current configurations."""
        config = await self.config.get_guild()
        lore_data = config.get("lore", {})
        log_channel_id = config.get("log_channel")
        
        log_channel_name = "Not Set"
        if log_channel_id:
            chan = ctx.guild.get_channel(log_channel_id)
            log_channel_name = f"#{chan.name}" if chan else f"Invalid ID ({log_channel_id})"
            
        total_users = len(lore_data.keys())
        total_entries = sum(len(entries) for entries in lore_data.values())

        table = (
            f"Log Channel:   {log_channel_name}\n"
            f"Users Tracked: {total_users}\n"
            f"Total Entries: {total_entries}"
        )
        await ctx.send(box(table, lang="yaml"))

    @serverloreset.command(name="logchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
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

    @serverloreset.command(name="list")
    async def list_all_lore(self, ctx):
        """Display all known lore for the server using an interactive menu."""
        lore_data = await self.config.guild(ctx.guild).lore()
        if not lore_data:
            return await ctx.send("No lore exists in this server.")

        view = AllLoreView(ctx, lore_data)
        await ctx.send(embed=view.get_embed(), view=view)

    @serverloreset.command(name="export")
    async def export_lore(self, ctx):
        """Export all lore to a JSON file."""
        lore_data = await self.config.guild(ctx.guild).lore()
        
        if not lore_data:
            return await ctx.send("There is no lore to export.")

        json_str = json.dumps(lore_data, indent=4)
        to_file = io.BytesIO(json_str.encode())
        
        await ctx.send(
            "Here is the exported lore data:",
            file=discord.File(to_file, filename="serverlore_export.json")
        )

    @serverloreset.command(name="import")
    async def import_lore(self, ctx):
        """Import lore from a JSON file attached to the message."""
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

        if not isinstance(data, dict):
             return await ctx.send("JSON root must be a dictionary.")
        
        async with self.config.guild(ctx.guild).lore() as lore_data:
            count = 0
            for uid, entries in data.items():
                if not isinstance(entries, list):
                    continue
                
                str_id = str(uid)
                if str_id not in lore_data:
                    lore_data[str_id] = []
                
                lore_data[str_id].extend(entries)
                count += len(entries)

        await ctx.send(f"✅ Successfully imported {count} lore entries.")

    @serverloreset.command(name="delete")
    async def delete_user_lore(self, ctx, user_id: int):
        """Delete all lore for a specific User ID."""
        async with self.config.guild(ctx.guild).lore() as lore_data:
            str_id = str(user_id)
            if str_id in lore_data:
                del lore_data[str_id]
                await ctx.send(f"🗑️ All lore for User ID `{user_id}` has been deleted.")
            else:
                await ctx.send(f"No lore found for User ID `{user_id}`.")

    @serverloreset.command(name="reset")
    async def reset_all_data(self, ctx):
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