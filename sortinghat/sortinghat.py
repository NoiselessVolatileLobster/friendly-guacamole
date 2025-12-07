import discord
import random
import logging
from typing import List, Optional
from redbot.core import commands, Config, checks

class HouseButton(discord.ui.Button):
    def __init__(self, role: discord.Role):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=role.name,
            custom_id=f"sortinghat_house_{role.id}"
        )
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        # Create an embed with details about the selected house
        embed = discord.Embed(
            title=f"House: {self.role.name}",
            color=self.role.color
        )
        embed.add_field(name="Total Members", value=str(len(self.role.members)), inline=False)
        
        if not self.role.members:
            embed.add_field(name="House Members", value="No members yet.", inline=False)
        else:
            all_members = [m.mention for m in self.role.members]
            
            # Discord limits field values to 1024 characters.
            # We need to chunk the list to fit into multiple fields if necessary.
            chunks = []
            current_chunk = []
            current_len = 0
            
            for member in all_members:
                # Check if adding this member + ", " exceeds 1024
                # We use 1000 as a safety buffer
                if current_len + len(member) + 2 > 1000:
                    chunks.append(", ".join(current_chunk))
                    current_chunk = [member]
                    current_len = len(member)
                else:
                    current_chunk.append(member)
                    current_len += len(member) + 2
            
            if current_chunk:
                chunks.append(", ".join(current_chunk))
            
            # Add fields to the embed
            for i, chunk in enumerate(chunks):
                # Only title the first field "House Members", leave others blank for cleaner look
                # \u200b is a zero-width space
                field_name = "House Members" if i == 0 else "\u200b"
                
                # Check total embed size limit (6000 chars) to prevent crashing
                if len(embed) + len(chunk) > 5900:
                    embed.add_field(name="...", value="*List truncated due to Discord embed limits.*", inline=False)
                    break
                    
                embed.add_field(name=field_name, value=chunk, inline=False)
        
        await interaction.response.edit_message(embed=embed)

class HouseView(discord.ui.View):
    def __init__(self, houses: List[discord.Role]):
        super().__init__(timeout=60)
        self.houses = houses
        for house in houses:
            self.add_item(HouseButton(house))

    async def on_timeout(self):
        # Disable buttons when the view times out
        for child in self.children:
            child.disabled = True
        pass

