import discord
import pytz
import datetime
from dateutil import parser
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
        self.continents = sorted(list(set(
            tz.split('/')[0] for tz in pytz.common_timezones if '/' in tz
        )))
        
        self.add_item(ContinentSelect(self.continents))

    async def show_countries(self, interaction: discord.Interaction, continent: str):
        self.selected_continent = continent
        
        relevant_countries = []
        for code, timezones in pytz.country_timezones.items():
            if any(tz.startswith(f"{continent}/") for tz in timezones):
                name = pytz.country_names.get(code, code)
                relevant_countries.append((name, code))
        
        relevant_countries.sort(key=lambda x: x[0])

        self.clear_items()

        chunk_size = 25
        chunks = [relevant_countries[i:i + chunk_size] for i in range(0, len(relevant_countries), chunk_size)]

        if not chunks:
             await interaction.response.edit_message(content=f"No countries found for {continent}.", view=self)
             return

        for index, chunk in enumerate(chunks):
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
        
        all_timezones = pytz.country_timezones.get(country_code, [])
        filtered_timezones = [
            tz for tz in all_timezones 
            if tz.startswith(f"{self.selected_continent}/")
        ]
        
        if not filtered_timezones:
            filtered_timezones = all_timezones

        filtered_timezones.sort()
        filtered_timezones = filtered_timezones[:25]

        self.clear_items()
        self.add_item(CitySelect(filtered_timezones))

        await interaction.response.edit_message(
            content=f"**{country_name}** selected. Finally, choose your local Timezone:", 
            view=self
        )

    async def finish(self, interaction: discord.Interaction, timezone: str):
        await self.cog.config.user_from_id(self.user_id).timezone.set(timezone)
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
        options = [
            discord.SelectOption(label=name[:100], value=code) 
            for name, code in countries
        ]
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_code = self.values[0]
        selected_name = next((opt.label for opt in self.options if opt.value == selected_code), selected_code)
        await self.view.show_timezones(interaction, selected_code, selected_name)


class CitySelect(discord.ui.Select):
    def __init__(self, timezones):
        options = []
        for tz in timezones:
            city_label = tz.split('/', 1)[1].replace('_', ' ')
            options.append(discord.SelectOption(label=city_label, value=tz))
        super().__init__(placeholder="Step 3: Select Timezone", options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish(interaction, self.values[0])


class Timezone(commands.Cog):
    """
    Allow users to set their timezone via a 3-step interactive View and generate timestamps.
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
        """Launch the Timezone selector view."""
        view = TimezoneView(self, interaction.user.id)
        await interaction.response.send_message(
            "Let's configure your timezone. Select your continent:", 
            view=view, 
            ephemeral=True
        )

    @app_commands.command(name="timestamp", description="Get a discord timestamp for a specific time in your timezone.")
    @app_commands.describe(time="The time to convert (e.g., '5pm', '17:00', '2:30 AM')")
    async def timestamp(self, interaction: discord.Interaction, time: str):
        """
        Converts a time string (e.g. 5pm) to a Discord timestamp based on your stored timezone.
        """
        # 1. Get User Timezone
        user_tz_str = await self.config.user_from_id(interaction.user.id).timezone()
        
        if not user_tz_str:
            await interaction.response.send_message(
                "❌ You haven't set your timezone yet! Run `/mytimezone` first.", 
                ephemeral=True
            )
            return

        try:
            # 2. Parse the input time
            # We use fuzzy=True to ignore extra text if they type sentences, though not strictly needed here.
            # parser.parse defaults missing date fields to "today".
            dt = parser.parse(time, fuzzy=True)
            
            # 3. Localize to user's timezone
            tz = pytz.timezone(user_tz_str)
            
            # Combine 'today' from user's perspective with the parsed 'time'
            now_in_tz = datetime.datetime.now(tz)
            dt_localized = tz.localize(datetime.datetime.combine(now_in_tz.date(), dt.time()))
            
            # If the resulting time has already passed today by a significant margin (e.g. 12 hours), 
            # some logic might prefer 'tomorrow', but usually standard behavior is "Today at X".
            # We will stick to strict "Today at X" for consistency.

            # 4. Convert to Unix Timestamp
            timestamp_int = int(dt_localized.timestamp())

            # 5. Send Response
            # <t:TIMESTAMP:t> gives "5:00 PM"
            # <t:TIMESTAMP:F> gives "Tuesday, 25 April 2025 5:00 PM"
            # The prompt requested standard snowflake format or specific text.
            # Using <t:ID> defaults to Short Date Time "25 April 2025 5:00 PM"
            
            await interaction.response.send_message(
                f"<t:{timestamp_int}> (This is your local time)\n"
                f"-# {interaction.user.mention}'s timezone is {user_tz_str}."
            )

        except (ValueError, pytz.UnknownTimeZoneError):
            await interaction.response.send_message(
                f"❌ I couldn't understand the time `{time}`. Please try formats like `17:00`, `5pm`, or `2:30 AM`.", 
                ephemeral=True
            )

    @commands.group(name="timezoneset")
    @commands.admin_or_permissions(administrator=True)
    async def timezoneset(self, ctx):
        """Administrator settings for Timezone."""
        pass

    @timezoneset.command(name="view")
    async def timezoneset_view(self, ctx):
        """View all users who have configured their timezone."""
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