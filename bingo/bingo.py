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
from redbot.core import Config, commands
from redbot.core.data_manager import bundled_data_path, cog_data_path

from .converter import Stamp

log = getLogger("red.trusty-cogs.bingo")

IMAGE_LINKS: Pattern = re.compile(
    r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg)(\?size=[0-9]*)?)", flags=re.I
)

# --- NEW: Game Type Definitions ---
GAME_TYPES: Dict[str, str] = {
    "STANDARD": "Any horizontal, vertical, or diagonal line.",
    "HORIZONTAL": "Any complete horizontal line.",
    "VERTICAL": "Any complete vertical line.",
    "X_PATTERN": "Both diagonal lines must be completed (form an 'X').",
    "COVERALL": "All 25 squares must be stamped (Blackout).",
}


class Bingo(commands.Cog):
    __version__ = "1.2.2"
    __author__ = ["TrustyJAID"]

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 218773382617890828)
        self.config.register_guild(
            tiles=[],
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
        
    async def red_delete_data_for_user(self, **kwargs):
        """
        Nothing to delete. Information saved is a set of points on a bingo card and
        does not represent end user data.
        """
        return
        
    # --- Utility Functions for Economy/Bank Interaction ---
    # The fix to reliably get the bank object is in get_bank_obj
    
    def _get_bank_object(self, method_name: str) -> Optional[Any]:
        """
        Attempts to find a valid bank object that possesses the specified method name.
        Performs an exhaustive search on the loaded 'Economy' cog and its attributes.
        (This is now mostly redundant but kept for robustness, though get_bank_obj is preferred)
        """
        bank_cog = self.bot.get_cog("Economy")
        if not bank_cog:
            log.debug("Economy cog is not loaded.")
            return None 

        if hasattr(bank_cog, method_name):
            log.debug("Found bank method directly on Economy cog object.")
            return bank_cog
            
        for attr_name in ["bank", "api", "currency"]:
            bank_attribute = getattr(bank_cog, attr_name, None)
            if bank_attribute and hasattr(bank_attribute, method_name):
                log.debug("Found bank method on Economy cog.%s attribute.", attr_name)
                return bank_attribute
        
        for attr_name in dir(bank_cog):
            if attr_name.startswith("_"): 
                continue 
            
            try:
                bank_attribute = getattr(bank_cog, attr_name)
            except Exception:
                continue
            
            if bank_attribute and hasattr(bank_attribute, method_name):
                log.debug("Found bank method on Economy cog.%s attribute via exhaustive search.", attr_name)
                return bank_attribute
            
        log.debug("Could not find required method '%s' on Economy cog or its attributes.", method_name)
        return None

    async def get_bank_obj(self, ctx: commands.Context) -> Optional[Any]:
        """
        Attempt to find the official bank interface object from the Economy cog.
        This is the standard and most reliable way to access deposit_credits.
        """
        bank_cog = self.bot.get_cog("Economy")
        if bank_cog and hasattr(bank_cog, "bank"):
            return bank_cog.bank # This is the standard bank interface object
        return None

    async def get_currency_name(self, ctx: commands.Context) -> str:
        """Helper to get the currency name if the bank cog is loaded."""
        # This still needs to be robust as some cogs name the method differently
        bank_obj = self._get_bank_object("get_currency_name")
        if bank_obj:
            # We must await this call as it's an async method on the bank object/cog
            return await bank_obj.get_currency_name(ctx.guild)
        return "credits" # Default currency name if cog is unavailable
    # --- End Utility Functions ---

    @commands.group(name="bingoset")
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def bingoset(self, ctx: commands.Context):
        """
        Commands for setting bingo settings
        """
        pass

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
        currency_name = await self.get_currency_name(ctx)

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
        # 1. Generate a new random 6-digit seed
        new_seed = random.randint(100000, 999999)
        await self.config.guild(ctx.guild).seed.set(new_seed)
        
        # 2. Reset all player cards
        await self.config.clear_all_members(guild=ctx.guild)
        
        await ctx.send(
            f"🎉 Starting a new bingo game! All player cards have been reset, and a new card arrangement has been generated using seed `{new_seed}`."
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
        Clear out the current bingo cards tiles.
        """
        await self.config.guild(ctx.guild).tiles.clear()
        await ctx.send("I have reset the servers bingo card tiles.")

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
        # Manually fetch tiles count and bank prize for display
        tiles_count = len(await self.config.guild(ctx.guild).tiles())
        bank_prize = await self.config.guild(ctx.guild).bank_prize()
        game_type = await self.config.guild(ctx.guild).game_type()
        currency_name = await self.get_currency_name(ctx)
        
        msg = f"Tiles Set: `{tiles_count}`\n"
        msg += f"Game Type: `{game_type}` ({GAME_TYPES.get(game_type, 'Unknown Type')})\n"
        msg += f"Bank Prize: `{bank_prize} {currency_name}`\n"

        for k, v in settings.items():
            if k in ["bank_prize", "seed", "game_type"]: # Skip items already printed or not needed
                continue
            
            if k == "watermark":
                v = await self.config.guild(ctx.guild).watermark()
            if k == "icon":
                v = await self.config.guild(ctx.guild).icon()
            if k == "background_tile":
                v = await self.config.guild(ctx.guild).background_tile()
                
            name = k.replace("_", " ").title() # Use more readable names
            msg += f"{name}: `{v}`\n"
        await ctx.maybe_send_embed(msg)

    @bingoset.command(name="tiles")
    async def bingoset_tiles(self, ctx: commands.Context, *, tiles: str):
        """
        Set the tiles for the servers bingo cards.

        `tiles` - Separate each tile with `;`
        """
        options = set(tiles.split(";"))
        if len(options) < 24:
            await ctx.send("You must provide exactly 24 tile options to make a bingo card.")
            return
        options = sorted(options)
        await self.config.guild(ctx.guild).tiles.set(options)
        await self.config.clear_all_members(guild=ctx.guild)
        card_settings = await self.get_card_options(ctx)
        file = await self.create_bingo_card(options, guild_name=ctx.guild.name, **card_settings)
        await ctx.send("Here's how your bingo cards will appear", file=file)

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
        tiles = await self.config.guild(ctx.guild).tiles()
        stamps = await self.config.member(ctx.author).stamps()
        bank_prize = await self.config.guild(ctx.guild).bank_prize()
        msg = None
        
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
        if self.check_stamps(stamps, ctx.guild):
            is_bingo_win = True
            if msg:
                msg += f"\n🎉 {ctx.author.mention} has a **BINGO!**"
            else:
                msg = f"🎉 {ctx.author.mention} has a **BINGO!**"
            
            # Step 3: Bank Prize Distribution Logic
            if bank_prize > 0:
                # Use the reliable helper to find the valid bank object
                bank_obj = await self.get_bank_obj(ctx)
                
                if bank_obj:
                    try:
                        # FIX: This is the correct standard call for Red's bank service (member, amount)
                        # The previous error was due to `bank_obj` incorrectly pointing to a Config object.
                        await bank_obj.deposit_credits(ctx.author, bank_prize)
                        currency = await self.get_currency_name(ctx)
                        msg += f" (and won **{bank_prize}** {currency}!)"
                    except Exception as e:
                        # Log the specific exception if the deposit call itself fails
                        log.error("Failed to deposit bank prize for bingo: %s", e, exc_info=True)
                        msg += "\n*Error awarding bank prize.*"
                else:
                    # If bank_obj is None, Economy cog is likely not loaded or bank interface is missing.
                    log.error(
                        "Bank prize configured but Economy cog is missing or missing the 'bank' interface."
                    )
                    msg += (
                        "\n\n🚨 **Bank Prize Error:** The Economy cog's bank function couldn't be found. "
                        "Is the Economy cog loaded?"
                    )
        
        # Step 4: Generate and send the card
        seed = int(await self.config.guild(ctx.guild).seed()) + ctx.author.id
        random.seed(seed)
        random.shuffle(tiles)
        card_settings = await self.get_card_options(ctx)
        
        temp = await self.create_bingo_card(
            tiles, stamps=stamps, guild_name=ctx.guild.name, **card_settings
        )
        
        # Send the message and the generated card image
        await ctx.send(
            content=msg,
            file=temp,
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