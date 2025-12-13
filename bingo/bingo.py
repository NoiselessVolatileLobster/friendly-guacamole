import asyncio
import functools
import random
import re
import sys
import textwrap
from io import BytesIO
from typing import List, Optional, Pattern, Tuple, Dict, Any

import aiohttp
import discord
from PIL import Image, ImageColor, ImageDraw, ImageFont
from red_commons.logging import getLogger
from redbot.core import Config, commands, bank
from redbot.core.data_manager import bundled_data_path, cog_data_path

from .converter import Stamp

log = getLogger("red.trusty-cogs.bingo")

IMAGE_LINKS: Pattern = re.compile(
    r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg)(\?size=[0-9]*)?)", flags=re.I
)

# --- Game Type Definitions ---
GAME_TYPES: Dict[str, str] = {
    "STANDARD": "Any horizontal, vertical, or diagonal line.",
    "HORIZONTAL": "Any complete horizontal line.",
    "VERTICAL": "Any complete vertical line.",
    "X_PATTERN": "Both diagonal lines must be completed (form an 'X').",
    "COVERALL": "All 25 squares must be stamped (Blackout).",
}

class NewGameView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="Start New Game & Deal Cards", style=discord.ButtonStyle.success, emoji="軸")
    async def start_new_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if active tileset is valid before resetting
        active_set_name = await self.cog.config.guild(interaction.guild).active_tileset()
        tilesets = await self.cog.config.guild(interaction.guild).tilesets()
        
        if active_set_name not in tilesets or len(tilesets[active_set_name]) < 24:
             await interaction.response.send_message(
                f"Cannot start game: The active tileset '{active_set_name}' has fewer than 24 tiles.",
                ephemeral=True
            )
             return

        # Reset the game state (new seed, clear cards)
        await self.cog.reset_game_state(interaction.guild)
        
        # Disable the button so it can't be clicked again
        button.disabled = True
        button.label = "Game Started"
        button.style = discord.ButtonStyle.secondary
        await interaction.message.edit(view=self)
        
        # Announce the new game
        await interaction.response.send_message(
            f"売 **{interaction.user.display_name}** has started a new game using the **{active_set_name}** tileset! All cards have been reset and reshuffled.",
        )
        self.stop()

