from PIL import Image, ImageDraw, ImageFont
import discord
import io
import asyncio
import time

# URL for Font Awesome 6 Free (Solid) TTF
# Switched to TTF and a specific version tag (6.5.1) for better stability/compatibility than OTF
FA_URL = "https://raw.githubusercontent.com/FortAwesome/Font-Awesome/6.5.1/webfonts/fa-solid-900.ttf"

# Font Cache (stores the BytesIO object of the font file)
FONT_FILE_CACHE = None

# Holiday Icon Map (Font Awesome 6 Unicode PUA)
HOLIDAY_ICONS = {
    1:  "\uf2dc", # Snowflake
    2:  "\uf06d", # Fire
    3:  "\uf563", # Cookie
    4:  "\uf7b6", # Mug Hot
    5:  "\uf7ad", # Mitten
    6:  "\uf772", # Scroll
    7:  "\uf696", # Socks
    8:  "\uf7c8", # Sleigh
    9:  "\uf0f3", # Bell
    10: "\uf001", # Music
    11: "\uf786", # Candy Cane
    12: "\uf005", # Star
    13: "\uf1fd", # Cake
    14: "\uf543", # Pizza
    15: "\uf0f4", # Coffee
    16: "\uf79f", # Cheers
    17: "\uf015", # Home
    18: "\uf7d0", # Snowman
    19: "\uf7c5", # Ice Skate
    20: "\uf4cd", # Parachute Box
    21: "\uf328", # Ribbon
    22: "\uf49e", # Box Open
    23: "\uf1bb", # Tree
    24: "\uf79c", # Gifts
    25: "\uf06b"  # Gift
}

async def get_fontawesome(bot, size):
    """
    Returns an ImageFont object for FontAwesome, downloading it if necessary.
    Returns None if download fails.
    """
    global FONT_FILE_CACHE
    
    # 1. Try Cache
    if FONT_FILE_CACHE:
        try:
            FONT_FILE_CACHE.seek(0)
            return ImageFont.truetype(FONT_FILE_CACHE, size)
        except Exception:
            # If cache is corrupted, reset it
            FONT_FILE_CACHE = None
    
    # 2. Download
    try:
        async with bot.session.get(FA_URL) as resp:
            if resp.status == 200:
                data = await resp.read()
                FONT_FILE_CACHE = io.BytesIO(data)
                return ImageFont.truetype(FONT_FILE_CACHE, size)
            else:
                print(f"FontAwesome Download Failed: Status {resp.status}")
    except Exception as e:
        print(f"Failed to download FontAwesome: {e}")
        
    return None

async def generate_holiday_image(bot, opened_days: list, current_day_int: int):
    """
    Generates a 5x5 grid image for the Holiday Gifts system.
    """
    # Configuration
    cell_size = 100
    padding = 10
    cols = 5
    rows = 5
    width = (cell_size * cols) + (padding * (cols + 1))
    height = (cell_size * rows) + (padding * (rows + 1))
    
    # Colors
    bg_color = (47, 49, 54)
    box_color_default = (114, 137, 218)
    box_color_opened = (46, 204, 113)
    box_color_25 = (255, 215, 0)
    text_color = (255, 255, 255)
    fail_color = (255, 0, 0)
    
    image = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    
    # Load Fonts
    icon_size = int(cell_size * 0.85)
    fa_font = await get_fontawesome(bot, icon_size)
    
    try:
        number_font = ImageFont.truetype("arialbd.ttf", 20)
        mark_font = ImageFont.truetype("arial.ttf", 60)
    except IOError:
        try:
             number_font = ImageFont.truetype("arial.ttf", 20)
             mark_font = number_font
        except IOError:
            number_font = ImageFont.load_default()
            mark_font = ImageFont.load_default()

    for i in range(1, 26):
        idx = i - 1
        row = idx // cols
        col = idx % cols
        
        x1 = padding + (col * (cell_size + padding))
        y1 = padding + (row * (cell_size + padding))
        x2 = x1 + cell_size
        y2 = y1 + cell_size
        
        center_x = x1 + (cell_size / 2)
        center_y = y1 + (cell_size / 2)
        
        is_opened = i in opened_days
        
        # Determine Fill
        if is_opened:
            fill = box_color_opened
            if i == 25:
                 fill = box_color_25
        elif i == 25:
            fill = box_color_25
        else:
            fill = box_color_default

        # Draw Rectangle
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=(0,0,0))
        
        if is_opened:
            # Draw Icon
            if fa_font:
                icon_char = HOLIDAY_ICONS.get(i, "\uf06b")
                try:
                    # New Pillow (8.0+)
                    draw.text((center_x, center_y), icon_char, fill=(255, 255, 255), font=fa_font, anchor="mm")
                except ValueError:
                    # Old Pillow (<8.0)
                    w, h = draw.textsize(icon_char, font=fa_font)
                    draw.text(((x1 + (cell_size-w)/2), (y1 + (cell_size-h)/2)), icon_char, fill=(255, 255, 255), font=fa_font)
            else:
                # Fallback if font failed to download
                draw.text((center_x, center_y), "!", fill=(255, 0, 0), font=mark_font, anchor="mm")

            # Day Number (Small)
            draw.text((x1 + 5, y1 + 5), str(i), fill=(220, 220, 220), font=number_font)
        else:
            # Day Number (Normal)
            draw.text((x1 + 10, y1 + 10), str(i), fill=text_color, font=number_font)
            
            # Missed Overlay
            if i < current_day_int:
                draw.text((x1 + 30, y1 + 20), "X", fill=fail_color, font=mark_font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    timestamp = int(time.time())
    return discord.File(buffer, filename=f"holiday_day_{current_day_int}_{timestamp}.png")