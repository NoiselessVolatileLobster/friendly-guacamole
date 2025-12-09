import discord
import asyncio
import aiohttp
import re
from redbot.core import commands, checks

class BulkEmoji(commands.Cog):
    """
    Upload multiple emojis at once by attaching files.
    """

    def __init__(self, bot):
        self.bot = bot

    def _extract_name_from_pattern(self, filename: str, pattern: str) -> str:
        """
        Extracts a substring from the filename based on a wildcard pattern.
        Example: 
        Filename: "8886-pastelgreena"
        Pattern: "pastelgreen*"
        Result: "pastelgreena"
        """
        # Escape the pattern to treat it as literal text, then convert * back to regex wildcard .*
        regex_pattern = re.escape(pattern).replace(r"\*", ".*")
        
        # Search for the pattern inside the filename
        match = re.search(regex_pattern, filename)
        
        if match:
            return match.group(0)
        return filename

    @commands.group()
    @checks.admin_or_permissions(manage_emojis=True)
    async def bulkemoji(self, ctx):
        """Manage bulk emoji operations."""
        pass

    @bulkemoji.command(name="upload")
    async def bulkemoji_upload(self, ctx, naming_pattern: str = None):
        """
        Uploads attached images as emojis.
        
        Optional: Provide a naming pattern with * as a wildcard to extract specific parts of the filename.
        
        Example:
        [p]bulkemoji upload pastel* (Renames "123-pastelblue.png" to "pastelblue")
        """
        if not ctx.message.attachments:
            return await ctx.send("Please attach the images you wish to upload as emojis to the command message.")

        # Initial status message
        status_msg = await ctx.send("Processing images... this may take a moment.")
        
        uploaded = 0
        failed = 0
        errors = []

        for attachment in ctx.message.attachments:
            # Basic validation for image types
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                failed += 1
                errors.append(f"`{attachment.filename}`: Not an image file.")
                continue

            # 1. Get raw filename without extension
            raw_name = attachment.filename.rsplit(".", 1)[0]
            
            # 2. Apply Naming Pattern (if provided)
            if naming_pattern:
                extracted_name = self._extract_name_from_pattern(raw_name, naming_pattern)
            else:
                extracted_name = raw_name

            # 3. Sanitize for Discord (Alphanumeric and underscores only)
            emoji_name = "".join(c for c in extracted_name if c.isalnum() or c == "_")
            
            # Discord emoji names must be at least 2 chars
            if len(emoji_name) < 2:
                emoji_name += "_emoji"

            try:
                image_data = await attachment.read()
                await ctx.guild.create_custom_emoji(
                    name=emoji_name,
                    image=image_data,
                    reason=f"Bulk upload by {ctx.author}"
                )
                uploaded += 1
                # Small delay to be polite to the API rate limits
                await asyncio.sleep(1) 

            except discord.HTTPException as e:
                failed += 1
                if e.code == 30008: # Max emojis reached
                    errors.append(f"`{attachment.filename}`: Max emoji slot limit reached.")
                    break # Stop processing if full
                elif e.code == 50035: # Invalid form body
                    errors.append(f"`{attachment.filename}`: Invalid name or file size.")
                else:
                    errors.append(f"`{attachment.filename}`: HTTP Error {e.status} ({e.text})")
            except Exception as e:
                failed += 1
                errors.append(f"`{attachment.filename}`: Unexpected error.")

        # Summary Report
        summary = f"**Bulk Emoji Upload Complete**\n✅ Uploaded: {uploaded}\n❌ Failed: {failed}"
        
        if errors:
            error_msg = "\n".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"\n...and {len(errors) - 10} more errors."
            summary += f"\n\n**Errors:**\n{error_msg}"

        await status_msg.edit(content=summary)