class Bingo(commands.Cog):
    __version__ = "1.3.0"
    __author__ = ["TrustyJAID"]

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 218773382617890828)
        self.config.register_guild(
            tiles=[], # Deprecated, kept for migration
            tilesets={"Standard": []}, # New system: Dict[name, List[str]]
            active_tileset="Standard",
            stamp_colour="#e6d705",
            text_colour="#FFF8E7",
            textborder_colour="#333333",
            background_colour="#010028",
            box_colour="#FFF8E7",
            watermark=None,
            icon=None,
            background_tile=None,
            name="",
            bingo="BINGO",
            seed=0,
            bank_prize=0,
            game_type="STANDARD",
        )
        self.config.register_member(stamps=[])

    async def cog_load(self):
        """Perform migration of old tiles to the new tileset system."""
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            
            # Check if old tiles exist
            old_tiles = await self.config.guild(guild).tiles()
            tilesets = await self.config.guild(guild).tilesets()
            
            # If we have old tiles, but 'Standard' is empty, migrate them
            if old_tiles and not tilesets.get("Standard"):
                tilesets["Standard"] = old_tiles
                await self.config.guild(guild).tilesets.set(tilesets)
                # Clear the old list to avoid re-migration
                await self.config.guild(guild).tiles.set([])
                log.info(f"Migrated {len(old_tiles)} tiles to 'Standard' tileset for guild {guild.name}")

    async def red_delete_data_for_user(self, **kwargs):
        """
        Nothing to delete. Information saved is a set of points on a bingo card and
        does not represent end user data.
        """
        return

    async def reset_game_state(self, guild: discord.Guild):
        """Resets the game state: new seed and clears member stamps."""
        new_seed = random.randint(100000, 999999)
        await self.config.guild(guild).seed.set(new_seed)
        await self.config.clear_all_members(guild=guild)

    @commands.group(name="bingoset")
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def bingoset(self, ctx: commands.Context):
        """
        Commands for setting bingo settings
        """
        pass

    # --- New Tileset Management Group ---
    @bingoset.group(name="tileset", aliases=["tiles"])
    async def bingoset_tileset(self, ctx: commands.Context):
        """
        Manage Bingo tilesets.
        
        Tilesets allow you to have different themes (e.g. 'Standard', 'Christmas')
        that you can swap between.
        """
        pass

    @bingoset_tileset.command(name="list")
    async def tileset_list(self, ctx: commands.Context):
        """List all available tilesets."""
        tilesets = await self.config.guild(ctx.guild).tilesets()
        active = await self.config.guild(ctx.guild).active_tileset()
        
        if not tilesets:
            return await ctx.send("No tilesets found.")

        msg = "## Available Tilesets\n"
        for name, tiles in tilesets.items():
            status = " (Active)" if name == active else ""
            msg += f"* **{name}**: {len(tiles)} tiles{status}\n"
        
        await ctx.send(msg)

    @bingoset_tileset.command(name="add", aliases=["create"])
    async def tileset_add(self, ctx: commands.Context, name: str):
        """Create a new empty tileset."""
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if name in tilesets:
            return await ctx.send(f"A tileset named `{name}` already exists.")
        
        tilesets[name] = []
        await self.config.guild(ctx.guild).tilesets.set(tilesets)
        await ctx.send(f"Created new tileset: **{name}**. Use `{ctx.clean_prefix}bingoset tileset addtiles {name} <tiles>` to populate it.")

    @bingoset_tileset.command(name="delete", aliases=["remove"])
    async def tileset_delete(self, ctx: commands.Context, name: str):
        """Delete a tileset."""
        tilesets = await self.config.guild(ctx.guild).tilesets()
        active = await self.config.guild(ctx.guild).active_tileset()

        if name not in tilesets:
            return await ctx.send(f"Tileset `{name}` does not exist.")
        
        if name == active:
            return await ctx.send(f"You cannot delete the active tileset. Load a different one first using `{ctx.clean_prefix}bingoset tileset load`.")

        del tilesets[name]
        await self.config.guild(ctx.guild).tilesets.set(tilesets)
        await ctx.send(f"Deleted tileset: **{name}**.")

    @bingoset_tileset.command(name="load", aliases=["active"])
    async def tileset_load(self, ctx: commands.Context, name: str):
        """Set the active tileset for the next game."""
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if name not in tilesets:
            return await ctx.send(f"Tileset `{name}` does not exist.")
        
        await self.config.guild(ctx.guild).active_tileset.set(name)
        # We also reset the game state because switching tiles invalidates current cards
        await self.reset_game_state(ctx.guild)
        await ctx.send(f"**{name}** is now the active tileset! The game board has been reset.")

    @bingoset_tileset.command(name="addtiles")
    async def tileset_addtiles(self, ctx: commands.Context, name: str, *, tiles: str):
        """
        Add tiles to a specific tileset.
        
        `name`: The name of the tileset.
        `tiles`: Semicolon separated list of tiles (e.g. "Tile 1; Tile 2").
        """
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if name not in tilesets:
            return await ctx.send(f"Tileset `{name}` does not exist.")

        new_tiles = [t.strip() for t in tiles.split(";") if t.strip()]
        tilesets[name].extend(new_tiles)
        
        # Remove duplicates while preserving order? No, set doesn't preserve order. 
        # Just sort them to be clean.
        tilesets[name] = sorted(list(set(tilesets[name])))
        
        await self.config.guild(ctx.guild).tilesets.set(tilesets)
        await ctx.send(f"Added {len(new_tiles)} tiles to **{name}**. Total tiles: {len(tilesets[name])}.")

    @bingoset_tileset.command(name="removetiles")
    async def tileset_removetiles(self, ctx: commands.Context, name: str, *, tiles: str):
        """
        Remove tiles from a specific tileset.
        
        `name`: The name of the tileset.
        `tiles`: Semicolon separated list of tiles to remove (exact match).
        """
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if name not in tilesets:
            return await ctx.send(f"Tileset `{name}` does not exist.")

        to_remove = [t.strip() for t in tiles.split(";") if t.strip()]
        original_count = len(tilesets[name])
        
        tilesets[name] = [t for t in tilesets[name] if t not in to_remove]
        removed_count = original_count - len(tilesets[name])
        
        await self.config.guild(ctx.guild).tilesets.set(tilesets)
        await ctx.send(f"Removed {removed_count} tiles from **{name}**.")

    @bingoset_tileset.command(name="view", aliases=["show"])
    async def tileset_view(self, ctx: commands.Context, name: Optional[str] = None):
        """
        View tiles in a tileset. Defaults to active set if name not provided.
        """
        if name is None:
            name = await self.config.guild(ctx.guild).active_tileset()
            
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if name not in tilesets:
            return await ctx.send(f"Tileset `{name}` does not exist.")
            
        tiles = tilesets[name]
        if not tiles:
            return await ctx.send(f"Tileset **{name}** is empty.")
            
        # Chunk text to avoid hitting discord limits
        msg = f"**Tiles in {name}** ({len(tiles)}):\n"
        tile_str = "; ".join(tiles)
        
        for page in textwrap.wrap(tile_str, 1900):
             await ctx.send(page)

    # --- End New Tileset Management Group ---

    @bingoset.command(name="stamp")
    async def bingoset_stamp(self, ctx: commands.Context, colour: Optional[discord.Colour] = None):
        """
        Set the colour of the "stamp" that fills the box.

        `colour` - must be a hex colour code
        """
        if colour is None:
            await self.config.guild(ctx.guild).stamp_colour.clear()
        else:
            await self.config.guild(ctx.guild).stamp_colour.set(str(colour))
        colour = await self.config.guild(ctx.guild).stamp_colour()
        await ctx.send(f"The Bingo card stamp has been set to {colour}")

    @bingoset.command(name="name")
    async def bingoset_name(self, ctx: commands.Context, *, name: str):
        """
        Set the name of the current bingo card.

        `name` - the name you want to use for the current bingo card.
        """
        await self.config.guild(ctx.guild).name.set(name)
        await ctx.send(f"The Bingo card name has been set to {name}")

    @bingoset.command(name="text")
    async def bingoset_text(self, ctx: commands.Context, colour: Optional[discord.Colour] = None):
        """
        Set the colour of the text.

        `colour` - must be a hex colour code
        """
        if colour is None:
            await self.config.guild(ctx.guild).text_colour.clear()
        else:
            await self.config.guild(ctx.guild).text_colour.set(str(colour))
        colour = await self.config.guild(ctx.guild).text_colour()
        await ctx.send(f"The Bingo card text has been set to {colour}")

    @bingoset.command(name="background")
    async def bingoset_background(
        self, ctx: commands.Context, colour: Optional[discord.Colour] = None
    ):
        """
        Set the colour of the Bingo card background.

        `colour` - must be a hex colour code
        """
        if colour is None:
            await self.config.guild(ctx.guild).background_colour.clear()
        else:
            await self.config.guild(ctx.guild).background_colour.set(str(colour))
        colour = await self.config.guild(ctx.guild).background_colour()
        await ctx.send(f"The Bingo card background has been set to {colour}")

    @bingoset.command(name="textborder")
    async def bingoset_text_border(
        self, ctx: commands.Context, colour: Optional[discord.Colour] = None
    ):
        """
        Set the colour of the text border.

        `colour` - must be a hex colour code
        """
        if colour is None:
            await self.config.guild(ctx.guild).textborder_colour.clear()
        else:
            await self.config.guild(ctx.guild).textborder_colour.set(str(colour))
        colour = await self.config.guild(ctx.guild).textborder_colour()
        await ctx.send(f"The Bingo card text border has been set to {colour}")

    @bingoset.command(name="box")
    async def bingoset_box(self, ctx: commands.Context, colour: Optional[discord.Colour] = None):
        """
        Set the colour of the Bingo card boxes border.

        `colour` - must be a hex colour code
        """
        if colour is None:
            await self.config.guild(ctx.guild).box_colour.clear()
        else:
            await self.config.guild(ctx.guild).box_colour.set(str(colour))
        colour = await self.config.guild(ctx.guild).box_colour()
        await ctx.send(f"The Bingo card box colour has been set to {colour}")

    @bingoset.command(name="bingo")
    async def bingoset_bingo(self, ctx: commands.Context, bingo: str):
        """
        Set the "BINGO" of the board.

        `bingo` - The word to use for bingo. Must be exactly 5 characters.
        """
        if len(set(list(bingo))) != 5:
            await ctx.send(
                "The 'BINGO' must be exactly 5 characters and contain no identical characters."
            )
            return
        await self.config.guild(ctx.guild).bingo.set(bingo.upper())
        await ctx.send(f"The 'BINGO' has been set to `{bingo.upper()}`")

    @bingoset.command(name="watermark")
    async def bingoset_watermark(self, ctx: commands.Context, image_url: Optional[str] = None):
        """
        Add a watermark image to the bingo card

        `[image_url]` - Must be an image url with `.jpg` or `.png` extension.
        """
        if image_url is None and not ctx.message.attachments:
            await self.config.guild(ctx.guild).watermark.clear()
            await ctx.send("I have cleared the bingo watermark.")
            return
        elif image_url is None and ctx.message.attachments:
            image = ctx.message.attachments[0]
            ext = image.filename.split(".")[-1]
            filename = f"{ctx.guild.id}-watermark.{ext}"
            await image.save(cog_data_path(self) / filename)
            await self.config.guild(ctx.guild).watermark.set(filename)
            await ctx.send("Saved the image as a watermark.")
        else:
            if not IMAGE_LINKS.search(image_url):
                await ctx.send("That is not a valid image URL. It must be either jpg or png.")
                return
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    data = await resp.read()
            ext = image_url.split(".")[-1]
            filename = f"{ctx.guild.id}-watermark.{ext}"
            with open(cog_data_path(self) / filename, "wb") as outfile:
                outfile.write(data)
            await self.config.guild(ctx.guild).watermark.set(filename)
            await ctx.send("Saved the image as a watermark.")

    @bingoset.command(name="icon")
    async def bingoset_icon(self, ctx: commands.Context, image_url: Optional[str] = None):
        """
        Add an icon image to the bingo card

        `[image_url]` - Must be an image url with `.jpg` or `.png` extension.
        """
        if image_url is None and not ctx.message.attachments:
            await self.config.guild(ctx.guild).icon.clear()
            await ctx.send("I have cleared the bingo icon.")
            return
        elif image_url is None and ctx.message.attachments:
            image = ctx.message.attachments[0]
            ext = image.filename.split(".")[-1]
            filename = f"{ctx.guild.id}-icon.{ext}"
            await image.save(cog_data_path(self) / filename)
            await self.config.guild(ctx.guild).icon.set(filename)
            await ctx.send("Saved the image as an icon.")
        else:
            if not IMAGE_LINKS.search(image_url):
                await ctx.send("That is not a valid image URL. It must be either jpg or png.")
                return
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    data = await resp.read()
            ext = image_url.split(".")[-1]
            filename = f"{ctx.guild.id}-icon.{ext}"
            with open(cog_data_path(self) / filename, "wb") as outfile:
                outfile.write(data)
            await self.config.guild(ctx.guild).icon.set(filename)
            await ctx.send("Saved the image as an icon.")

    @bingoset.command(name="bgtile")
    async def bingoset_bgtile(self, ctx: commands.Context, image_url: Optional[str] = None):
        """
        Set the background image (tiled).

        This will override the background colour if set as it will attempt
        to tile the image over the entire background.

        `[image_url]` - Must be an image url with `.jpg` or `.png` extension.
        """
        if image_url is None and not ctx.message.attachments:
            await self.config.guild(ctx.guild).background_tile.clear()
            await ctx.send("I have cleared the bingo background image.")
            return
        elif image_url is None and ctx.message.attachments:
            image = ctx.message.attachments[0]
            ext = image.filename.split(".")[-1]
            filename = f"{ctx.guild.id}-bgtile.{ext}"
            await image.save(cog_data_path(self) / filename)
            await self.config.guild(ctx.guild).background_tile.set(filename)
            await ctx.send("Saved the image as an background tile.")
            return
        else:
            if not IMAGE_LINKS.search(image_url):
                await ctx.send("That is not a valid image URL. It must be either jpg or png.")
                return
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    data = await resp.read()
            ext = image_url.split(".")[-1]
            filename = f"{ctx.guild.id}-bgtile.{ext}"
            with open(cog_data_path(self) / filename, "wb") as outfile:
                outfile.write(data)
            await self.config.guild(ctx.guild).background_tile.set(filename)
            await ctx.send("Saved the image as the background tile.")

    @bingoset.command(name="bankprize")
    async def bingoset_bankprize(self, ctx: commands.Context, amount: int = None):
        """
        Set the bank prize awarded to the user who gets a bingo.

        Set to 0 to disable. Requires the Economy cog to be loaded.
        """
        # Using the standard bank module for currency name
        currency_name = await bank.get_currency_name(ctx.guild)

        if amount is None:
            prize = await self.config.guild(ctx.guild).bank_prize()
            return await ctx.send(
                f"The current bank prize for bingo is **{prize}** {currency_name}. "
                f"Use `{ctx.prefix}bingoset bankprize <amount>` to change it."
            )
        
        if amount < 0:
            return await ctx.send("The prize amount must be 0 or greater.")
        
        await self.config.guild(ctx.guild).bank_prize.set(amount)
        if amount == 0:
            await ctx.send("The bank prize for bingo has been **disabled**.")
        else:
            await ctx.send(
                f"The bank prize for bingo has been set to **{amount}** {currency_name}."
            )

    @bingoset.command(name="gametype")
    async def bingoset_gametype(self, ctx: commands.Context, game_type: Optional[str] = None):
        """
        Set the win condition (game type) for the current bingo game.

        Available types:
        - `STANDARD`: Any horizontal, vertical, or diagonal line. (Default)
        - `HORIZONTAL`: Any complete horizontal line.
        - `VERTICAL`: Any complete vertical line.
        - `X_PATTERN`: Both diagonal lines must be completed.
        - `COVERALL`: All 25 squares must be stamped (Blackout).
        """
        game_types = GAME_TYPES.keys()

        if game_type is None:
            current_type = await self.config.guild(ctx.guild).game_type()
            current_desc = GAME_TYPES.get(current_type, "Unknown Type")
            
            msg = f"The current game type is **{current_type}** ({current_desc}).\n\n"
            msg += "Available game types:\n"
            for k, v in GAME_TYPES.items():
                msg += f"`{k}`: {v}\n"
            return await ctx.send(msg)
            
        game_type = game_type.upper()
        if game_type not in game_types:
            return await ctx.send(
                f"Invalid game type. Choose one of: {', '.join(game_types)}"
            )

        await self.config.guild(ctx.guild).game_type.set(game_type)
        await ctx.send(
            f"The game type has been set to **{game_type}** ({GAME_TYPES[game_type]})."
        )
            
    @commands.command(name="newbingo")
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def newbingo(self, ctx: commands.Context):
        """
        Starts a new game of bingo by resetting all player cards and shuffling
        the tiles for everyone using a new random seed.
        """
        # Retrieve Active Tileset Info
        active_set_name = await self.config.guild(ctx.guild).active_tileset()
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if active_set_name not in tilesets:
            # Fallback for safety, though should be handled by migration
            tilesets["Standard"] = []
            await self.config.guild(ctx.guild).tilesets.set(tilesets)
            await self.config.guild(ctx.guild).active_tileset.set("Standard")
            active_set_name = "Standard"

        tiles = tilesets[active_set_name]

        if len(tiles) < 24:
            return await ctx.send(
                f"The active tileset '{active_set_name}' has fewer than 24 tiles. "
                f"Please add more tiles using `{ctx.clean_prefix}bingoset tileset addtiles` before starting a game."
            )

        await self.reset_game_state(ctx.guild)
        
        new_seed = await self.config.guild(ctx.guild).seed()
        
        await ctx.send(
            f"脂 Starting a new bingo game using the **{active_set_name}** tileset! "
            f"All player cards have been reset, and a new card arrangement has been generated using seed `{new_seed}`."
        )


    @bingoset.command(name="reset")
    async def bingoset_reset(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """
        Reset a users bingo card or reset the whole servers bingo card.
        """
        if member is None:
            await self.config.clear_all_members(guild=ctx.guild)
            await ctx.send("Resetting everyone's bingo card.")
        else:
            await self.config.member(member).clear()
            await ctx.send(
                f"Resetting {member.mention}'s bingo card.",
                allowed_mentions=discord.AllowedMentions(users=False),
            )

    @bingoset.command(name="clear")
    async def bingoset_clear(self, ctx: commands.Context):
        """
        Clear out the ACTIVE tileset's tiles.
        """
        active_name = await self.config.guild(ctx.guild).active_tileset()
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if active_name in tilesets:
            tilesets[active_name] = []
            await self.config.guild(ctx.guild).tilesets.set(tilesets)
            await ctx.send(f"I have cleared the tiles for the active tileset: **{active_name}**.")
        else:
             await ctx.send("Active tileset not found in configuration.")


    @bingoset.command(name="seed")
    async def bingoset_seed(self, ctx: commands.Context, seed: int):
        """
        Set an additional seed to the randomness of players cards.

        `seed` - A number that is added to the player ID used to
        seed their card.

        Use this to shuffle everyone's card while keeping the exact
        same tiles for a game of bingo. Default is 0.
        """
        if seed >= sys.maxsize >> 1 or seed <= (-1 * sys.maxsize >> 1):
            await ctx.send("That seed is too large, choose a smaller number.")
            return
        await self.config.guild(ctx.guild).seed.set(seed)
        await ctx.send("I have saved the additional seed to the players cards.")

    @bingoset.command(name="settings")
    async def bingoset_settings(self, ctx: commands.Context):
        """
        Show the current bingo card settings
        """
        settings = await self.get_card_options(ctx)
        
        # Tiles info
        active_set_name = await self.config.guild(ctx.guild).active_tileset()
        tilesets = await self.config.guild(ctx.guild).tilesets()
        tiles_count = len(tilesets.get(active_set_name, []))

        bank_prize = await self.config.guild(ctx.guild).bank_prize()
        game_type = await self.config.guild(ctx.guild).game_type()
        
        # Using the standard bank module for currency name
        currency_name = await bank.get_currency_name(ctx.guild)
        
        msg = f"Active Tileset: `{active_set_name}`\n"
        msg += f"Tiles in Active Set: `{tiles_count}`\n"
        msg += f"Game Type: `{game_type}` ({GAME_TYPES.get(game_type, 'Unknown Type')})\n"
        msg += f"Bank Prize: `{bank_prize} {currency_name}`\n"

        for k, v in settings.items():
            if k in ["bank_prize", "seed", "game_type"]: 
                continue
            
            if k == "watermark":
                v = await self.config.guild(ctx.guild).watermark()
            if k == "icon":
                v = await self.config.guild(ctx.guild).icon()
            if k == "background_tile":
                v = await self.config.guild(ctx.guild).background_tile()
                
            name = k.replace("_", " ").title()
            msg += f"{name}: `{v}`\n"
        await ctx.maybe_send_embed(msg)

    async def check_stamps(self, stamps: List[List[int]], guild: discord.Guild) -> bool:
        """
        Checks if the users current stamps warrants a bingo based on the configured game type.
        """
        game_type = await self.config.guild(guild).game_type()
        
        # --- COVERALL Check ---
        if game_type == "COVERALL":
            # Coverall requires all 24 non-free space tiles to be stamped. Free space is implicitly covered.
            return len(stamps) == 24
            
        # --- LINE-BASED Checks (STANDARD, HORIZONTAL, VERTICAL, X_PATTERN) ---
        
        # 1. Prepare results dictionary
        results = {
            "x": {0: 0, 1: 0, 2: 0, 3: 0, 4: 0},
            "y": {0: 0, 1: 0, 2: 0, 3: 0, 4: 0},
            "right_diag": 0,
            "left_diag": 0,
        }
        
        # 2. Get all stamped coordinates, ensuring uniqueness and including the Free Space ([2, 2])
        # Convert list of lists to a set of tuples for easy uniqueness check, then iterate
        all_stamps_set = set(tuple(s) for s in stamps)
        all_stamps_set.add((2, 2))
        
        # 3. Calculate line results
        for stamp_tuple in all_stamps_set:
            x, y = stamp_tuple
            results["x"][x] += 1
            results["y"][y] += 1
            
            # Diagonal checks
            if x == y: # right_diag (0,0) to (4,4)
                results["right_diag"] += 1
            if x + y == 4: # left_diag (4,0) to (0,4)
                results["left_diag"] += 1

        # 4. Evaluate against game type
        if game_type == "STANDARD":
            return (
                results["right_diag"] == 5 or 
                results["left_diag"] == 5 or 
                any(i == 5 for i in results["x"].values()) or 
                any(i == 5 for i in results["y"].values())
            )
        elif game_type == "HORIZONTAL":
            return any(i == 5 for i in results["y"].values())
        elif game_type == "VERTICAL":
            return any(i == 5 for i in results["x"].values())
        elif game_type == "X_PATTERN":
            # Requires both diagonal lines to be completed
            return results["right_diag"] == 5 and results["left_diag"] == 5
            
        return False

    @commands.command(name="bingo", aliases=["stamp"])
    @commands.guild_only()
    @commands.bot_has_permissions(attach_files=True)
    async def bingo(self, ctx: commands.Context, stamp: Optional[Stamp] = None):
        """
        Generate a Bingo Card

        `stamp` - Select the tile that you would like to stamp. If not
        provided will just show your current bingo card.
        """
        # Fetching settings from config
        active_set_name = await self.config.guild(ctx.guild).active_tileset()
        tilesets = await self.config.guild(ctx.guild).tilesets()
        
        if active_set_name not in tilesets:
             return await ctx.send("Error: The active tileset was not found. Please contact an admin.")
             
        tiles = tilesets[active_set_name]
        
        if len(tiles) < 24:
            return await ctx.send(
                f"The active tileset '{active_set_name}' has fewer than 24 tiles. "
                "An admin needs to add more tiles before cards can be generated."
            )

        stamps = await self.config.member(ctx.author).stamps()
        bank_prize = await self.config.guild(ctx.guild).bank_prize()
        msg = None
        win_embed = None
        view = None
        
        # Step 1: Handle stamping/unstamping logic
        if stamp is not None:
            # Assuming stamp is a List[int] from the converter: [x, y]
            if stamp in stamps:
                stamps.remove(stamp)
                msg = f"Unstamped tile **{ctx.message.clean_content.split()[-1].upper()}**."
            else:
                stamps.append(stamp)
                msg = f"Stamped tile **{ctx.message.clean_content.split()[-1].upper()}**."

            await self.config.member(ctx.author).stamps.set(stamps)
            
        # Step 2: Check for a BINGO win
        if await self.check_stamps(stamps, ctx.guild):
            # Create Win Embed
            win_embed = discord.Embed(
                title="圷 BINGO! 圷", 
                description=f"Congratulations **{ctx.author.display_name}**! You have won the game!", 
                color=discord.Color.green()
            )
            win_embed.set_thumbnail(url=ctx.author.display_avatar.url)
            
            # --- STANDARD BANK INTEGRATION LOGIC ---
            if bank_prize > 0:
                try:
                    # Using the standard Red V3 bank module, as confirmed by your working snippet
                    await bank.deposit_credits(ctx.author, bank_prize)
                    currency = await bank.get_currency_name(ctx.guild)
                    win_embed.add_field(name="Prize Won", value=f"醇 **{bank_prize}** {currency}", inline=False)
                except Exception as e:
                    # This will catch issues like Economy not being loaded or transaction failures
                    log.error("Failed to deposit bank prize using redbot.core.bank.", exc_info=True)
                    win_embed.add_field(name="Prize Error", value="Could not deposit credits. Please contact an admin.", inline=False)
            # --- END STANDARD BANK INTEGRATION LOGIC ---
            
            # Add Footer and View
            win_embed.set_footer(text="Click the button below to start a new game!")
            view = NewGameView(self)
        
        # Step 3: Generate and send the card
        seed = int(await self.config.guild(ctx.guild).seed()) + ctx.author.id
        random.seed(seed)
        
        # We must assume the list of tiles is > 24, which we checked earlier.
        # Create a local copy to shuffle
        tiles_to_shuffle = tiles.copy()
        random.shuffle(tiles_to_shuffle)
        
        card_settings = await self.get_card_options(ctx)
        
        temp = await self.create_bingo_card(
            tiles_to_shuffle, stamps=stamps, guild_name=ctx.guild.name, **card_settings
        )
        
        # Send the message and the generated card image
        # If there is a win, we attach the embed and view.
        await ctx.send(
            content=msg,
            file=temp,
            embed=win_embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=False),
        )

    async def get_card_options(self, ctx: commands.Context) -> dict:
        ret = {
            "background_colour": await self.config.guild(ctx.guild).background_colour(),
            "text_colour": await self.config.guild(ctx.guild).text_colour(),
            "textborder_colour": await self.config.guild(ctx.guild).textborder_colour(),
            "stamp_colour": await self.config.guild(ctx.guild).stamp_colour(),
            "box_colour": await self.config.guild(ctx.guild).box_colour(),
            "name": await self.config.guild(ctx.guild).name(),
            "bingo": await self.config.guild(ctx.guild).bingo(),
            "seed": await self.config.guild(ctx.guild).seed(),
            "bank_prize": await self.config.guild(ctx.guild).bank_prize(),
            "game_type": await self.config.guild(ctx.guild).game_type(),
        }
        if watermark := await self.config.guild(ctx.guild).watermark():
            ret["watermark"] = Image.open(cog_data_path(self) / watermark)
        if icon := await self.config.guild(ctx.guild).icon():
            ret["icon"] = Image.open(cog_data_path(self) / icon)
        if background_tile := await self.config.guild(ctx.guild).background_tile():
            ret["background_tile"] = Image.open(cog_data_path(self) / background_tile)
        return ret

    async def create_bingo_card(
        self,
        tiles: List[str],
        name: str,
        guild_name: str,
        bingo: str,
        background_colour: str,
        text_colour: str,
        textborder_colour: str,
        stamp_colour: str,
        box_colour: str,
        watermark: Optional[Image.Image] = None,
        icon: Optional[Image.Image] = None,
        background_tile: Optional[Image.Image] = None,
        stamps: List[Tuple[int, int]] = [],
        seed: int = 0,
        bank_prize: int = 0,
        game_type: str = "STANDARD",
    ) -> Optional[discord.File]:
        task = functools.partial(
            self._create_bingo_card,
            options=tiles,
            name=name,
            guild_name=guild_name,
            bingo=bingo,
            background_colour=background_colour,
            text_colour=text_colour,
            textborder_colour=textborder_colour,
            stamp_colour=stamp_colour,
            box_colour=box_colour,
            watermark=watermark,
            icon=icon,
            background_tile=background_tile,
            stamps=stamps,
            seed=seed,
            bank_prize=bank_prize,
            game_type=game_type,
        )
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, task)
        try:
            return await asyncio.wait_for(task, timeout=60)
        except asyncio.TimeoutError:
            log.error("There was an error generating the bingo card")
            return None

    def _create_bingo_card(
        self,
        options: List[str],
        name: str,
        guild_name: str,
        bingo: str,
        background_colour: str,
        text_colour: str,
        textborder_colour: str,
        stamp_colour: str,
        box_colour: str,
        watermark: Optional[Image.Image] = None,
        icon: Optional[Image.Image] = None,
        background_tile: Optional[Image.Image] = None,
        stamps: List[Tuple[int, int]] = [],
        seed: int = 0,
        bank_prize: int = 0,
        game_type: str = "STANDARD",
    ):
        base_height, base_width = 1000, 700
        base = Image.new("RGBA", (base_width, base_height), color=background_colour)
        draw = ImageDraw.Draw(base)
        if background_tile:
            # https://stackoverflow.com/a/69807463
            bg_x, bg_y = background_tile.size
            for i in range(0, base_width, bg_x):
                for j in range(0, base_height, bg_y):
                    base.paste(background_tile, (i, j))
        font_path = str(bundled_data_path(self) / "SourceSansPro-SemiBold.ttf")
        font = ImageFont.truetype(font=font_path, size=180)
        font2 = ImageFont.truetype(font=font_path, size=20)
        font3 = ImageFont.truetype(font=font_path, size=30)
        credit_font = ImageFont.truetype(font=font_path, size=10)
        draw.text(
            (690, 975),
            f"Bingo Cog written by @trustyjaid\nBingo card colours and images provided by {guild_name} moderators",
            fill=text_colour,
            stroke_width=1,
            align="right",
            stroke_fill=textborder_colour,
            anchor="rs",
            font=credit_font,
        )
        if watermark is not None:
            watermark = watermark.convert("RGBA")
            # https://stackoverflow.com/a/72983761
            wm = watermark.copy()
            wm.putalpha(128)
            watermark.paste(wm, watermark)
            # watermark.putalpha(128)

            # https://stackoverflow.com/a/56868633
            x1 = int(0.5 * base.size[0]) - int(0.5 * watermark.size[0])
            y1 = int(0.5 * base.size[1]) - int(0.5 * watermark.size[1])
            x2 = int(0.5 * base.size[0]) + int(0.5 * watermark.size[0])
            y2 = int(0.5 * base.size[1]) + int(0.5 * watermark.size[1])
            base.alpha_composite(watermark, (x1, y1))
        if icon is not None:
            icon = icon.convert("RGBA")
            icon.thumbnail((90, 90), Image.LANCZOS)
            base.paste(icon, (305, 905), icon)

        letter_count = 0
        for letter in bingo:
            scale = 130
            letter_x = 85 + (scale * letter_count)
            letter_y = 150
            draw.text(
                (letter_x, letter_y),
                letter,
                fill=text_colour,
                stroke_width=4,
                stroke_fill=textborder_colour,
                anchor="ms",
                font=font,
            )
            letter_count += 1
        log.trace("_create_bingo_card name: %s", name)
        draw.text(
            (350, 200),
            name,
            fill=text_colour,
            stroke_width=1,
            stroke_fill="black",
            anchor="ms",
            font=font3,
        )
        count = 0
        for x in range(5):
            for y in range(5):
                scale = 130
                x0 = 25 + (scale * x)
                x1 = x0 + scale
                y0 = 250 + (scale * y)
                y1 = y0 + scale
                if x == 2 and y == 2:
                    text = "Free Space"
                else:
                    try:
                        text = options[count]
                    except IndexError:
                        text = "Free Space"
                    count += 1
                draw.rectangle((x0, y0, x1, y1), outline=box_colour)
                
                # Check for stamp, including the free space implicitly
                is_stamped = [x, y] in stamps or [x, y] == [2, 2]
                
                if is_stamped:
                    log.info("Filling square %s %s", x, y)
                    colour = list(ImageColor.getrgb(stamp_colour))
                    colour.append(128)
                    nb = base.copy()
                    nd = ImageDraw.Draw(nb)
                    nd.ellipse((x0 + 5, y0 + 5, x1 - 5, y1 - 5), fill=tuple(colour))
                    base.alpha_composite(nb, (0, 0))

                if len(text) > 60:
                    text = text[:57] + "..."

                lines = textwrap.wrap(text, width=13)
                font_height = font2.getbbox(text)[3] - font2.getbbox(text)[1]
                text_x = x0 + int(scale / 2)
                if len(lines) > 1:
                    text_y = y0 + (int(scale / 2) - ((len(lines) / 3) * font_height))
                else:
                    text_y = y0 + (int(scale / 2))

                for line in lines:
                    draw.text(
                        (text_x, text_y),
                        line,
                        fill=text_colour,
                        stroke_width=1,
                        stroke_fill=textborder_colour,
                        anchor="ms",
                        font=font2,
                    )
                    text_y += font_height
        
        # --- Draw Vertical Row Numbers (1-5) ---
        for y in range(5):
            scale = 130
            number = str(y + 1)  # 1-based index (1 to 5)
            
            # Position to the left of the B column
            text_x = 15 
            
            # Center the number vertically in the row
            text_y = 250 + (scale * y) + (scale / 2) 

            draw.text(
                (text_x, text_y),
                number,
                fill=text_colour,
                stroke_width=2,
                stroke_fill=textborder_colour,
                anchor="lm", # Left-justified, vertically centered ('middle')
                font=font3, # Use the size 30 font
            )
        # --- END DRAW VERTICAL ROW NUMBERS ---

        temp = BytesIO()
        base.save(temp, format="webp", optimize=True)
        temp.seek(0)
        return discord.File(temp, filename="bingo.webp")