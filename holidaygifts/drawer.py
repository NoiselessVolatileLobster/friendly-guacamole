from PIL import Image, ImageDraw, ImageFont
import discord
import io
import math

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
    box_color_default = (114, 137, 218) # Blurpleish
    box_color_25 = (255, 215, 0) # Gold
    text_color = (255, 255, 255)
    check_color = (57, 255, 20) # Neon Green
    fail_color = (255, 0, 0) # Red
    
    # Create Base
    image = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    
    # Try to load a font, otherwise default
    try:
        font = ImageFont.truetype("arial.ttf", 40)
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
        
        # Determine Box Color
        fill = box_color_default
        if i == 25:
            fill = box_color_25
            
        # Draw Box
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=(0,0,0))
        
        # Draw Number
        draw.text((x1 + 10, y1 + 10), str(i), fill=text_color, font=font)
        
        # Draw Status Overlay
        status_text = ""
        status_fill = None
        
        if i in opened_days:
            status_text = "✓"
            status_fill = check_color
        elif i < current_day_int:
            status_text = "X"
            status_fill = fail_color
            
        if status_text:
            draw.text((x1 + 30, y1 + 20), status_text, fill=status_fill, font=mark_font)

    # Save to buffer
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    return discord.File(buffer, filename=f"holiday_day_{current_day_int}.png")