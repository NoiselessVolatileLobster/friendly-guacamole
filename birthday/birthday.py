import discord
import re
import asyncio
from datetime import datetime
import pytz
from typing import Optional, List, Dict, Union

from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

# --- UI CLASSES ---

class BirthdayModal(discord.ui.Modal, title="Set Your Birthday"):
    month = discord.ui.TextInput(
        label="Month (Number 1-12 or Name)",
        placeholder="e.g., July or 07",
        min_length=1,
        max_length=10,
        required=True
    )
    day = discord.ui.TextInput(
        label="Day",
        placeholder="e.g., 24",
        min_length=1,
        max_length=2,
        required=True
    )
    year = discord.ui.TextInput(
        label="Year (Optional)",
        placeholder="e.g., 1990 (Leave empty if preferred)",
        min_length=4,
        max_length=4,
        required=False
    )

    def __init__(self, cog, view):
        super().__init__()
        self.cog = cog
        self.view_obj = view

    async def on_submit(self, interaction: discord.Interaction):
        # Basic parsing logic
        month_input = self.month.value.lower()
        day_input = self.day.value
        year_input = self.year.value

        # Month parsing
        months = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12
        }
        
        m_val = None
        if month_input.isdigit():
            m_val = int(month_input)
        else:
            for k, v in months.items():
                if k in month_input:
                    m_val = v
                    break
        
        if not m_val or not (1 <= m_val <= 12):
            return await interaction.response.send_message("Invalid month provided.", ephemeral=True)
            
        try:
            d_val = int(day_input)
            if not (1 <= d_val <= 31): raise ValueError
        except ValueError:
            return await interaction.response.send_message("Invalid day provided.", ephemeral=True)

        y_val = None
        if year_input and year_input.isdigit():
            y_val = int(year_input)

        # Save to Config
        await self.cog.config.user(interaction.user).month.set(m_val)
        await self.cog.config.user(interaction.user).day.set(d_val)
        await self.cog.config.user(interaction.user).year.set(y_val)

        msg = f"Birthday set to: {m_val}/{d_val}"
        if y_val:
            msg += f"/{y_val}"
        
        await interaction.response.send_message(msg, ephemeral=True)