class SortingHat(commands.Cog):
    """Sorts users into houses to keep teams balanced."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98123749812, force_registration=True)
        default_guild = {
            "house_role_ids": [],
            "required_level": 0
        }
        self.config.register_guild(**default_guild)
        self.log = logging.getLogger("red.sortinghat")

    async def _get_house_roles(self, guild: discord.Guild) -> List[discord.Role]:
        """Fetches the actual role objects from config."""
        role_ids = await self.config.guild(guild).house_role_ids()
        roles = []
        for r_id in role_ids:
            role = guild.get_role(r_id)
            if role:
                roles.append(role)
        return roles

    async def _sort_member(self, member: discord.Member, houses: List[discord.Role]) -> Optional[discord.Role]:
        """
        Sorts a single member into a house.
        Returns the Role they were sorted into, or None if no action was taken.
        """
        if not houses:
            return None

        # 1. Check if user is already in a house
        for house in houses:
            if house in member.roles:
                return None # User is already sorted, do not move them.

        # 2. Calculate house populations
        # We fetch the length of members from the role object
        house_counts = {role: len(role.members) for role in houses}

        # 3. Find the minimum count
        min_count = min(house_counts.values())

        # 4. Filter houses that have the minimum count
        smallest_houses = [role for role, count in house_counts.items() if count == min_count]

        # 5. Pick a random house from the smallest ones (to handle ties randomly)
        chosen_house = random.choice(smallest_houses)

        # 6. Assign the role
        try:
            await member.add_roles(chosen_house, reason="Sorting Hat: Balancing houses")
            return chosen_house
        except discord.Forbidden:
            self.log.warning(f"Could not assign role {chosen_house.name} to {member.name} in {member.guild.name}. Check hierarchy.")
            return None
        except Exception as e:
            self.log.error(f"Error sorting member {member.name}: {e}")
            return None

    @commands.Cog.listener()
    async def on_levelup(self, guild: discord.Guild, member: discord.Member, level: int):
        """
        Listens for the 'levelup' event dispatched by Vertyco's LevelUp cog.
        Sorts the user if they reach the required level.
        """
        if member.bot:
            return
            
        required_level = await self.config.guild(guild).required_level()
        
        # If no level is configured (0), we assume this feature is disabled
        if not required_level:
            return

        if level >= required_level:
            houses = await self._get_house_roles(guild)
            if not houses:
                return
            
            # _sort_member handles the check if they are already in a house
            await self._sort_member(member, houses)

    @commands.group()
    @commands.guild_only()
    async def sortinghatset(self, ctx):
        """Configuration for the Sorting Hat."""
        pass

    @sortinghatset.command(name="level")
    @checks.admin_or_permissions(manage_roles=True)
    async def sh_level(self, ctx, level: int):
        """
        Set the LevelUp level required to be sorted into a house.
        Set to 0 to disable auto-sorting.
        """
        if level < 0:
            await ctx.send("Level cannot be negative.")
            return
            
        await self.config.guild(ctx.guild).required_level.set(level)
        if level == 0:
            await ctx.send("Auto-sorting by level has been disabled.")
        else:
            await ctx.send(f"Users will now be sorted when they reach level {level}.")

    @sortinghatset.group(name="houserole")
    @checks.admin_or_permissions(manage_roles=True)
    async def sh_houserole(self, ctx):
        """Add or remove roles to be used as Houses."""
        pass

    @sh_houserole.command(name="add")
    async def sh_add(self, ctx, role: discord.Role):
        """Add a role to the house list."""
        async with self.config.guild(ctx.guild).house_role_ids() as role_ids:
            if role.id in role_ids:
                await ctx.send(f"{role.name} is already a house.")
                return
            role_ids.append(role.id)
        await ctx.send(f"Added {role.name} to the Sorting Hat houses.")

    @sh_houserole.command(name="remove")
    async def sh_remove(self, ctx, role: discord.Role):
        """Remove a role from the house list."""
        async with self.config.guild(ctx.guild).house_role_ids() as role_ids:
            if role.id not in role_ids:
                await ctx.send(f"{role.name} is not a configured house.")
                return
            role_ids.remove(role.id)
        await ctx.send(f"Removed {role.name} from the Sorting Hat houses.")

    @sortinghatset.command(name="sortunsorted")
    @checks.admin_or_permissions(manage_roles=True)
    async def sh_sortunsorted(self, ctx):
        """
        Checks all members and sorts those who don't have a house yet.
        
        If a 'level' is configured via `[p]sortinghatset level`, this command
        will respect it and only sort users who meet that level requirement.
        """
        houses = await self._get_house_roles(ctx.guild)
        if not houses:
            await ctx.send("No house roles are configured. Please add some first.")
            return

        required_level = await self.config.guild(ctx.guild).required_level()
        levelup_cog = self.bot.get_cog("LevelUp")

        # If a level is required, we MUST have the LevelUp cog loaded.
        if required_level > 0 and not levelup_cog:
            await ctx.send(
                "A required level is set, but the 'LevelUp' cog is not loaded. "
                "I cannot verify user levels, so I will not sort anyone. "
                "Please load LevelUp or set the required level to 0."
            )
            return

        await ctx.send("Sorting unsorted members... this may take a moment.")
        
        sorted_count = 0
        skipped_count = 0
        
        async with ctx.typing():
            for member in ctx.guild.members:
                if member.bot:
                    continue
                
                # Check level requirement if active
                if required_level > 0:
                    try:
                        # Standard Vertyco LevelUp API pattern
                        user_data = await levelup_cog.db.get_member_data(ctx.guild.id, member.id)
                        if user_data.level < required_level:
                            skipped_count += 1
                            continue
                    except Exception as e:
                        # Fail safe: if we can't get data, don't sort them
                        self.log.error(f"Failed to fetch level data for {member.id}: {e}")
                        continue

                result = await self._sort_member(member, houses)
                if result:
                    sorted_count += 1

        msg = f"Sorting complete. Assigned house roles to {sorted_count} members."
        if required_level > 0:
            msg += f" (Skipped {skipped_count} members who were below level {required_level})"
            
        await ctx.send(msg)

    @commands.command()
    @commands.guild_only()
    async def houses(self, ctx):
        """View the houses and their details."""
        houses = await self._get_house_roles(ctx.guild)
        if not houses:
            await ctx.send("There are no houses established yet.")
            return

        embed = discord.Embed(
            title="The Houses",
            description="Click a button below to see details about a specific house.",
            color=await ctx.embed_color()
        )
        
        # Calculate total population for the main embed
        total_sorted = sum([len(h.members) for h in houses])
        embed.add_field(name="Houses", value=str(len(houses)), inline=True)
        embed.add_field(name="Sorted Members", value=str(total_sorted), inline=True)

        view = HouseView(houses)
        message = await ctx.send(embed=embed, view=view)