import discord
import pytz
from typing import Optional
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import box, pagify

class TimezoneView(discord.ui.View):
    """
    A 3-step ephemeral View:
    1. Select Continent
    2. Select Country (Split into multiple dropdowns if > 25)
    3. Select Timezone (City)
    """
    def __init__(self, cog, user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.selected_continent = None
        self.selected_country_code = None

        # Build Continent List
        # We filter for continents that actually have countries in pytz
        self.continents = sorted(list(set(
            tz.split('/')[0] for tz in pytz.common_timezones if '/' in tz
        )))
        
        # Step 1: Continent Select
        self.add_item(ContinentSelect(self.continents))

    async def show_countries(self, interaction: discord.Interaction, continent: str):
        self.selected_continent = continent
        
        # 1. Identify countries in this continent
        # pytz.country_timezones is { 'US': ['America/New_York', ...], ... }
        # pytz.country_names is { 'US': 'United States', ... }
        
        relevant_countries = []
        for code, timezones in pytz.country_timezones.items():
            # Check if any timezone for this country belongs to the selected continent
            if any(tz.startswith(f"{continent}/") for tz in timezones):
                name = pytz.country_names.get(code, code)
                relevant_countries.append((name, code))
        
        relevant_countries.sort(key=lambda x: x[0]) # Sort by Name

        # Clear previous items (Continent Select)
        self.clear_items()

        # 2. Create Country Dropdowns
        # Discord limits select menus to 25 options. 
        # If we have > 25 countries, we split them into multiple Select menus.
        
        chunk_size = 25
        chunks = [relevant_countries[i:i + chunk_size] for i in range(0, len(relevant_countries), chunk_size)]

        if not chunks:
             await interaction.response.edit_message(content=f"No countries found for {continent}. This is odd.", view=self)
             return

        for index, chunk in enumerate(chunks):
            # Label distinction: "Countries A-M", "Countries N-Z" if multiple
            start_letter = chunk[0][0][0].upper()
            end_letter = chunk[-1][0][0].upper()
            placeholder = f"Select Country ({start_letter}-{end_letter})" if len(chunks) > 1 else "Select Country"
            
            self.add_item(CountrySelect(chunk, placeholder))

        await interaction.response.edit_message(
            content=f"**{continent}** selected. Now choose your Country:", 
            view=self
        )

    async def show_timezones(self, interaction: discord.Interaction, country_code: str, country_name: str):
        self.selected_country_code = country_code
        
        # Get timezones for this country
        # We filter again to ensure we only show ones matching the selected continent
        # (Russia, for example, is in both Europe and Asia)
        all_timezones = pytz.country_timezones.get(country_code, [])
        filtered_timezones = [
            tz for tz in all_timezones 
            if tz.startswith(f"{self.selected_continent}/")
        ]
        
        # Fallback: if strict filtering removes everything (rare edge cases), show all for country
        if not filtered_timezones:
            filtered_timezones = all_timezones

        filtered_timezones.sort()
        
        # Slice to 25 just in case a single country has > 25 zones (rare, but possible)
        filtered_timezones = filtered_timezones[:25]

        self.clear_items()
        self.add_item(CitySelect(filtered_timezones))

        await interaction.response.edit_message(
            content=f"**{country_name}** selected. Finally, choose your local Timezone:", 
            view=self
        )

    async def finish(self, interaction: discord.Interaction, timezone: str):
        await self.cog.config.user_from_id(self.user_id).timezone.set(timezone)
        
        # Disable inputs
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(
            content=f"✅ Timezone set to: **{timezone}**", 
            view=self
        )
        self.stop()


class ContinentSelect(discord.ui.Select):
    def __init__(self, continents):
        options = [discord.SelectOption(label=c, value=c) for c in continents[:25]]
        super().__init__(placeholder="Step 1: Select Continent", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_countries(interaction, self.values[0])


class CountrySelect(discord.ui.Select):
    def __init__(self, countries, placeholder):
        # countries is a list of tuples: (Name, ISO_Code)
        options = [
            discord.SelectOption(label=name[:100], value=code) 
            for name, code in countries
        ]
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Find the name for the selected code for display purposes
        selected_code = self.values[0]
        selected_name = next((opt.label for opt in self.options if opt.value == selected_code), selected_code)
        await self.view.show_timezones(interaction, selected_code, selected_name)


class CitySelect(discord.ui.Select):
    def __init__(self, timezones):
        # timezones is a list of strings like "America/New_York"
        options = []
        for tz in timezones:
            # Clean up label: "America/New_York" -> "New York"
            city_label = tz.split('/', 1)[1].replace('_', ' ')
            options.append(discord.SelectOption(label=city_label, value=tz))
            
        super().__init__(placeholder="Step 3: Select Timezone", options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish(interaction, self.values[0])


class Timezone(commands.Cog):
    """
    Allow users to set their timezone via a 3-step interactive View (Continent -> Country -> City).
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981234712399, force_registration=True)
        default_user = {"timezone": None}
        self.config.register_user(**default_user)

    # --- Public API ---

    async def get_user_timezone(self, user_id: int) -> Optional[str]:
        return await self.config.user_from_id(user_id).timezone()

    # --- Commands ---

    @app_commands.command(name="mytimezone", description="Set your timezone.")
    async def mytimezone(self, interaction: discord.Interaction):
        """
        Launch the Timezone selector view.
        """
        view = TimezoneView(self, interaction.user.id)
        await interaction.response.send_message(
            "Let's configure your timezone. Select your continent:", 
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
        col1_w = max(len(r[0]) for r in data + [headers])
        col2_w = max(len(r[1]) for r in data + [headers])

        table_lines = [f"{headers[0].ljust(col1_w)} | {headers[1].ljust(col2_w)}"]
        table_lines.append(f"{'-'*col1_w}-+-{'-'*col2_w}")
        
        for row in data:
            table_lines.append(f"{row[0].ljust(col1_w)} | {row[1].ljust(col2_w)}")

        full_table = "\n".join(table_lines)

        for page in pagify(full_table):
            await ctx.send(box(page, lang="prolog"))