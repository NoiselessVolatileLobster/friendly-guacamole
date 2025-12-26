import discord
import pytz
from typing import Optional
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

class TimezoneView(discord.ui.View):
    """
    A view that allows users to select a Continent, then a City.
    """
    def __init__(self, cog, user_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.selected_continent = None
        
        # Parse common timezones into {Continent: [City, City...]}
        self.tz_map = {}
        for tz in pytz.common_timezones:
            if "/" in tz:
                continent, city = tz.split("/", 1)
                if continent not in self.tz_map:
                    self.tz_map[continent] = []
                self.tz_map[continent].append(city)
        
        # Setup Continent Select
        self.continent_select = discord.ui.Select(
            placeholder="Select your Continent...",
            options=[
                discord.SelectOption(label=c, value=c) 
                for c in sorted(self.tz_map.keys())
            ],
            min_values=1,
            max_values=1,
            row=0
        )
        self.continent_select.callback = self.on_continent_select
        self.add_item(self.continent_select)

        # Placeholder for City Select (added later)
        self.city_select = None

    async def on_continent_select(self, interaction: discord.Interaction):
        self.selected_continent = self.continent_select.values[0]
        
        # Create City options (limit to 25 due to Discord API limits)
        # In a production env with >25 cities per continent, you might need subdivision
        cities = sorted(self.tz_map[self.selected_continent])[:25]
        
        options = [
            discord.SelectOption(label=city, value=f"{self.selected_continent}/{city}") 
            for city in cities
        ]

        # Remove old city select if exists
        if self.city_select:
            self.remove_item(self.city_select)

        self.city_select = discord.ui.Select(
            placeholder=f"Select City in {self.selected_continent}...",
            options=options,
            min_values=1,
            max_values=1,
            row=1
        )
        self.city_select.callback = self.on_city_select
        self.add_item(self.city_select)
        
        await interaction.response.edit_message(content="Now select your city:", view=self)

    async def on_city_select(self, interaction: discord.Interaction):
        chosen_tz = self.city_select.values[0]
        await self.cog.config.user_from_id(self.user_id).timezone.set(chosen_tz)
        
        # Disable view after selection
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(
            content=f"✅ Timezone set to: **{chosen_tz}**", 
            view=self
        )
        self.stop()

class Timezone(commands.Cog):
    """
    Allow users to set their timezone via UI and expose it to other cogs.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981234712399, force_registration=True)
        default_user = {"timezone": None}
        self.config.register_user(**default_user)

    # --- Public API ---

    async def get_user_timezone(self, user_id: int) -> Optional[str]:
        """
        Public API method to get a user's timezone string (e.g., 'America/New_York').
        Returns None if not set.
        """
        return await self.config.user_from_id(user_id).timezone()

    # --- Commands ---

    @app_commands.command(name="mytimezone", description="Set your timezone using a dropdown menu.")
    async def mytimezone(self, interaction: discord.Interaction):
        """
        Launch the Timezone selector view.
        """
        view = TimezoneView(self, interaction.user.id)
        await interaction.response.send_message(
            "Please select your continent to begin:", 
            view=view, 
            ephemeral=True
        )

    @commands.group(name="timezoneset")
    @commands.admin_or_permissions(administrator=True)
    async def timezoneset(self, ctx):
        """
        Administrator settings for Timezone.
        """
        pass

    @timezoneset.command(name="view")
    async def timezoneset_view(self, ctx):
        """
        View all users who have configured their timezone.
        """
        all_users = await self.config.all_users()
        
        if not all_users:
            await ctx.send("No timezones have been captured yet.")
            return

        # Formatting data for the table
        data = []
        for user_id, data_dict in all_users.items():
            tz = data_dict.get("timezone")
            if tz:
                user = ctx.guild.get_member(user_id)
                username = user.display_name if user else f"Unknown ({user_id})"
                data.append([username, tz])

        if not data:
             await ctx.send("No timezones set.")
             return

        # Simple table construction
        headers = ["User", "Timezone"]
        # Calculate column widths
        col1_w = max(len(r[0]) for r in data + [headers])
        col2_w = max(len(r[1]) for r in data + [headers])

        table_lines = [f"{headers[0].ljust(col1_w)} | {headers[1].ljust(col2_w)}"]
        table_lines.append(f"{'-'*col1_w}-+-{'-'*col2_w}")
        
        for row in data:
            table_lines.append(f"{row[0].ljust(col1_w)} | {row[1].ljust(col2_w)}")

        full_table = "\n".join(table_lines)

        # Use pagify to handle long lists safely
        for page in pagify(full_table):
            await ctx.send(box(page, lang="prolog"))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # This listener ensures hybrid commands/app commands are processed if not using tree sync
        # Red usually handles this, but good to ensure context availability.
        pass