import discord
from redbot.core import commands, checks
from typing import Optional, Union, List

class Say(commands.Cog):
    """
    A cog to allow the bot to repeat messages in specific channels or threads.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_messages=True)
    async def say(
        self,
        ctx: commands.Context,
        channel: Optional[Union[discord.TextChannel, discord.Thread]] = None,
        *,
        message: str = None
    ):
        """
        Make the bot say something in the current channel or a specified one.
        Supports sending attachments.

        Usage:
        [p]say <message>
        [p]say #channel <message>
        [p]say <attachment> (No text required if attachment is present)
        """
        
        # 1. Delete the user's message immediately
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        # 2. Determine the target destination
        target_destination = channel or ctx.channel

        # 3. Check if the bot has permission to speak in the target
        if not target_destination.permissions_for(ctx.guild.me).send_messages:
            try:
                await ctx.author.send(f"I do not have permission to send messages in {target_destination.mention}.")
            except discord.Forbidden:
                pass
            return

        # 4. Process Attachments
        # We need to convert the attachments from the context message into discord.File objects
        files: List[discord.File] = []
        if ctx.message.attachments:
            for attachment in ctx.message.attachments:
                # Converts the attachment directly to a file object
                files.append(await attachment.to_file())

        # 5. Validation
        # Ensure we aren't trying to send an empty message
        if not message and not files:
            try:
                await ctx.author.send("You must provide a message or an attachment.")
            except discord.Forbidden:
                pass
            return

        # 6. Send the message
        allowed_mentions = discord.AllowedMentions(
            users=True,
            roles=True,
            everyone=False
        )

        try:
            await target_destination.send(
                content=message, 
                files=files, 
                allowed_mentions=allowed_mentions
            )
        except discord.HTTPException as e:
            # Catch errors like file size limits
            try:
                await ctx.author.send(f"Failed to send message: {e}")
            except discord.Forbidden:
                pass