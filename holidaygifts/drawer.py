from PIL import Image, ImageDraw, ImageFont
import discord
import io

# Emoji Map for Days 1-25
HOLIDAY_EMOJIS = {
    1: "❄️", 2: "🕯️", 3: "🍪", 4: "🥛", 5: "🧤",
    6: "🧣", 7: "🧦", 8: "🦌", 9: "🔔", 10: "🎶",
    11: "🍬", 12: "🍭", 13: "🧁", 14: "🥧", 15: "☕",
    16: "🥂", 17: "🏠", 18: "⛄", 19: "⛸️", 20: "🛷",
    21: "🎀", 22: "📦", 23: "🎄", 24: "🎅", 25: "🎁"
}

async def generate_holiday_image(opened_days: list, current_day_int: int):
    """
    Generates a 5x5 grid image for the Holiday Gifts system.
    opened_days: list of integers representing days the user successfully opened.
    current_day_int: The actual current day of the event (1-25).
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
    
    # Create Base
    image = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    
    # Font Setup
    # We try to load Arial for general text.
    # For emojis, we attempt to load a font large enough for 85% box size.
    emoji_size = int(cell_size * 0.85)
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
        mark_font = ImageFont.truetype("arial.ttf", 60)
        emoji_font = ImageFont.truetype("arial.ttf", emoji_size)
    except IOError:
        # Fallback if arial.ttf is not found on the host system
        font = ImageFont.load_default()
        mark_font = ImageFont.load_default()
        emoji_font = ImageFont.load_default()

    for i in range(1, 26):
        # Calculate Grid Position (0-indexed)
        idx = i - 1
        row = idx // cols
        col = idx % cols
        
        x1 = padding + (col * (cell_size + padding))
        y1 = padding + (row * (cell_size + padding))
        x2 = x1 + cell_size
        y2 = y1 + cell_size
        
        # Calculate Center for Emoji
        center_x = x1 + (cell_size / 2)
        center_y = y1 + (cell_size / 2)
        
        # --- Logic: Determine Appearance ---
        is_opened = i in opened_days
        
        # 1. Background Fill
        if is_opened:
            fill = box_color_opened
            if i == 25:
                 # Optional: Keep day 25 Gold even if opened, or blend? 
                 # Let's keep it Gold to show its prestige, but maybe darker?
                 # Or just Green to show completion. Let's use Gold for 25 always.
                 fill = box_color_25
        elif i == 25:
            fill = box_color_25
        else:
            fill = box_color_default

        # Draw Box
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=(0,0,0))
        
        # 2. Content
        if is_opened:
            # Draw relevant emoji (~85% size)
            emoji_char = HOLIDAY_EMOJIS.get(i, "🎁")
            
            # Using anchor="mm" to center text (Middle-Middle)
            # This requires a relatively recent Pillow version (8.0+), standard in Red 3.5+
            try:
                draw.text((center_x, center_y), emoji_char, fill=(255, 255, 255), font=emoji_font, anchor="mm")
            except ValueError:
                # Fallback for older Pillow versions
                w, h = draw.textsize(emoji_char, font=emoji_font)
                draw.text(((x1 + (cell_size-w)/2), (y1 + (cell_size-h)/2)), emoji_char, fill=(255, 255, 255), font=emoji_font)
                
            # Draw Day Number (Smaller, in corner, so we still know which day it was)
            draw.text((x1 + 5, y1 + 5), str(i), fill=(200, 200, 200), font=font)
            
        else:
            # Not opened yet
            
            # Draw Day Number (Center-ish or Top-Left standard)
            draw.text((x1 + 10, y1 + 10), str(i), fill=text_color, font=font)
            
            # Draw "Missed" overlay if day passed and not opened
            if i < current_day_int:
                draw.text((x1 + 30, y1 + 20), "X", fill=fail_color, font=mark_font)

    # Save to buffer
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    return discord.File(buffer, filename=f"holiday_day_{current_day_int}.png")