import discord
import asyncio
import logging
import json
import io
from datetime import datetime, timedelta, timezone
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import humanize_list, pagify, box

log = logging.getLogger("red.serverversary")

class Serverversary(commands.Cog):
    """
    Celebrate user join serverversaries with roles and messages.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=948375934857, force_registration=True)

        default_guild = {
            "channel_id": None,
            "role_id": None,
            "message": "Happy {ordinal} serverversary, {mention}!",
            "active_roles": {},  # {user_id: timestamp_to_remove}
            "enabled": True
        }

        default_member = {
            "join_date": None,  # Stored as timestamp float
            "last_celebrated_year": 0
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        self.bg_loop = self.bot.loop.create_task(self.serverversary_loop())

    def cog_unload(self):
        if self.bg_loop:
            self.bg_loop.cancel()

    def get_ordinal(self, n):
        """Helper to convert number to ordinal string (1st, 2nd, etc)."""
        if 11 <= (n % 100) <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
        return f"{n}{suffix}"

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Record join date when a user joins."""
        # We store the date silently.
        # We prefer the Discord joined_at date, converted to UTC timestamp.
        if member.joined_at:
            await self.config.member(member).join_date.set(member.joined_at.timestamp())

    async def serverversary_loop(self):
        """Runs hourly to check for serverversaries and role removals."""
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog("Serverversary"):
            try:
                now = datetime.now(timezone.utc)
                
                for guild in self.bot.guilds:
                    if not await self.config.guild(guild).enabled():
                        continue
                        
                    await self.check_guild_serverversaries(guild, now)
                    await self.check_guild_role_removals(guild, now)
                    
            except Exception as e:
                log.error("Error in serverversary loop", exc_info=e)
            
            # Sleep for an hour
            await asyncio.sleep(3600)

    async def check_guild_serverversaries(self, guild, now):
        channel_id = await self.config.guild(guild).channel_id()
        role_id = await self.config.guild(guild).role_id()
        msg_template = await self.config.guild(guild).message()
        
        channel = guild.get_channel(channel_id) if channel_id else None
        role = guild.get_role(role_id) if role_id else None

        # Optimization: We iterate all members in config to find matches.
        # In very large guilds, this might need further optimization, 
        # but for typical use, iterating member config is safe in Red.
        all_members = await self.config.all_members(guild)

        for user_id, data in all_members.items():
            if not data.get("join_date"):
                continue

            join_ts = data["join_date"]
            last_year = data.get("last_celebrated_year", 0)
            
            join_dt = datetime.fromtimestamp(join_ts, timezone.utc)
            
            # Calculate the serverversary date for the CURRENT year
            try:
                serverversary_this_year = join_dt.replace(year=now.year)
            except ValueError:
                # Handle leap years (Feb 29 joined) -> Move to Feb 28 or Mar 1
                serverversary_this_year = join_dt.replace(year=now.year, day=28)

            years_joined = now.year - join_dt.year

            if years_joined < 1:
                continue # Haven't been here a year yet

            # Logic:
            # 1. We haven't celebrated this year (last_year < now.year)
            # 2. The current time is past the serverversary time (now >= serverversary_this_year)
            # 3. We are within a reasonable window (e.g., within 24 hours of the serverversary time)
            #    to prevent celebrating serverversaries missed 6 months ago if the bot was off.
            #    However, prompt asks to post "as close to actual join time".
            #    Since loop is hourly, checking `now >= serverversary` triggers it the first hour after the time passes.
            
            if last_year < now.year and now >= serverversary_this_year:
                # Update DB immediately so we don't double post if logic fails below
                await self.config.member_from_ids(guild.id, user_id).last_celebrated_year.set(now.year)
                
                member = guild.get_member(user_id)
                if not member:
                    continue

                # 1. Send Message
                if channel:
                    ordinal_str = self.get_ordinal(years_joined)
                    formatted_msg = msg_template.replace("{mention}", member.mention).replace("{ordinal}", ordinal_str)
                    
                    try:
                        await channel.send(formatted_msg)
                    except discord.Forbidden:
                        log.warning(f"Could not send serverversary message in {guild.name}")

                # 2. Assign Role
                if role:
                    try:
                        await member.add_roles(role, reason=f"{years_joined} Year Serverversary")
                        
                        # Schedule removal
                        remove_at = now + timedelta(hours=24)
                        async with self.config.guild(guild).active_roles() as active_roles:
                            active_roles[str(user_id)] = remove_at.timestamp()
                            
                    except discord.Forbidden:
                        log.warning(f"Could not assign serverversary role in {guild.name}")

    async def check_guild_role_removals(self, guild, now):
        """Checks for expired serverversary roles."""
        role_id = await self.config.guild(guild).role_id()
        if not role_id:
            return
            
        role = guild.get_role(role_id)
        if not role:
            return

        async with self.config.guild(guild).active_roles() as active_roles:
            # Iterate a copy of keys to modify the dict during iteration
            for user_id_str, remove_ts in list(active_roles.items()):
                if now.timestamp() >= remove_ts:
                    # Time to remove
                    user_id = int(user_id_str)
                    member = guild.get_member(user_id)
                    
                    if member and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="Serverversary day over")
                        except discord.Forbidden:
                            pass # Can't remove, permission issue
                    
                    # Delete from config regardless of whether user is in server/role removal worked
                    # to prevent infinite trying.
                    del active_roles[user_id_str]

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def serverversary(self, ctx):
        """Manage serverversary settings."""
        pass

    @serverversary.command()
    async def sync(self, ctx):
        """
        Update current members' join dates.
        
        This will look at every member currently in the server. 
        If they are not in the database, it saves their Discord join date.
        It does NOT overwrite existing database entries (preserves original join dates if manually edited).
        """
        async with ctx.typing():
            count = 0
            for member in ctx.guild.members:
                if not member.joined_at:
                    continue
                
                # Check if exists
                stored_date = await self.config.member(member).join_date()
                if stored_date is None:
                    await self.config.member(member).join_date.set(member.joined_at.timestamp())
                    count += 1
            
        await ctx.send(f"Synced! Recorded join dates for {count} new members.")

    @serverversary.command()
    async def channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for serverversary messages."""
        if channel:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"Serverversary messages will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("Serverversary messages disabled (no channel set).")

    @serverversary.command()
    async def role(self, ctx, role: discord.Role = None):
        """Set the role to assign for 24 hours."""
        if role:
            await self.config.guild(ctx.guild).role_id.set(role.id)
            await ctx.send(f"I will assign {role.name} on serverversaries.")
        else:
            await self.config.guild(ctx.guild).role_id.set(None)
            await ctx.send("Serverversary role disabled.")

    @serverversary.command()
    async def message(self, ctx, *, message: str):
        """
        Set the serverversary message.
        
        Variables:
        {mention} - Mentions the user
        {ordinal} - The year number (1st, 2nd, etc)
        """
        await self.config.guild(ctx.guild).message.set(message)
        await ctx.send(f"Message set to:\n{box(message)}")
        # Send a test example
        example = message.replace("{mention}", ctx.author.mention).replace("{ordinal}", "1st")
        await ctx.send(f"Example: {example}")

    @serverversary.command()
    async def list(self, ctx):
        """List upcoming serverversaries (sorted by nearest date)."""
        all_members = await self.config.all_members(ctx.guild)
        if not all_members:
            return await ctx.send("No serverversaries recorded. Run `[p]serverversary sync` first.")

        now = datetime.now(timezone.utc)
        upcoming = []

        # Build list of (next_anniv_date, display_name, years_joined)
        for user_id, data in all_members.items():
            if not data.get("join_date"):
                continue
            
            # Filter for members still in guild
            member = ctx.guild.get_member(user_id)
            if not member:
                continue

            ts = data["join_date"]
            join_dt = datetime.fromtimestamp(ts, timezone.utc)
            
            # Logic to find the NEXT occurrence relative to NOW
            try:
                anniv_this_year = join_dt.replace(year=now.year)
            except ValueError:
                # Handle Feb 29 on non-leap years -> Feb 28
                anniv_this_year = join_dt.replace(year=now.year, day=28)

            if anniv_this_year < now:
                # It passed this year, next one is next year
                target_year = now.year + 1
            else:
                # It is upcoming later this year
                target_year = now.year

            # Calculate precise next anniversary date
            try:
                next_anniv = join_dt.replace(year=target_year)
            except ValueError:
                # Handle Feb 29 for next year if applicable
                next_anniv = join_dt.replace(year=target_year, day=28)
            
            years_joined = target_year - join_dt.year
            upcoming.append((next_anniv, member.display_name, years_joined))

        if not upcoming:
            return await ctx.send("No current members found with recorded join dates.")

        # Sort by date
        upcoming.sort(key=lambda x: x[0])

        lines = ["**Upcoming Serverversaries**"]
        for next_anniv, name, years in upcoming:
            date_str = next_anniv.strftime("%d %b %Y")
            lines.append(f"{name}: {date_str} ({years} years)")

        msg = "\n".join(lines)
        
        for page in pagify(msg, page_length=1900):
            await ctx.send(page)

    @serverversary.command()
    async def export(self, ctx):
        """Export serverversary data to a JSON file."""
        data = await self.config.all_members(ctx.guild)
        if not data:
            return await ctx.send("No data to export.")

        # Prepare JSON
        file_content = json.dumps(data, indent=4)
        f = io.BytesIO(file_content.encode('utf-8'))
        
        await ctx.send("Here is the serverversary data backup:", file=discord.File(f, filename=f"serverversary_export_{ctx.guild.id}.json"))

    @serverversary.command()
    async def import_data(self, ctx):
        """
        Import serverversary data from a JSON file.
        
        Attach a valid JSON file to this command.
        """
        if not ctx.message.attachments:
            return await ctx.send("Please attach a JSON file.")
        
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".json"):
            return await ctx.send("File must be a .json file.")

        try:
            content = await attachment.read()
            data = json.loads(content)
            
            # Validation: Ensure it looks like member data
            if not isinstance(data, dict):
                raise ValueError("Root must be a dictionary.")
                
            # Overwrite/Update data
            async with self.config.guild(ctx.guild).all_members() as members_data:
                # We do a merge: Update existing, add new.
                for user_id, user_data in data.items():
                    if "join_date" in user_data:
                        # We use the raw dict access to set bulk data
                        members_data[user_id] = user_data

            await ctx.send("Data imported successfully.")
            
        except json.JSONDecodeError:
            await ctx.send("Invalid JSON format.")
        except Exception as e:
            await ctx.send(f"Error importing data: {e}")