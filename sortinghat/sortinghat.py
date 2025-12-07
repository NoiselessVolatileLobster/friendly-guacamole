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
            description=f"Information for the {self.role.mention} house.",
            color=self.role.color
        )
        embed.add_field(name="Total Members", value=str(len(self.role.members)), inline=True)
        
        # List a few members as a preview
        members_preview = [m.display_name for m in self.role.members[:10]]
        if len(self.role.members) > 10:
            members_str = ", ".join(members_preview) + f" and {len(self.role.members) - 10} more..."
        elif members_preview:
            members_str = ", ".join(members_preview)
        else:
            members_str = "No members yet."
            
        embed.add_field(name="Roster Preview", value=members_str, inline=False)
        
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
        # Note: We can't edit the message here easily without holding a ref to it, 
        # but the interaction will just fail gracefully or we can pass the message in init.
        pass

class SortingHat(commands.Cog):
    """Sorts users into houses to keep teams balanced."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98123749812, force_registration=True)
        default_guild = {
            "house_role_ids": []
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
    async def on_member_join(self, member: discord.Member):
        """Automatically sorts new members."""
        if member.bot:
            return
            
        guild = member.guild
        houses = await self._get_house_roles(guild)
        
        if not houses:
            return

        await self._sort_member(member, houses)

    @commands.group()
    @commands.guild_only()
    async def sortinghatset(self, ctx):
        """Configuration for the Sorting Hat."""
        pass

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
        This respects the 'balanced' logic (filling smallest houses first).
        """
        houses = await self._get_house_roles(ctx.guild)
        if not houses:
            await ctx.send("No house roles are configured. Please add some first.")
            return

        await ctx.send("Sorting unsorted members... this may take a moment.")
        
        sorted_count = 0
        async with ctx.typing():
            for member in ctx.guild.members:
                if member.bot:
                    continue
                result = await self._sort_member(member, houses)
                if result:
                    sorted_count += 1

        await ctx.send(f"Sorting complete. Assigned house roles to {sorted_count} members.")

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