class TimezoneModal(discord.ui.Modal, title="Set Timezone"):
    tz = discord.ui.TextInput(
        label="Timezone Code",
        placeholder="e.g., America/New_York or UTC",
        min_length=2,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        tz_input = self.tz.value.strip()
        
        if tz_input not in pytz.all_timezones and tz_input != "EST":
            # Simple fuzzy check helper could go here, but keeping it strict for safety
            return await interaction.response.send_message(
                "Invalid Timezone. Please check `https://en.wikipedia.org/wiki/List_of_tz_database_time_zones`", 
                ephemeral=True
            )

        await self.cog.config.user(interaction.user).timezone.set(tz_input)
        await interaction.response.send_message(f"Timezone set to `{tz_input}`.", ephemeral=True)


class BirthdayView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="Set Birthday", style=discord.ButtonStyle.primary, emoji="🎂")
    async def set_bday(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BirthdayModal(self.cog, self))

    @discord.ui.button(label="Set Timezone", style=discord.ButtonStyle.secondary, emoji="🌎")
    async def set_tz(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TimezoneModal(self.cog))

    @discord.ui.button(label="Remove Data", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_data(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.config.user(interaction.user).clear()
        await interaction.response.send_message("Your birthday and timezone data have been removed.", ephemeral=True)


# --- MAIN COG ---

class Birthday(commands.Cog):
    """Manage birthdays with roles, announcements, and imports."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237123987123, force_registration=True)

        default_guild = {
            "announce_channel": None,
            "announce_message_year": "Happy {ordinal} birthday, {mention}! 🎉",
            "announce_message_no_year": "Happy Birthday {mention}! 🎉",
            "birthday_role": None,
        }
        default_user = {
            "month": None,
            "day": None,
            "year": None,
            "timezone": "UTC",
            "last_celebrated": None # Store as string "YYYY-MM-DD"
        }

        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        
        self.loop_task = self.bot.loop.create_task(self.birthday_loop())

    def cog_unload(self):
        if self.loop_task:
            self.loop_task.cancel()

    # --- HELPERS ---
    
    def get_ordinal(self, n):
        """Returns the ordinal string for a number (e.g., 21 -> 21st)."""
        if 11 <= (n % 100) <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
        return f"{n}{suffix}"

    def get_next_birthday(self, month, day, now):
        """Calculates the datetime of the next birthday."""
        try:
            bday_this_year = now.replace(month=month, day=day)
        except ValueError:
            # Handle leap years (Feb 29) on non-leap years
            bday_this_year = now.replace(month=3, day=1) 

        if bday_this_year.date() < now.date():
            # Birthday passed this year, next is next year
            return bday_this_year.replace(year=now.year + 1)
        return bday_this_year

    # --- LOOPS ---

    async def birthday_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                # Run logic every 10 minutes to catch timezones
                await self.check_birthdays()
                await asyncio.sleep(600) 
            except Exception as e:
                print(f"Error in birthday loop: {e}")
                await asyncio.sleep(60)

    async def check_birthdays(self):
        guilds_config = await self.config.all_guilds()
        
        # Pre-fetch configurations to minimize DB calls
        all_users = await self.config.all_users()

        for guild_id, g_conf in guilds_config.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            role_id = g_conf.get("birthday_role")
            role = guild.get_role(role_id) if role_id else None
            
            channel_id = g_conf.get("announce_channel")
            channel = guild.get_channel(channel_id) if channel_id else None

            for user_id, u_conf in all_users.items():
                member = guild.get_member(int(user_id))
                if not member:
                    continue

                if not u_conf.get("month") or not u_conf.get("day"):
                    continue

                # Timezone logic
                tz_name = u_conf.get("timezone", "UTC")
                try:
                    tz = pytz.timezone(tz_name)
                except pytz.UnknownTimeZoneError:
                    tz = pytz.UTC

                now_in_tz = datetime.now(tz)
                today_str = now_in_tz.strftime("%Y-%m-%d")
                
                is_birthday = (now_in_tz.month == u_conf["month"]) and (now_in_tz.day == u_conf["day"])
                
                # 1. ANNOUNCEMENTS & ROLE ADDITION
                if is_birthday:
                    # Check if already celebrated this year (in their timezone)
                    last_celeb = u_conf.get("last_celebrated")
                    
                    if last_celeb != today_str:
                        # Mark as celebrated
                        await self.config.user(member).last_celebrated.set(today_str)
                        
                        # Add Role
                        if role:
                            try:
                                await member.add_roles(role, reason="Birthday!")
                            except discord.Forbidden:
                                pass

                        # Send Message
                        if channel:
                            ordinal_str = ""
                            has_year = bool(u_conf.get("year"))
                            
                            if has_year:
                                # Use ordinal message and calculate age
                                age = now_in_tz.year - u_conf["year"]
                                ordinal_str = self.get_ordinal(age)
                                msg_template = g_conf.get("announce_message_year", "Happy {ordinal} birthday, {mention}! 🎉")
                            else:
                                # Use simple message
                                msg_template = g_conf.get("announce_message_no_year", "Happy Birthday {mention}! 🎉")


                            try:
                                msg = msg_template.replace("{mention}", member.mention)
                                msg = msg.replace("{ordinal}", ordinal_str)
                                await channel.send(msg)
                            except discord.Forbidden:
                                pass

                # 2. ROLE REMOVAL (24 hours later logic)
                # Logic: If user has role, but it is NOT their birthday in their timezone, remove it.
                if role and role in member.roles:
                    if not is_birthday:
                        try:
                            await member.remove_roles(role, reason="Birthday over")
                        except discord.Forbidden:
                            pass

    # --- USER COMMANDS ---

    @commands.command(name="seebirthday")
    async def seebirthday(self, ctx, user: discord.Member = None):
        """See when a user's birthday is."""
        if not user:
            user = ctx.author
        
        conf = await self.config.user(user).all()
        if not conf["month"] or not conf["day"]:
            return await ctx.send(f"{user.display_name} hasn't set their birthday yet.")
        
        date_str = f"{conf['month']}/{conf['day']}"
        if conf["year"]:
            date_str += f"/{conf['year']}"
            
        tz_str = conf.get("timezone", "UTC")
        
        embed = discord.Embed(color=user.color)
        embed.set_author(name=f"{user.display_name}'s Birthday", icon_url=user.display_avatar.url)
        embed.add_field(name="Date", value=date_str, inline=True)
        embed.add_field(name="Timezone", value=tz_str, inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name="listbirthdays")
    async def listbirthdays(self, ctx, count: int = 10):
        """List the next X upcoming birthdays."""
        if count > 25: count = 25 # Cap to prevent abuse
        
        data = await self.config.all_users()
        upcoming_list = []
        
        # Use UTC for sorting comparison to keep it simple, 
        # though ideally we'd project everyone's next birthday to UTC.
        now_utc = datetime.now(pytz.UTC)

        for uid, u_data in data.items():
            if not u_data["month"] or not u_data["day"]:
                continue
            
            member = ctx.guild.get_member(uid)
            if not member:
                continue

            # Calculate next birthday timestamp
            next_bday = self.get_next_birthday(u_data["month"], u_data["day"], now_utc)
            delta = (next_bday - now_utc).days
            
            upcoming_list.append((member, next_bday, delta, u_data["year"]))

        # Sort by delta (days until birthday)
        upcoming_list.sort(key=lambda x: x[2])
        
        # Slice top X
        top_x = upcoming_list[:count]
        
        if not top_x:
            return await ctx.send("No birthdays registered.")

        msg = ""
        for member, date_obj, delta, year in top_x:
            date_str = date_obj.strftime("%B %d")
            age_str = ""
            if year:
                age = date_obj.year - year
                age_str = f" (Turning {age})"
            
            days_str = "Today!" if delta == 0 else f"in {delta} days"
            msg += f"**{member.display_name}**: {date_str}{age_str} - {days_str}\n"

        embed = discord.Embed(title=f"Upcoming {len(top_x)} Birthdays", description=msg, color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands.group(name="birthday", aliases=["bday"], invoke_without_command=True)
    async def birthday(self, ctx):
        """Manage birthdays."""
        # Display the interactive View
        view = BirthdayView(self)
        desc = (
            "Use the buttons below to configure your birthday and timezone.\n\n"
            "**Timezones**: You can find your timezone code [here]"
            "(https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)."
        )
        embed = discord.Embed(
            title="Birthday Management",
            description=desc,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed, view=view)

    # --- ADMIN COMMANDS ---

    @commands.group(name="bset")
    @checks.admin_or_permissions(manage_guild=True)
    async def bset(self, ctx):
        """Admin configuration for birthdays."""
        pass

    @bset.command(name="channel")
    async def bset_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for birthday announcements."""
        if channel:
            await self.config.guild(ctx.guild).announce_channel.set(channel.id)
            await ctx.send(f"Announcements will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).announce_channel.set(None)
            await ctx.send("Announcements disabled.")

    @bset.command(name="messagesimple")
    async def bset_message_simple(self, ctx, *, message: str):
        """Set the announcement message when the user has NO birth year (no ordinal). Use {mention} for the user."""
        await self.config.guild(ctx.guild).announce_message_no_year.set(message)
        await ctx.send(f"Simple message set to: {message}")

    @bset.command(name="messageordinal")
    async self.config.user_from_id(uid_int).all() as u_conf:
        """Set the announcement message when the user HAS a birth year. Use {mention} for the user and {ordinal} for age."""
        await self.config.guild(ctx.guild).announce_message_year.set(message)
        await ctx.send(f"Ordinal message set to: {message}")

    @bset.command(name="role")
    async def bset_role(self, ctx, role: discord.Role = None):
        """Set the role to give on birthdays (removed after 24h)."""
        if role:
            await self.config.guild(ctx.guild).birthday_role.set(role.id)
            await ctx.send(f"Birthday role set to {role.name}.")
        else:
            await self.config.guild(ctx.guild).birthday_role.set(None)
            await ctx.send("Birthday role disabled.")

    @bset.command(name="listall")
    async def bset_listall(self, ctx):
        """List all stored birthdays and timezones for guild members."""
        
        all_users_data = await self.config.all_users()
        output = []
        
        # Header
        output.append(f"Registered Birthdays for {ctx.guild.name}:\n")
        output.append("-" * 40)
        
        found_count = 0
        
        for user_id, u_data in all_users_data.items():
            if not u_data["month"] or not u_data["day"]:
                continue
            
            member = ctx.guild.get_member(user_id)
            if not member:
                continue # Skip users not in this guild
            
            found_count += 1
            
            # Format Date
            date_str = f"{u_data['month']}/{u_data['day']}"
            if u_data['year']:
                date_str += f"/{u_data['year']}"
            
            # Format Output Line
            tz_str = u_data.get('timezone', 'UTC')
            line = f"{member.display_name} (ID: {user_id}): {date_str} | TZ: {tz_str}"
            output.append(line)

        if not found_count:
            return await ctx.send("No birthdays are currently registered for members in this guild.")
            
        output.append("-" * 40)
        output.append(f"Total registered members: {found_count}")

        # Use pagify for potentially long output
        output_text = "\n".join(output)
        for page in pagify(output_text, delims=["\n"], page_length=1900):
            await ctx.send(box(page))

    @bset.command(name="import")
    async def bset_import(self, ctx):
        """
        Import birthdays from a text file attached to the message.
        Format expected: MMM-DD: ID name ... | Time zone: Region/City
        """
        if not ctx.message.attachments:
            return await ctx.send("Please attach a .txt file with the data.")
        
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".txt"):
            return await ctx.send("File must be a .txt file.")

        try:
            content = await attachment.read()
            text = content.decode("utf-8")
        except Exception:
            return await ctx.send("Could not read file.")

        # REGEX PATTERN
        # Captures: Month, Day, ID, (Timezone optional)
        # Matches: "Jul-24: 105104365890580480" ... "| Time zone: America/Winnipeg"
        pattern = r"(?P<month>[A-Za-z]{3})-(?P<day>\d{1,2}):\s+(?P<id>\d+).*?(?:\|\s+Time zone:\s+(?P<tz>.*))?"
        
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, 
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }

        imported_count = 0
        errors = 0

        lines = text.splitlines()
        for line in lines:
            if not line.strip(): continue
            
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                m_str = match.group("month").lower()
                d_str = match.group("day")
                uid_str = match.group("id")
                tz_str = match.group("tz")

                m_int = month_map.get(m_str)
                if not m_int:
                    errors += 1
                    continue

                try:
                    d_int = int(d_str)
                    uid_int = int(uid_str)
                except ValueError:
                    errors += 1
                    continue

                # Clean timezone
                final_tz = "UTC"
                if tz_str:
                    clean_tz = tz_str.strip()
                    if clean_tz in pytz.all_timezones or clean_tz == "EST":
                        final_tz = clean_tz
                
                # Save to config
                # Note: The import format provided didn't have years, so year is None
                async with self.config.user_from_id(uid_int).all() as u_conf:
                    u_conf["month"] = m_int
                    u_conf["day"] = d_int
                    u_conf["year"] = None
                    u_conf["timezone"] = final_tz
                
                imported_count += 1
            else:
                # Regex didn't match line structure
                if len(line) > 5: # ignore tiny junk lines
                    errors += 1

        await ctx.send(f"Import complete. Imported: {imported_count}. Skipped/Errors: {errors}.")