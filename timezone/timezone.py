import discord
import pytz
from typing import Optional
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import box, pagify

class TimezoneView(discord.ui.View):
    """
    An ephemeral View that mimics a multi-step form:
    1. Select Continent
    2. Select City
    """
    def __init__(self, cog, user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.selected_continent = None
        
        # 1. Prepare Continent Data
        # We group common timezones by their primary region (e.g., 'America', 'Europe')
        self.tz_map = {}
        for tz in pytz.common_timezones:
            if "/" in tz:
                continent, city = tz.split("/", 1)
                if continent not in self.tz_map:
                    self.tz_map[continent] = []
                self.tz_map[continent].append(city)
        
        # 2. Add Continent Select Menu
        self.continent_select = discord.ui.Select(
            placeholder="Step 1: Select your Continent",
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

        # Placeholder for City Select (added dynamically later)
        self.city_select = None

    async def on_continent_select(self, interaction: discord.Interaction):
        self.selected_continent = self.continent_select.values[0]
        
        # Filter cities for the selected continent
        cities = sorted(self.tz_map[self.selected_continent])
        
        # SAFETY: Discord allows max 25 options. 
        # If a continent has >25 cities, we slice the list. 
        # (A production bot might need a "Next Page" logic here, but this prevents crashes)
        cities = cities[:25]
        
        options = [
            discord.SelectOption(
                label=city.replace("_", " "), 
                value=f"{self.selected_continent}/{city}"
            ) 
            for city in cities
        ]

        # Remove the old city select if the user changed their mind and re-picked continent
        if self.city_select in self.children:
            self.remove_item(self.city_select)

        # Create the City Select Menu
        self.city_select = discord.ui.Select(
            placeholder=f"Step 2: Select City in {self.selected_continent}",
            options=options,
            min_values=1,
            max_values=1,
            row=1
        )
        self.city_select.callback = self.on_city_select
        self.add_item(self.city_select)
        
        # Update the view with the new dropdown
        await interaction.response.edit_message(
            content=f"Continent **{self.selected_continent}** selected. Now choose your city:", 
            view=self
        )

    async def on_city_select(self, interaction: discord.Interaction):
        chosen_tz = self.city_select.values[0]
        
        # Save to Config
        await self.cog.config.user_from_id(self.user_id).timezone.set(chosen_tz)
        
        # Disable all inputs to show it is "locked in"
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(
            content=f"✅ Timezone successfully set to: **{chosen_tz}**", 
            view=self
        )
        self.stop()

class Timezone(commands.Cog):
    """
    Allow users to set their timezone via an interactive View.
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
        # We send this as ephemeral=True so it acts like a private "modal" popup
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

        headers = ["User", "Timezone"]
        # Basic column width calculation
        col1_w = max(len(r[0]) for r in data + [headers])
        col2_w = max(len(r[1]) for r in data + [headers])

        table_lines = [f"{headers[0].ljust(col1_w)} | {headers[1].ljust(col2_w)}"]
        table_lines.append(f"{'-'*col1_w}-+-{'-'*col2_w}")
        
        for row in data:
            table_lines.append(f"{row[0].ljust(col1_w)} | {row[1].ljust(col2_w)}")

        full_table = "\n".join(table_lines)

        for page in pagify(full_table):
            await ctx.send(box(page, lang="prolog"))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # Listener to ensure interaction contexts are processed if needed
        pass