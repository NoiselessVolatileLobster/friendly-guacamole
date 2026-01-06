from PIL import Image, ImageDraw, ImageFont
import discord
import io
import asyncio

# Emoji Map for Days 1-25
HOLIDAY_EMOJIS = {
    1: "❄️", 2: "🕯️", 3: "🍪", 4: "🥛", 5: "🧤",
    6: "🧣", 7: "🧦", 8: "🦌", 9: "🔔", 10: "🎶",
    11: "🍬", 12: "🍭", 13: "🧁", 14: "🥧", 15: "☕",
    16: "🥂", 17: "🏠", 18: "⛄", 19: "⛸️", 20: "🛷",
    21: "🎀", 22: "📦", 23: "🎄", 24: "🎅", 25: "🎁"
}

# Cache images in memory so we don't spam requests
EMOJI_CACHE = {}

async def get_emoji_image(bot, emoji_char):
    """
    Fetches the Twemoji PNG for a given unicode emoji char.
    """
    if emoji_char in EMOJI_CACHE:
        return EMOJI_CACHE[emoji_char]
    
    # Convert unicode char to hex string for Twemoji URL (e.g. "1f385")
    # We strip variant selectors to match Twemoji filenames better
    code = "-".join(f"{ord(c):x}" for c in emoji_char if ord(c) != 0xfe0f)
    
    url = f"https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/72x72/{code}.png"
    
    try:
        async with bot.session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                EMOJI_CACHE[emoji_char] = img
                return img
    except Exception as e:
        print(f"Failed to load emoji {emoji_char}: {e}")
        pass
    
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
    bg_color = (47, 49, 54) # Discord Dark Mode Greyish
    
    # Box Colors
    box_color_default = (114, 137, 218) # Blurpleish (Locked/Future)
    box_color_opened = (46, 204, 113)   # Emerald Green (Opened)
    box_color_25 = (255, 215, 0)        # Gold (Day 25 Special)
    
    # Text/Overlay Colors
    text_color = (255, 255, 255)
    fail_color = (255, 0, 0) # Red for missed days
    
    # Pre-fetch emojis for opened days
    needed_days = [d for d in opened_days if d in HOLIDAY_EMOJIS]
    tasks = []
    for d in needed_days:
        char = HOLIDAY_EMOJIS[d]
        if char not in EMOJI_CACHE:
            tasks.append(get_emoji_image(bot, char))
    
    if tasks:
        await asyncio.gather(*tasks)

    # Create Base
    image = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    
    # Font Setup
    emoji_size = int(cell_size * 0.85)
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
        mark_font = ImageFont.truetype("arial.ttf", 60)
    except IOError:
        font = ImageFont.load_default()
        mark_font = ImageFont.load_default()

    for i in range(1, 26):
        # Calculate Grid Position (0-indexed)
        idx = i - 1
        row = idx // cols
        col = idx % cols
        
        x1 = padding + (col * (cell_size + padding))
        y1 = padding + (row * (cell_size + padding))
        x2 = x1 + cell_size
        y2 = y1 + cell_size
        
        # Calculate Center
        center_x = x1 + (cell_size / 2)
        center_y = y1 + (cell_size / 2)
        
        is_opened = i in opened_days
        
        # 1. Background Fill
        if is_opened:
            fill = box_color_opened
            if i == 25:
                 fill = box_color_25
        elif i == 25:
            fill = box_color_25
        else:
            fill = box_color_default

        # Draw Box
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=(0,0,0))
        
        # 2. Content
        if is_opened:
            # Draw Emoji Image
            emoji_char = HOLIDAY_EMOJIS.get(i, "🎁")
            emoji_img = EMOJI_CACHE.get(emoji_char)
            
            if emoji_img:
                # Resize emoji to fit nicely
                icon = emoji_img.resize((emoji_size, emoji_size), resample=Image.BICUBIC)
                
                # Calculate position to center it
                icon_x = int(center_x - (emoji_size / 2))
                icon_y = int(center_y - (emoji_size / 2))
                
                # Paste with alpha mask
                image.paste(icon, (icon_x, icon_y), icon)
            else:
                # Fallback if download failed
                draw.text((x1+20, y1+20), "???", fill=(255,255,255), font=font)
                
            # Draw Day Number (Smaller, in corner)
            draw.text((x1 + 5, y1 + 5), str(i), fill=(200, 200, 200), font=font)
            
        else:
            # Not opened yet
            draw.text((x1 + 10, y1 + 10), str(i), fill=text_color, font=font)
            
            # Draw "Missed" overlay
            if i < current_day_int:
                draw.text((x1 + 30, y1 + 20), "X", fill=fail_color, font=mark_font)

    # Save to buffer
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    return discord.File(buffer, filename=f"holiday_day_{current_day_int}.png")