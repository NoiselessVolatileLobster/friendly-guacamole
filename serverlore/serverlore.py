import discord
import json
import time
import io
import logging
from typing import Union, List, Dict, Optional

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import pagify, box

log = logging.getLogger("red.serverlore")

class LoreView(discord.ui.View):
    def __init__(self, ctx, entries: List[Dict], target_id: int, cog: "ServerLore"):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.entries = entries
        self.target_id = target_id
        self.cog = cog
        self.index = 0
        self.total = len(entries)
        
        # Determine if user is a mod/admin to show the delete button
        self.is_mod = False

    async def check_perms(self):
        # We check perms asynchronously before sending
        is_owner = await self.ctx.bot.is_owner(self.ctx.author)
        is_admin = await self.ctx.bot.permissions.is_admin(self.ctx.author)
        is_mod = await self.ctx.bot.permissions.is_mod(self.ctx.author)
        self.is_mod = is_owner or is_admin or is_mod
        
        # Remove delete button if not mod
        if not self.is_mod:
            # The delete button is the middle one (index 2)
            # Previous, Counter, Delete, Next
            # We filter children to remove the one with custom_id 'delete_btn'
            self.remove_item(self.delete_entry)

    def get_embed(self) -> discord.Embed:
        entry = self.entries[self.index]
        
        # Prepare data
        author_id = entry.get("author")
        content = entry.get("content", "No content.")
        timestamp = entry.get("date", 0)
        link = entry.get("link", None)
        
        embed = discord.Embed(title=f"Lore for User ID: {self.target_id}", color=discord.Color.blue())
        embed.description = content
        
        embed.add_field(name="Author", value=f"<@{author_id}> ({author_id})")
        embed.add_field(name="Date", value=f"<t:{int(timestamp)}:F>")
        
        if link:
            embed.add_field(name="Link", value=f"[Jump to Message]({link})", inline=False)
            
        embed.set_footer(text=f"Entry {self.index + 1}/{self.total}")
        return embed

    def update_buttons(self):
        self.previous_page.disabled = self.index == 0
        self.next_page.disabled = self.index == self.total - 1
        
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

        # Logic to remove from config
        async with self.cog.config.guild(self.ctx.guild).lore() as lore_data:
            str_id = str(self.target_id)
            if str_id in lore_data:
                # We fetch the specific item currently being viewed to ensure we delete the right one
                # incase the list shifted (rare race condition, but safe approach)
                item_to_delete = self.entries[self.index]
                if item_to_delete in lore_data[str_id]:
                    lore_data[str_id].remove(item_to_delete)
                    log.info(f"Lore deleted by {self.ctx.author} for user {self.target_id}: {item_to_delete}")
                    await interaction.followup.send(f"Entry deleted by {self.ctx.author.mention}.", ephemeral=True)
                
                # Update local list
                self.entries.pop(self.index)
                self.total = len(self.entries)

        if self.total == 0:
            await interaction.response.edit_message(content="No lore entries remaining.", embed=None, view=None)
            self.stop()
        else:
            # Adjust index if we deleted the last item
            if self.index >= self.total:
                self.index = self.total - 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self.update_buttons()
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
        self.config.register_guild(lore={})

    @commands.command()
    @commands.guild_only()
    async def newlore(self, ctx, user: discord.Member, *, message: str):
        """Create a new lore entry for a user."""
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

        await ctx.send(f"✅ New lore added for **{user.display_name}**.")

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
        is_mod = await self.bot.permissions.is_mod(ctx.author) or await self.bot.is_owner(ctx.author)
        
        if not is_member and not is_mod:
            return await ctx.send("That user is no longer in this server.")

        lore_data = await self.config.guild(ctx.guild).lore()
        user_lore = lore_data.get(str(user_id), [])

        if not user_lore:
            return await ctx.send(f"No lore found for ID {user_id}.")

        view = LoreView(ctx, user_lore, user_id, self)
        await view.check_perms()
        view.update_buttons()
        
        await ctx.send(embed=view.get_embed(), view=view)

    # --- Administrator Commands ---

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def seealllore(self, ctx):
        """Display all known lore for the server."""
        lore_data = await self.config.guild(ctx.guild).lore()
        if not lore_data:
            return await ctx.send("No lore exists in this server.")

        output = ""
        count = 0
        for uid, entries in lore_data.items():
            count += len(entries)
            output += f"**User ID: {uid}** ({len(entries)} entries)\n"

        if not output:
             return await ctx.send("No lore entries found.")
             
        header = f"Total Lore Entries: {count}\n\n"
        pages = list(pagify(header + output))
        
        for page in pages:
            await ctx.send(page)

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
            # We merge imported data with existing data, or overwrite? 
            # Request implies just "import", usually implies merging or overwriting. 
            # I will perform a merge (append entries) to be safe, unless key exists.
            
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