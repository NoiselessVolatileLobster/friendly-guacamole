import discord
import asyncio
import aiohttp
from redbot.core import commands, checks

class BulkEmoji(commands.Cog):
    """
    Upload multiple emojis at once by attaching files.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.group()
    @checks.admin_or_permissions(manage_emojis=True)
    async def bulkemoji(self, ctx):
        """Manage bulk emoji operations."""
        pass

    @bulkemoji.command(name="upload")
    async def bulkemoji_upload(self, ctx):
        """
        Uploads attached images as emojis.
        
        Attach the images you want to upload to this command message.
        The filename will be used as the emoji name.
        """
        if not ctx.message.attachments:
            return await ctx.send("Please attach the images you wish to upload as emojis to the command message.")

        # Initial status message
        msg = await ctx.send("Processing images... this may take a moment.")
        
        uploaded = 0
        failed = 0
        errors = []

        for attachment in ctx.message.attachments:
            # Basic validation for image types
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                failed += 1
                errors.append(f"`{attachment.filename}`: Not an image file.")
                continue

            # Sanitize filename for emoji name (remove extension, replace spaces)
            emoji_name = attachment.filename.rsplit(".", 1)[0]
            emoji_name = "".join(c for c in emoji_name if c.isalnum() or c == "_")
            
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
                elif e.code == 50035: # Invalid form body (name issues, size, etc)
                    errors.append(f"`{attachment.filename}`: Invalid file size or name.")
                else:
                    errors.append(f"`{attachment.filename}`: HTTP Error {e.status} ({e.text})")
            except Exception as e:
                failed += 1
                errors.append(f"`{attachment.filename}`: Unexpected error.")

        # Summary Report
        summary = f"**Bulk Emoji Upload Complete**\n✅ Uploaded: {uploaded}\n❌ Failed: {failed}"
        
        if errors:
            # Truncate errors if list is too long for one message
            error_msg = "\n".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"\n...and {len(errors) - 10} more errors."
            summary += f"\n\n**Errors:**\n{error_msg}"

        await msg.edit(content=summary)