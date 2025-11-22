import discord
from redbot.core import Config, commands
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from typing import Dict, Optional, List, Tuple, Any

# Identifier used for the Config instance to ensure unique storage
# This should be a unique number for the cog.
IDENTIFIER = 825480081037

class WhereAreWe(commands.Cog):
    """
    Posts an embed showing the member count for a configured list of roles.
    Admins can add and remove roles to track.
    """

    def __init__(self, bot):
        self.bot = bot
        # Initialize Config: scoped to guild level, storing a dictionary of role IDs (str) to emoji strings.
        self.config = Config.get_conf(self, identifier=IDENTIFIER, force_registration=True)
        self.config.register_guild(
            tracked_roles={} # Changed to a dictionary to store role ID and emoji pairing
        )

    # --- Public API Method ---
    async def get_tracked_role_member_counts(self, guild: discord.Guild) -> List[Dict[str, Any]]:
        """
        Public API method to retrieve member counts for all tracked roles in a guild.

        Returns a list of dictionaries, sorted by member count (descending).
        Each dictionary contains: 'role_name', 'member_count', and 'emoji'.

        Example Output Structure:
        [
            {'role_name': 'North America', 'member_count': 420, 'emoji': '🇺🇸'},
            {'role_name': 'Europe', 'member_count': 300, 'emoji': '🇪🇺'},
            ...
        ]
        """
        tracked_data: Dict[str, str] = await self.config.guild(guild).tracked_roles()

        role_data_unsorted: List[Tuple[str, str, int]] = []
        
        for role_id_str, emoji in tracked_data.items():
            role_id = int(role_id_str)
            role: discord.Role = guild.get_role(role_id)
            
            if role:
                member_count = len(role.members)
                role_data_unsorted.append((role.name, emoji, member_count))
            else:
                # Still include deleted roles in the list for completeness, but with 0 count
                role_data_unsorted.append((f"Deleted Role (ID: {role_id})", "❌", 0))

        # Sort data: sort by member count (index 2) in descending order (reverse=True)
        role_data_sorted = sorted(role_data_unsorted, key=lambda item: item[2], reverse=True)

        # Convert the sorted tuple list into the final dictionary format
        result = []
        for role_name, emoji, count in role_data_sorted:
            # We filter out deleted roles and zero-member roles here for clean API output,
            # as is done in the main command.
            if count > 0 and emoji != "❌":
                 result.append({
                    "role_name": role_name,
                    "member_count": count,
                    "emoji": emoji
                })
        
        return result

    # --- Command Logic ---
    @commands.guild_only()
    @commands.command(name="wherearewe")
    async def wherearewe_command(self, ctx: commands.Context):
        """
        Displays an embed with the member count for all tracked roles in this server,
        sorted by member count (highest first).
        """
        # Use the new API method to retrieve and process the data
        role_data_list = await self.get_tracked_role_member_counts(ctx.guild)
        
        if not role_data_list:
            # Check for no roles configured (config empty)
            tracked_data: Dict[str, str] = await self.config.guild(ctx.guild).tracked_roles()
            if not tracked_data:
                 return await ctx.send(
                    "No roles are currently configured for tracking. "
                    f"An administrator must use `{ctx.prefix}wherearesettings add <role> [emoji]` first."
                )
            # Check for roles configured but all have 0 members
            else:
                return await ctx.send("All tracked roles currently have 0 members.")


        # 3. Build the Embed
        embed = discord.Embed(
            title="🌎 Where are we?",
            description="Number of members per continent:",
            color=0xB4C6FF # The integer representation of #B4C6FF
        )
        
        # 4. Build a single string for the list content using the new format
        content_lines = []
        for data in role_data_list:
            role_name = data['role_name']
            emoji = data['emoji']
            count = data['member_count']
            
            # Requested Format: "{emoji} **Role Name**: #"
            line = f"{emoji} **{role_name}**: {count}"
            content_lines.append(line)
        
        # Add the entire list as the value of a single, non-inline field
        embed.add_field(
            name='\u200b', 
            value='\n'.join(content_lines),
            inline=False
        )

        await ctx.send(embed=embed)


    @commands.guild_only()
    @commands.group(name="wherearesettings")
    @commands.admin_or_permissions(manage_guild=True)
    async def wherearewe_settings(self, ctx: commands.Context):
        """Manages the list of roles tracked by the wherearewe command."""
        pass

    @wherearewe_settings.command(name="list")
    async def settings_list(self, ctx: commands.Context):
        """Lists all roles currently being tracked, including their associated emoji."""
        guild: discord.Guild = ctx.guild
        # Retrieve the dictionary: {role_id_str: emoji_string}
        tracked_data: Dict[str, str] = await self.config.guild(guild).tracked_roles()

        if not tracked_data:
            return await ctx.send("No roles are currently being tracked.")

        roles_text = []
        
        for role_id_str, emoji in tracked_data.items():
            role_id = int(role_id_str)
            role: discord.Role = guild.get_role(role_id)
            
            emoji_display = emoji if emoji else ""
            
            if role:
                roles_text.append(f"{emoji_display} {role.mention} (`{role.id}`)")
            else:
                roles_text.append(f"❌ **Deleted Role** (`{role_id}`)")

        # Use pagination for potentially long lists
        pages = []
        for i in range(0, len(roles_text), 10):
            chunk = roles_text[i:i + 10]
            embed = discord.Embed(
                title="Current Tracked Roles",
                description="\n".join(chunk),
                color=0xB4C6FF
            )
            embed.set_footer(text=f"Total tracked roles: {len(tracked_data)}")
            pages.append(embed)

        if pages:
            await menu(ctx, pages, DEFAULT_CONTROLS)
        else:
            await ctx.send("No roles are currently being tracked.")

    @wherearewe_settings.command(name="add")
    async def settings_add(self, ctx: commands.Context, role: discord.Role, emoji: Optional[str] = None):
        """Adds a role to the list of roles to be tracked, optionally with an emoji."""
        guild: discord.Guild = ctx.guild
        # Retrieve the dictionary: {role_id_str: emoji_string}
        tracked_data: Dict[str, str] = await self.config.guild(guild).tracked_roles()
        role_id_str = str(role.id)
        
        # Use a default emoji if none is provided
        emoji_to_store = emoji if emoji else "⚪"

        if role_id_str in tracked_data:
            if tracked_data[role_id_str] == emoji_to_store:
                 return await ctx.send(f"**{role.name}** is already tracked with the emoji {emoji_to_store}.")
            else:
                 # Update the emoji if the role is already tracked but the emoji is different
                 tracked_data[role_id_str] = emoji_to_store
                 await self.config.guild(guild).tracked_roles.set(tracked_data)
                 return await ctx.send(f"Updated the emoji for **{role.name}** to {emoji_to_store}.")

        # Add the new role/emoji pair
        tracked_data[role_id_str] = emoji_to_store
        await self.config.guild(guild).tracked_roles.set(tracked_data)
        await ctx.send(f"Successfully added **{role.name}** to the tracked list with emoji {emoji_to_store}.")

    @wherearewe_settings.command(name="remove")
    async def settings_remove(self, ctx: commands.Context, role: discord.Role):
        """Removes a role from the list of roles being tracked."""
        guild: discord.Guild = ctx.guild
        # Retrieve the dictionary: {role_id_str: emoji_string}
        tracked_data: Dict[str, str] = await self.config.guild(guild).tracked_roles()
        role_id_str = str(role.id)

        if role_id_str not in tracked_data:
            return await ctx.send(f"{role.name} is not currently being tracked.")

        del tracked_data[role_id_str]
        await self.config.guild(guild).tracked_roles.set(tracked_data)
        await ctx.send(f"Successfully removed **{role.name}** from the tracked list.")