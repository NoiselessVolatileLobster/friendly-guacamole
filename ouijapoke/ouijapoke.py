import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import humanize_list
from datetime import datetime, timedelta, timezone
import random
import re

# Pydantic is used for structured configuration in modern Red cogs
try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object
    Field = lambda *args, **kwargs: None

# --- Configuration Schema (Settings) ---

class OuijaSettings(BaseModel):
    """Schema for guild configuration settings."""
    poke_days: int = Field(default=30, ge=1, description="Days a member must be inactive to be eligible for a poke.")
    summon_days: int = Field(default=60, ge=1, description="Days a member must be inactive to be eligible for a summon.")
    
    poke_message: str = Field(
        default="Hey {user_mention}, the Ouija Board feels your presence. Come say hello!",
        description="The message used when poking. Use {user_mention} for the user."
    )
    
    poke_gifs: list[str] = Field(default=[], description="List of URLs for 'poke' GIFs.")
    summon_gifs: list[str] = Field(default=[], description="List of URLs for 'summon' GIFs.")

# --- Cog Class ---

class OuijaPoke(commands.Cog):
    """Tracks user activity and allows 'poking' or 'summoning' inactive members with a spooky twist."""

    # Updated identifier to reflect the new name/concept, though Red uses the file name too.
    def __init__(self, bot):
        self.bot = bot
        # Config setup:
        # last_seen: A dictionary mapping user IDs to their last active datetime (ISO 8601 string)
        # ouija_settings: The Pydantic model for configurable settings
        self.config = Config.get_conf(self, identifier=148000552390, force_registration=True)
        self.config.register_guild(
            last_seen={}, # {user_id: "ISO_DATETIME_STRING"}
            ouija_settings=OuijaSettings().model_dump()
        )

    # --- Utility Methods ---

    async def _get_settings(self, guild_id: int) -> OuijaSettings:
        """Retrieves and parses the guild settings."""
        settings_data = await self.config.guild(guild_id).ouija_settings()
        return OuijaSettings(**settings_data)

    async def _set_settings(self, guild_id: int, settings: OuijaSettings):
        """Saves the updated guild settings."""
        await self.config.guild(guild_id).ouija_settings.set(settings.model_dump())
    
    def _is_valid_gif_url(self, url: str) -> bool:
        """Simple check if the URL looks like a GIF link."""
        return re.match(r'^https?://[^\s/$.?#].[^\s]*\.(gif|webp)(\?.*)?$', url, re.IGNORECASE) is not None

    # --- Listeners (Event Handlers) ---
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Updates the last_seen time for any message sent."""
        if message.guild is None or message.author.bot or message.webhook_id:
            return

        user_id = str(message.author.id)
        current_time_utc = datetime.now(timezone.utc).isoformat()
        
        data = await self.config.guild(message.guild.id).last_seen()
        data[user_id] = current_time_utc
        await self.config.guild(message.guild.id).last_seen.set(data)
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Sets the last_seen time for a new member to now."""
        if member.bot:
            return
        
        user_id = str(member.id)
        current_time_utc = datetime.now(timezone.utc).isoformat()
        
        data = await self.config.guild(member.guild.id).last_seen()
        if user_id not in data:
            data[user_id] = current_time_utc
            await self.config.guild(member.guild.id).last_seen.set(data)


    # --- Poking/Summoning Logic ---

    def _get_inactivity_cutoff(self, days: int) -> datetime:
        """Calculates the ISO datetime cutoff point for inactivity."""
        return datetime.now(timezone.utc) - timedelta(days=days)

    async def _get_eligible_members(self, guild: discord.Guild, days_inactive: int) -> list[discord.Member]:
        """Gets a list of members eligible for poking/summoning."""
        cutoff_dt = self._get_inactivity_cutoff(days_inactive)
        last_seen_data = await self.config.guild(guild.id).last_seen()
        eligible_members = []
        
        for user_id_str, last_seen_dt_str in last_seen_data.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            if member is None or member.bot:
                continue

            try:
                last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if last_seen_dt < cutoff_dt:
                eligible_members.append(member)
        
        return eligible_members

    async def _send_activity_message(self, ctx: commands.Context, member: discord.Member, message_text: str, gif_list: list[str], title: str):
        """Sends the final message with the embedded GIF."""
        
        final_message = message_text.replace("{user_mention}", member.mention)

        embed = discord.Embed(
            title=title,
            description=final_message,
            color=await ctx.embed_color()
        )
        
        if gif_list:
            gif_url = random.choice(gif_list)
            embed.set_image(url=gif_url)

        await ctx.send(content=member.mention, embed=embed)


    # --- User Commands ---

    @commands.group(invoke_without_command=True, aliases=["ouija"])
    async def ouijapoke(self, ctx: commands.Context):
        """
        Commands for OuijaPoke: check your status, or poke/summon inactive members.
        """
        await ctx.send_help(ctx.command)

    @ouijapoke.command(name="check")
    async def ouijapoke_check(self, ctx: commands.Context):
        """Shows how many days it has been since you last sent a message."""
        user_id = str(ctx.author.id)
        data = await self.config.guild(ctx.guild.id).last_seen()
        last_seen_dt_str = data.get(user_id)

        if not last_seen_dt_str:
            return await ctx.send("I haven't recorded any activity for you yet! Say something now!")

        last_seen_dt = datetime.fromisoformat(last_seen_dt_str).replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        
        difference = now_dt - last_seen_dt
        days = difference.days
        
        message = (
            f"The Ouija Planchette last saw you move **{days} days** ago. "
            f"(On {last_seen_dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"
        )
        await ctx.send(message)


    @ouijapoke.command(name="poke")
    async def ouijapoke_random(self, ctx: commands.Context):
        """
        Pokes a random member who has been inactive for the configured number of days.
        """
        await ctx.trigger_typing()
        settings = await self._get_settings(ctx.guild.id)
        
        eligible_members = await self._get_eligible_members(ctx.guild, settings.poke_days)
        
        if not eligible_members:
            return await ctx.send(f"Everyone is active! No one is eligible to be poked (needs >{settings.poke_days} days of inactivity).")

        member_to_poke = random.choice(eligible_members)
        
        await self._send_activity_message(
            ctx,
            member_to_poke,
            settings.poke_message,
            settings.poke_gifs,
            title="👻 Ouija Poke!"
        )
    
    @ouijapoke.command(name="summon")
    async def ouijasummon_random(self, ctx: commands.Context):
        """
        Summons a random member who has been inactive for the configured number of days.
        """
        await ctx.trigger_typing()
        settings = await self._get_settings(ctx.guild.id)
        
        eligible_members = await self._get_eligible_members(ctx.guild, settings.summon_days)
        
        if not eligible_members:
            return await ctx.send(f"The spirits are quiet. No one is eligible to be summoned (needs >{settings.summon_days} days of inactivity).")

        member_to_summon = random.choice(eligible_members)
        
        await self._send_activity_message(
            ctx,
            member_to_summon,
            settings.poke_message, 
            settings.summon_gifs, # Use the summon GIF list
            title="🕯️ ADMIN SUMMONING RITUAL 🕯️"
        )


    # --- Admin Commands (Settings and Overrides) ---

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def ouijaset(self, ctx: commands.Context):
        """Manages the OuijaPoke settings."""
        if ctx.invoked_subcommand is None:
            settings = await self._get_settings(ctx.guild.id)
            
            msg = (
                "**OuijaPoke Settings**\n"
                f"- **Poke Inactivity:** {settings.poke_days} days\n"
                f"- **Summon Inactivity:** {settings.summon_days} days\n"
                f"- **Poke Message:** `{settings.poke_message}`\n"
                f"- **Poke GIFs:** {len(settings.poke_gifs)} stored\n"
                f"- **Summon GIFs:** {len(settings.summon_gifs)} stored"
            )
            await ctx.send(msg)

    # --- Days Settings ---

    @ouijaset.command(name="pokedays")
    async def ouijaset_pokedays(self, ctx: commands.Context, days: int):
        """Sets the number of days a member must be inactive to be eligible for a 'poke'."""
        if days < 1:
            return await ctx.send("Days must be 1 or greater.")
        settings = await self._get_settings(ctx.guild.id)
        settings.poke_days = days
        await self._set_settings(ctx.guild.id, settings)
        await ctx.send(f"Members are now eligible to be poked after **{days}** days of inactivity.")

    @ouijaset.command(name="summondays")
    async def ouijaset_summondays(self, ctx: commands.Context, days: int):
        """Sets the number of days a member must be inactive to be eligible for a 'summon'."""
        if days < 1:
            return await ctx.send("Days must be 1 or greater.")
        settings = await self._get_settings(ctx.guild.id)
        settings.summon_days = days
        await self._set_settings(ctx.guild.id, settings)
        await ctx.send(f"Members are now eligible to be summoned after **{days}** days of inactivity.")

    # --- Message Setting ---
    
    @ouijaset.command(name="pokemessage")
    async def ouijaset_pokemessage(self, ctx: commands.Context, *, message: str):
        """
        Sets the message used when a user is poked. 
        
        Use `{user_mention}` as a variable for the user mention.
        """
        if "{user_mention}" not in message:
            return await ctx.send("The message must contain `{user_mention}` to mention the inactive user.")
        settings = await self._get_settings(ctx.guild.id)
        settings.poke_message = message
        await self._set_settings(ctx.guild.id, settings)
        await ctx.send(f"Poke message set to: `{message}`")


    # --- GIF Management Commands ---

    @ouijaset.group(name="pokegifs", invoke_without_command=True)
    async def ouijaset_pokegifs(self, ctx: commands.Context):
        """
        Manages the list of GIFs used for the 'poke' command.
        
        Use `[p]ouijaset pokegifs add <url>` or `[p]ouijaset pokegifs remove <url>`
        """
        settings = await self._get_settings(ctx.guild.id)
        gifs = settings.poke_gifs
        if not gifs:
            msg = "There are currently no Poke GIFs configured."
        else:
            gif_list = "\n".join(f"`{i+1}.` <{g}>" for i, g in enumerate(gifs))
            msg = f"**Current Poke GIFs ({len(gifs)} total):**\n{gif_list}"
        
        await ctx.send(msg)

    @ouijaset_pokegifs.command(name="add")
    async def pokegifs_add(self, ctx: commands.Context, url: str):
        """Adds a new GIF URL to the poke list."""
        if not self._is_valid_gif_url(url):
            return await ctx.send("That doesn't look like a valid GIF URL. Make sure it ends with `.gif` or a common animated extension.")
        
        settings = await self._get_settings(ctx.guild.id)
        if url in settings.poke_gifs:
            return await ctx.send("That GIF is already in the list.")
        
        settings.poke_gifs.append(url)
        await self._set_settings(ctx.guild.id, settings)
        await ctx.send(f"Added new Poke GIF: <{url}>")

    @ouijaset_pokegifs.command(name="remove")
    async def pokegifs_remove(self, ctx: commands.Context, url: str):
        """Removes a GIF URL from the poke list."""
        settings = await self._get_settings(ctx.guild.id)
        
        try:
            settings.poke_gifs.remove(url)
            await self._set_settings(ctx.guild.id, settings)
            await ctx.send(f"Removed Poke GIF: <{url}>")
        except ValueError:
            await ctx.send("That GIF URL was not found in the list.")


    @ouijaset.group(name="summongifs", invoke_without_command=True)
    async def ouijaset_summongifs(self, ctx: commands.Context):
        """
        Manages the list of GIFs used for the 'summon' command.
        
        Use `[p]ouijaset summongifs add <url>` or `[p]ouijaset summongifs remove <url>`
        """
        settings = await self._get_settings(ctx.guild.id)
        gifs = settings.summon_gifs
        if not gifs:
            msg = "There are currently no Summon GIFs configured."
        else:
            gif_list = "\n".join(f"`{i+1}.` <{g}>" for i, g in enumerate(gifs))
            msg = f"**Current Summon GIFs ({len(gifs)} total):**\n{gif_list}"
        
        await ctx.send(msg)

    @ouijaset_summongifs.command(name="add")
    async def summongifs_add(self, ctx: commands.Context, url: str):
        """Adds a new GIF URL to the summon list."""
        if not self._is_valid_gif_url(url):
            return await ctx.send("That doesn't look like a valid GIF URL. Make sure it ends with `.gif` or a common animated extension.")
        
        settings = await self._get_settings(ctx.guild.id)
        if url in settings.summon_gifs:
            return await ctx.send("That GIF is already in the list.")
        
        settings.summon_gifs.append(url)
        await self._set_settings(ctx.guild.id, settings)
        await ctx.send(f"Added new Summon GIF: <{url}>")

    @ouijaset_summongifs.command(name="remove")
    async def summongifs_remove(self, ctx: commands.Context, url: str):
        """Removes a GIF URL from the summon list."""
        settings = await self._get_settings(ctx.guild.id)
        
        try:
            settings.summon_gifs.remove(url)
            await self._set_settings(ctx.guild.id, settings)
            await ctx.send(f"Removed Summon GIF: <{url}>")
        except ValueError:
            await ctx.send("That GIF URL was not found in the list.")


    # --- Last Seen Override Command ---

    @ouijaset.command(name="override")
    async def ouijaset_override(self, ctx: commands.Context, role: discord.Role, days_ago: int):
        """
        Overrides the last active date for all members of a given role.

        Example: `[p]ouijaset override @Spirits 60` 
        Sets everyone with the @Spirits role to last active 60 days ago.
        """
        if days_ago < 0:
            return await ctx.send("The number of days must be 0 or greater.")
        
        await ctx.trigger_typing()
        
        target_last_active_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        target_last_active_dt_str = target_last_active_dt.isoformat()
        
        last_seen_data = await self.config.guild(ctx.guild.id).last_seen()
        
        updated_count = 0
        
        for member in role.members:
            if member.bot:
                continue
            
            last_seen_data[str(member.id)] = target_last_active_dt_str
            updated_count += 1
            
        await self.config.guild(ctx.guild.id).last_seen.set(last_seen_data)
        
        await ctx.send(
            f"The Ouija spirits have whispered that **{updated_count}** members "
            f"in the **{role.name}** role were last seen **{days_ago} days ago** "
            f"({target_last_active_dt.strftime('%Y-%m-%d %H:%M:%S UTC')})."
        )

# --- Red Setup Function ---

async def setup(bot):
    await bot.add_cog(OuijaPoke(bot))