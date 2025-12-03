import re
import discord
from discord.ui import View, Button
from redbot.core import Config, commands
from redbot.core.commands import Context
from redbot.core import checks
from datetime import datetime, timezone
from typing import Optional

DEFAULT_GUILD = {
    "welcome_channel_id": None,
    "welcome_role_id": None,
    "welcome_message": (
        "Welcome back, {user}! We're glad you're here for your {count} time. "
        "You were last here on {last_join_date}. Please check out {role}."
    ),
    "first_join_message": (
        "Welcome, {user}! We are thrilled to have you here for the first time. "
        "Check out {role} to get started."
    ),
}

DEFAULT_MEMBER = {
    "rejoin_count": 0,
    "last_join_date": None,
}


class PaginatorView(View):
    def __init__(self, ctx: Context, pages: list[str]):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.pages = pages
        self.index = 0
        self.message = None

    async def send(self):
        self.message = await self.ctx.send(f"```{self.pages[0]}```", view=self)

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        if self.index > 0:
            self.index -= 1
        await interaction.response.edit_message(
            content=f"```{self.pages[self.index]}```", view=self
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await interaction.response.edit_message(
            content=f"```{self.pages[self.index]}```", view=self
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(view=None)
        self.stop()
class JoinTracker(commands.Cog):
    """Tracks joins, rejoins, and generates welcome messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=148008422401290145)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_member(**DEFAULT_MEMBER)

    # Utility
    def ordinal(self, n: int) -> str:
        if 10 <= n % 100 <= 20:
            return f"{n}th"
        return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        data = await self.config.member(member).all()
        settings = await self.config.guild(member.guild).all()

        first = data["last_join_date"] is None and data["rejoin_count"] == 0
        rejoin = data["rejoin_count"]
        prev_date = data["last_join_date"]

        # Update counters
        if not first:
            rejoin += 1
            await self.config.member(member).rejoin_count.set(rejoin)

        if member.joined_at:
            await self.config.member(member).last_join_date.set(
                member.joined_at.astimezone(timezone.utc).isoformat()
            )

        # Get welcome channel
        channel = member.guild.get_channel(settings["welcome_channel_id"])
        if not channel:
            return

        # Role mention
        role_mention = ""
        role_id = settings["welcome_role_id"]
        if role_id:
            role = member.guild.get_role(role_id)
            role_mention = role.mention if role else f"<@&{role_id}>"

        # Message formatting
        if first:
            tmpl = settings["first_join_message"]
            fields = {"user": member.mention, "role": role_mention}
        else:
            tmpl = settings["welcome_message"]
            prev = (
                datetime.fromisoformat(prev_date).strftime("%Y-%m-%d")
                if prev_date
                else "an unknown date"
            )
            fields = {
                "user": member.mention,
                "role": role_mention,
                "count": self.ordinal(rejoin + 1),
                "last_join_date": prev,
            }

        try:
            msg = tmpl.format(**fields)
        except Exception:
            msg = f"Welcome back, {member.mention}!"

        await channel.send(msg)
    @commands.group(name="jointracker", aliases=["jt"])
    @checks.admin_or_permissions(manage_guild=True)
    async def jointracker(self, ctx: Context):
        pass

    @jointracker.command()
    async def setchannel(self, ctx: Context, channel: Optional[discord.TextChannel] = None):
        await self.config.guild(ctx.guild).welcome_channel_id.set(
            channel.id if channel else None
        )
        await ctx.send("Welcome channel updated." if channel else "Welcome channel cleared.")

    @jointracker.command()
    async def setwelcomerole(self, ctx: Context, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).welcome_role_id.set(
            role.id if role else None
        )
        await ctx.send("Welcome role updated." if role else "Welcome role cleared.")

    @jointracker.command()
    async def setfirstjoinmsg(self, ctx: Context, *, msg: str):
        await self.config.guild(ctx.guild).first_join_message.set(msg)
        await ctx.send("First join message updated.")

    @jointracker.command()
    async def setwelcomemsg(self, ctx: Context, *, msg: str):
        await self.config.guild(ctx.guild).welcome_message.set(msg)
        await ctx.send("Rejoin message updated.")

    @jointracker.command()
    async def setrejoins(self, ctx: Context, target: str, count: int):
        """Set rejoins by name OR raw user ID (even if not in server)."""
        if count < 0:
            return await ctx.send("Count must be non-negative.")

        # Detect raw user ID
        m = re.search(r"(\\d{17,20})", target)
        if m:
            uid = int(m.group(1))
            member = ctx.guild.get_member(uid)
            try:
                user = member or self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            except Exception:
                user = discord.Object(id=uid)
            target_obj = user
        else:
            member = ctx.guild.get_member_named(target)
            if not member:
                return await ctx.send("Invalid target.")
            target_obj = member

        await self.config.member(target_obj).rejoin_count.set(count)

        # Update join date if applicable
        if isinstance(target_obj, discord.Member) and target_obj.joined_at:
            await self.config.member(target_obj).last_join_date.set(
                target_obj.joined_at.astimezone(timezone.utc).isoformat()
            )
        else:
            await self.config.member(target_obj).last_join_date.set(None)

        await ctx.send(f"Updated rejoins for {target_obj.id} to {count}.")

    @jointracker.command()
    async def populate(self, ctx: Context):
        """Assign missing members a join count of 1."""
        await ctx.defer()
        data = await self.config.all_members(ctx.guild)
        updated = 0

        for member in ctx.guild.members:
            if member.bot:
                continue

            mdata = data.get(str(member.id), {})
            if mdata.get("rejoin_count") is None:
                await self.config.member(member).rejoin_count.set(0)
                if member.joined_at:
                    await self.config.member(member).last_join_date.set(
                        member.joined_at.astimezone(timezone.utc).isoformat()
                    )
                updated += 1

        await ctx.send(f"Populated {updated} members.")
    @jointracker.command()
    async def list(self, ctx: Context):
        await ctx.defer()
        guild = ctx.guild
        data = await self.config.all_members(guild)

        ids = set(int(k) for k in data.keys()) | {
            m.id for m in guild.members if not m.bot
        }

        rows = []
        for uid in sorted(ids):
            d = data.get(str(uid), {})
            count = d.get("rejoin_count", 0)
            iso = d.get("last_join_date")

            member = guild.get_member(uid)
            if member:
                name = member.display_name
            else:
                try:
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    name = user.name
                except Exception:
                    name = "?"

            last = iso or "?"
            rows.append((str(uid), name, last, str(count + 1)))

        # Build pages
        header = f"{'User ID':<20} {'Name':<30} {'Last Join':<20} Joined\n"
        sep = "-" * 80 + "\n"

        pages = []
        page = header + sep
        count = 0

        for uid, name, last, joined in rows:
            page += f"{uid:<20} {name:<30} {last:<20} {joined}\n"
            count += 1
            if count >= 12:
                pages.append(page)
                page = header + sep
                count = 0

        if count:
            pages.append(page)

        view = PaginatorView(ctx, pages)
        await view.send()

    @jointracker.command()
    async def downloadcsv(self, ctx: Context):
        """Download the full join tracker data as CSV."""
        import csv
        from io import StringIO

        guild = ctx.guild
        data = await self.config.all_members(guild)

        ids = set(int(k) for k in data) | {m.id for m in guild.members if not m.bot}

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["User ID", "Name", "Last Join Date", "Times Joined"])

        for uid in sorted(ids):
            d = data.get(str(uid), {})
            count = d.get("rejoin_count", 0)
            iso = d.get("last_join_date")

            member = guild.get_member(uid)
            if member:
                name = member.display_name
            else:
                try:
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    name = user.name
                except Exception:
                    name = "?"

            writer.writerow([uid, name, iso or "?", count + 1])

        buffer.seek(0)
        await ctx.send(
            file=discord.File(buffer, filename="jointracker.csv")
        )
