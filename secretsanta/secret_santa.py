import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
import random
from typing import Optional, List, Dict, Set
from datetime import datetime, timezone
from tabulate import tabulate

# --- UI Components for Sign-Up ---

class SecretSantaModal(discord.ui.Modal, title="Secret Santa Sign-Up"):
    """Discord Modal for users to enter their sign-up information."""
    
    country = discord.ui.TextInput(
        label="Your Country",
        placeholder="e.g., Canada, United States, Japan",
        max_length=100,
        style=discord.TextStyle.short,
    )

    wishlist_url = discord.ui.TextInput(
        label="Please paste your Amazon Wishlist here",
        placeholder="Enter a full URL (e.g., https://www.amazon.com/hz/wishlist/...)",
        max_length=500,
        style=discord.TextStyle.short,
        required=True, # Wishlist is mandatory for participation
    )
    
    def __init__(self, cog: "SecretSanta") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        user = interaction.user # Get the user object for DM attempt
        
        # 1. Check if user is already signed up
        current_signups = await self.cog.config.signups()
        if user_id_str in current_signups:
            await interaction.response.send_message(
                "🎁 You are already signed up for Secret Santa! No need to sign up again.", 
                ephemeral=True
            )
            return

        country = self.country.value.strip()
        wishlist = self.wishlist_url.value.strip() # Extract the wishlist URL

        # Check if sign-ups are open
        if not await self.cog.config.ss_open():
            await interaction.response.send_message(
                "❌ Secret Santa sign-ups are currently closed.", 
                ephemeral=True
            )
            return

        # 2. Record the sign-up
        async with self.cog.config.signups() as signups:
            # Store the User ID as a string key
            signups[user_id_str] = {
                "country": country, 
                "username": str(user),
                "wishlist": wishlist, # Store the wishlist URL
                "timestamp": datetime.now(timezone.utc).timestamp() # Store UTC timestamp
            }
        
        # 3. Attempt to send confirmation DM and determine status message
        dm_status_message = ""
        try:
            await user.send(
                f"Thank you for signing up for Secret Santa! We have recorded your Amazon Wishlist: <{wishlist}>. "
                "You will receive your match's details once the sign-up period closes."
            )
            dm_status_message = "\n\n**DM Status:** You should be getting a confirmation message in your Direct Messages right now."
        except discord.Forbidden:
            dm_status_message = "\n\n**DM Status:** Uh-oh. I couldn't send you a Direct Message. Please check your privacy settings to ensure I can contact you when matching occurs."
        except Exception:
            dm_status_message = "\n\n**DM Status:** Uh-oh. I couldn't send you a Direct Message."

        # 4. Send the final ephemeral response
        await interaction.response.send_message(
            f"✅ You have successfully signed up for Secret Santa! Country: **{country}**."
            f"\nYour Wishlist: **<{wishlist}>** was recorded."
            f"{dm_status_message}",
            ephemeral=True
        )


class SantaButtonView(discord.ui.View):
    """Discord View containing the persistent sign-up button."""
    def __init__(self, cog: "SecretSanta"):
        # Set timeout to None so the view persists across bot restarts
        super().__init__(timeout=None)
        self.cog = cog
        # Set a persistent custom ID for the bot to recognize it after restarts
        self.santa_button.custom_id = "secret_santa_signup_button"
        
    @discord.ui.button(
        label="Click to Join Secret Santa!", 
        style=discord.ButtonStyle.green, 
        emoji="🎅"
    )
    async def santa_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if sign-ups are open
        if not await self.cog.config.ss_open():
            await interaction.response.send_message(
                "❌ Secret Santa sign-ups are currently closed for the year. Try again next time!", 
                ephemeral=True
            )
            return
            
        # If open, show the Modal form
        await interaction.response.send_modal(SecretSantaModal(self.cog))

# --- UI Components for Post-Match Actions ---

class RecipientActionView(discord.ui.View):
    """
    View containing buttons for the Recipient to interact anonymously back with their Santa.
    This view is sent via DM to the Recipient when the Santa reports an error.
    """
    def __init__(self, cog: "SecretSanta", santa_id: int, recipient_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.santa_id = str(santa_id)
        self.recipient_id = str(recipient_id)

    @discord.ui.button(label="I have fixed my wishlist", style=discord.ButtonStyle.green, emoji="✅")
    async def fixed_wishlist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Green button: Sends an anonymous DM to Santa that the wishlist is fixed."""
        await self.cog.send_reply_dm(
            interaction, 
            self.santa_id, 
            self.recipient_id,
            "✅Wishlist has been fixed", 
            "Wishlist Fixed Notification"
        )

class SantaActionView(discord.ui.View):
    """
    View containing buttons for the Santa to interact anonymously with their recipient.
    This view is sent via DM after matching.
    """
    def __init__(self, cog: "SecretSanta", santa_id: int):
        # Setting timeout to None allows buttons to be active indefinitely in the DM.
        super().__init__(timeout=None) 
        self.cog = cog
        # Store the ID of the GIVER (Santa) who received this specific DM/View
        self.santa_id = str(santa_id) 

    @discord.ui.button(label="Report Wishlist Error", style=discord.ButtonStyle.red, emoji="❗")
    async def report_error_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Red button: Sends an anonymous DM about a wishlist error to the recipient."""
        # We attach the RecipientActionView so they can reply "Fixed"
        await self.cog.send_anonymous_dm(
            interaction, 
            self.santa_id, 
            "I have been asked to tell you there is an error with your Secret Santa 2025 wishlist.", 
            "Wishlist Error Report",
            attach_reply_view=True
        )

    @discord.ui.button(label="Gift is on its way!", style=discord.ButtonStyle.green, emoji="🚚")
    async def gift_sent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Green button: Sends an anonymous DM that the gift is in transit to the recipient."""
        await self.cog.send_anonymous_dm(
            interaction, 
            self.santa_id, 
            "I have been asked to tell you your Secret Santa 2025 gift is on its way!", 
            "Gift Sent Notification"
        )

    @discord.ui.button(label="Gift Delayed", style=discord.ButtonStyle.secondary, emoji="⏳")
    async def gift_delayed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Yellow/Grey button: Sends an anonymous DM that the gift is delayed."""
        await self.cog.send_anonymous_dm(
            interaction, 
            self.santa_id, 
            "⏳Your gift has shipped, but it has been delayed", 
            "Gift Delayed Notification"
        )

# --- Main Cog ---

class SecretSanta(commands.Cog):
    """A Discord Secret Santa sign-up and matching cog."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # Default configuration structure
        default_global = {
            "embed_data": {
                "title": "🎁 Secret Santa Sign-Up!",
                "description": "Click the button below to join our annual Secret Santa event. You'll be matched with a random recipient once sign-ups close. Be sure to provide accurate country information!",
                "image": None,
                "channel_id": None,
                "message_id": None,
            },
            "ss_open": False,  # Is the sign-up period open?
            "signups": {},     # {user_id_str: {"country": "...", "username": "...", "wishlist": "...", "timestamp": float}}
            "matches": {},     # {santa_user_id_str: recipient_user_id_str}
            "dm_confirm": {},  # {santa_user_id_str: True/False}
            "log_channel_id": None, # Channel to log anonymous actions
        }
        
        self.config.register_global(**default_global)

    # Re-adds the persistent view when the bot restarts
    @commands.Cog.listener()
    async def on_ready(self):
        message_id = await self.config.embed_data.message_id()
        channel_id = await self.config.embed_data.channel_id()
        
        # Only add the view if a message has been successfully set up previously
        if message_id and channel_id:
            # The bot needs to be aware of the view object to process interactions
            self.bot.add_view(SantaButtonView(self))

    # --- Helper Functions for Anonymous Actions and Logging ---

    async def _log_action(self, log_channel: Optional[discord.TextChannel], title: str, status: str, sender: discord.User, receiver: discord.User):
        """
        Helper function to log action status to the configured channel.
        
        log_channel: The resolved log channel object, or None if not found/configured.
        """
        if not log_channel:
            # Cannot log if the channel doesn't exist or isn't configured
            return

        embed = discord.Embed(
            title=f"Secret Santa Action Log: {title}",
            description=f"Action attempted by **{sender.name}** (`{sender.id}`).",
            color=discord.Color.red() if status.startswith("FAILED") else discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Sender", value=f"{sender.mention}\n`{sender.id}`", inline=True)
        embed.add_field(name="Receiver", value=f"{receiver.mention}\n`{receiver.id}`", inline=True)
        embed.add_field(name="Status", value=status, inline=False)
        
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # Cannot send to log channel (e.g., bot permissions were revoked)
            pass

    async def send_anonymous_dm(self, interaction: discord.Interaction, santa_id_str: str, message_content: str, log_title: str, attach_reply_view: bool = False):
        """
        Handles the anonymous DM and logging process for Santa (Giver) -> Recipient actions.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        matches = await self.config.matches()
        signups = await self.config.signups()
        
        # 1. Get Recipient ID
        recipient_id_str = matches.get(santa_id_str)
        if not recipient_id_str:
            await interaction.followup.send("❌ Could not find a match for you. Has matching been run?", ephemeral=True)
            return

        # 2. Get User Objects
        santa = self.bot.get_user(int(santa_id_str))
        recipient = self.bot.get_user(int(recipient_id_str))

        if not santa or not recipient:
            await interaction.followup.send("❌ Recipient or Santa user could not be found. They might have left the server.", ephemeral=True)
            return
            
        recipient_username = signups.get(recipient_id_str, {}).get("username", recipient.name)

        # 3. Resolve the log channel
        log_channel: Optional[discord.TextChannel] = None
        log_channel_id = await self.config.log_channel_id()
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            if not log_channel:
                await self.config.log_channel_id.set(None)

        # 4. Prepare View (if needed)
        reply_view = None
        if attach_reply_view:
            reply_view = RecipientActionView(self, santa_id=santa.id, recipient_id=recipient.id)

        # 5. Send Anonymous DM
        log_status = ""
        try:
            await recipient.send(f"🎅 **Secret Santa 2025 Notification**\n\n{message_content}", view=reply_view)
            log_status = "SUCCESS"
            await interaction.followup.send(
                f"✅ Your message for **{recipient_username}** has been sent anonymously.", 
                ephemeral=True
            )
        except discord.Forbidden:
            log_status = "FAILED (DMs blocked)"
            await interaction.followup.send(
                f"❌ Failed to send DM to **{recipient_username}**. They likely have DMs disabled.", 
                ephemeral=True
            )
        except Exception as e:
            log_status = f"FAILED (Error: {e})"
            await interaction.followup.send(
                f"❌ An unknown error occurred while trying to send the DM to **{recipient_username}**.", 
                ephemeral=True
            )
            
        # 6. Log the action
        await self._log_action(log_channel, log_title, log_status, sender=santa, receiver=recipient)

    async def send_reply_dm(self, interaction: discord.Interaction, santa_id_str: str, recipient_id_str: str, message_content: str, log_title: str):
        """
        Handles the anonymous DM and logging process for Recipient -> Santa actions.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 1. Get User Objects
        santa = self.bot.get_user(int(santa_id_str))
        recipient = self.bot.get_user(int(recipient_id_str))

        if not santa:
            await interaction.followup.send("❌ Your Santa could not be reached (user not found).", ephemeral=True)
            return

        # 2. Resolve the log channel
        log_channel: Optional[discord.TextChannel] = None
        log_channel_id = await self.config.log_channel_id()
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            if not log_channel:
                await self.config.log_channel_id.set(None)

        # 3. Send Anonymous DM to Santa
        log_status = ""
        try:
            await santa.send(f"📨 **Message from your Recipient**\n\n{message_content}")
            log_status = "SUCCESS"
            await interaction.followup.send(
                f"✅ Your message has been sent to your Santa.", 
                ephemeral=True
            )
        except discord.Forbidden:
            log_status = "FAILED (DMs blocked)"
            await interaction.followup.send(
                f"❌ Failed to send DM to your Santa. They likely have DMs disabled.", 
                ephemeral=True
            )
        except Exception as e:
            log_status = f"FAILED (Error: {e})"
            await interaction.followup.send(
                f"❌ An unknown error occurred while trying to send the DM.", 
                ephemeral=True
            )
            
        # 4. Log the action (Sender is Recipient here, Receiver is Santa)
        sender_user = recipient if recipient else interaction.user
        await self._log_action(log_channel, log_title, log_status, sender=sender_user, receiver=santa)


    # --- Admin Commands ---

    @commands.group(name="secretsanta", aliases=["ss"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def ss(self, ctx: commands.Context):
        """Manages the Secret Santa event for the server."""
        await ctx.send_help(self.ss)
        
    @ss.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_setup(
        self, 
        ctx: commands.Context, 
        channel: discord.TextChannel, 
        title: str, 
        description: str, 
        image_url: Optional[str] = None
    ):
        """
        Posts the Secret Santa sign-up embed and button to a specified channel.
        
        <channel>: The channel to post the embed in.
        <title>: The title for the embed (in quotes if multiple words).
        <description>: The description for the embed (in quotes if multiple words).
        [image_url]: (Optional) A direct URL to an image for the embed.
        """
        
        # 1. Build the Embed
        embed = discord.Embed(
            title=title, 
            description=description, 
            color=await ctx.embed_color()
        )
        if image_url:
            embed.set_image(url=image_url)
            
        # 2. Post the message with the persistent View
        view = SantaButtonView(self)
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            return await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
            
        # 3. Store the configuration data for persistence
        await self.config.embed_data.set({
            "title": title,
            "description": description,
            "image": image_url,
            "channel_id": channel.id,
            "message_id": message.id,
        })
        
        # 4. Add the persistent view to the bot's state
        self.bot.add_view(view)

        await ctx.send(
            f"✅ Secret Santa sign-up message posted in {channel.mention}. "
            "Users can now sign up when registration is open (use `[p]secretsanta open`)."
        )

    @ss.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_clear_data(self, ctx: commands.Context):
        """
        Clears all sign-ups, matches, and DM confirmations, and forgets the embed location.
        The embed content is retained, but a new setup is required to post the sign-up embed again.
        """
        # 1. Clear all dynamic event data
        await self.config.signups.set({})
        await self.config.matches.set({})
        await self.config.dm_confirm.set({})
        await self.config.ss_open.set(False) 
        
        # 2. Clear the message/channel IDs so the bot forgets the old embed location.
        # This prevents the view from being re-added in on_ready after restart.
        async with self.config.embed_data() as embed_data:
            embed_data["channel_id"] = None
            embed_data["message_id"] = None
        
        await ctx.send(
            "✅ All Secret Santa event data (sign-ups, matches) has been **cleared**.\n"
            "The location of the sign-up embed has been **forgotten**. You must run "
            "`[p]secretsanta setup` to post a new sign-up message."
        )

    @ss.command(name="redraw")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_redraw_embed(self, ctx: commands.Context):
        """Redraws the Secret Santa sign-up embed and button in the configured channel."""
        
        embed_data = await self.config.embed_data()
        channel_id = embed_data["channel_id"]
        old_message_id = embed_data["message_id"]

        if not channel_id:
            return await ctx.send("❌ Setup is incomplete. Please run `[p]secretsanta setup` first.")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ The configured channel no longer exists. Please run `[p]secretsanta setup` to set a new channel.")
            
        # 1. Try to delete the old message
        if old_message_id:
            try:
                old_message = await channel.fetch_message(old_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden):
                # Message already gone or bot can't delete it, which is fine
                pass

        # 2. Rebuild the embed
        embed = discord.Embed(
            title=embed_data["title"], 
            description=embed_data["description"], 
            color=await ctx.embed_color()
        )
        if embed_data["image"]:
            embed.set_image(url=embed_data["image"])
        
        # 3. Post the new message with the persistent View
        view = SantaButtonView(self)
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            return await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
            
        # 4. Update the config with the new message ID
        embed_data["message_id"] = message.id
        await self.config.embed_data.set(embed_data)
        
        # 5. Add the persistent view (important for interactions on the new message)
        self.bot.add_view(view)

        await ctx.send(
            f"✅ Secret Santa sign-up message successfully redrawn in {channel.mention}. "
            f"The new message ID (`{message.id}`) has been stored."
        )

    @ss.command(name="setlogchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where anonymous Secret Santa actions (like gift sent) will be logged."""
        await self.config.log_channel_id.set(channel.id)
        await ctx.send(
            f"✅ Anonymous action log channel set to {channel.mention}. "
            "Successful and failed anonymous communications will be logged here."
        )

    @ss.command(name="open")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_open_signup(self, ctx: commands.Context):
        """Opens the Secret Santa sign-up period. Users can click the button."""
        await self.config.ss_open.set(True)
        await ctx.send("✅ Secret Santa sign-ups are now **OPEN**! Users can click the button to join.")

    @ss.command(name="close")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_close_signup(self, ctx: commands.Context):
        """Closes the Secret Santa sign-up period. Button clicks will be denied."""
        await self.config.ss_open.set(False)
        await ctx.send("✅ Secret Santa sign-ups are now **CLOSED**. Users attempting to click the button will be notified.")
        
    @ss.command(name="match")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_match(self, ctx: commands.Context):
        """
        Closes sign-up, performs the Secret Santa matching, and DMs all participants.
        
        Matching prioritizes recipients in the same country as the Santa.
        """
        await self.config.ss_open.set(False) # Ensure it's closed before matching
        
        signups: Dict[str, Dict[str, str]] = await self.config.signups()
        participant_ids: List[str] = list(signups.keys())
        
        if len(participant_ids) < 2:
            return await ctx.send(f"❌ Not enough participants to perform a match (signed up: {len(participant_ids)}). At least 2 are required.")

        # --- Matching Logic ---
        
        givers: List[str] = participant_ids[:] 
        recipients_country_map: Dict[str, str] = {uid: signups[uid]["country"] for uid in participant_ids}
        matches: Dict[str, str] = {}
        
        random.shuffle(givers) # Randomize givers list
        
        # 1. First Pass: Match within the same country
        givers_to_match_later: List[str] = []
        
        for giver_id in givers:
            giver_country = recipients_country_map[giver_id]
            
            # Find all available recipients who are in the same country AND not already a recipient AND not the giver themselves
            same_country_recipients: Set[str] = set()
            for recipient_id, country in recipients_country_map.items():
                if (
                    recipient_id != giver_id and 
                    recipient_id not in matches.values() and 
                    country == giver_country
                ):
                    same_country_recipients.add(recipient_id)

            if same_country_recipients:
                # Prioritized Match found
                recipient_id = random.choice(list(same_country_recipients))
                matches[giver_id] = recipient_id
            else:
                # No in-country match available, defer to cross-country matching
                givers_to_match_later.append(giver_id)


        # 2. Second Pass: Match remaining givers with remaining recipients (cross-country)
        remaining_recipients: List[str] = [
            uid for uid in participant_ids 
            if uid not in matches.values()
        ]
        
        # Perform a derangement (circular shift) to ensure no one is matched with themselves
        
        remaining_givers_ids: List[str] = givers_to_match_later
        
        # Shuffle until a valid derangement is achieved (no giver matches themselves)
        attempt = 0
        while True:
            random.shuffle(remaining_recipients)
            
            valid_match = True
            if len(remaining_givers_ids) != len(remaining_recipients):
                # This should technically not happen if the logic is correct, but as a safeguard
                valid_match = False
            else:
                for giver_index, giver_id in enumerate(remaining_givers_ids):
                    if giver_id == remaining_recipients[giver_index]:
                        valid_match = False
                        break
            
            if valid_match:
                break
            
            attempt += 1
            if attempt > 1000 and len(remaining_givers_ids) > 1:
                # Highly unlikely, but prevents infinite loop on rare complex scenarios
                await ctx.send("⚠️ Failed to find a valid cross-country match after many attempts! Try again.")
                return

        # Finalize the remaining matches
        for giver_index, giver_id in enumerate(remaining_givers_ids):
            matches[giver_id] = remaining_recipients[giver_index]

        # 3. Store the final matches
        await self.config.matches.set(matches)
        
        # 4. DM the results and track confirmation
        dm_success_count = 0
        dm_confirm: Dict[str, bool] = {}
        
        for santa_id_str, recipient_id_str in matches.items():
            santa = self.bot.get_user(int(santa_id_str))
            recipient_info = signups.get(recipient_id_str, {})
            recipient_username = recipient_info.get("username", f"Unknown User (ID: {recipient_id_str})")
            recipient_country = recipient_info.get("country", "Unknown")
            recipient_wishlist = recipient_info.get("wishlist", "No wishlist URL provided.")
            
            if santa:
                try:
                    # Send the DM with the new SantaActionView
                    action_view = SantaActionView(self, santa.id)
                    await santa.send(
                        f"🎉 **Your Secret Santa Recipient!** 🎉\n\n"
                        f"Your recipient is **{recipient_username}**.\n"
                        f"Their location is **{recipient_country}**.\n"
                        f"Their Wishlist: <{recipient_wishlist}>\n\n" # Uses <URL> format for clickability
                        "It is important that this is kept a secret! Happy gifting!\n\n"
                        "--- **Anonymous Gifting Actions** ---\n"
                        "Use the buttons below to communicate anonymously with your recipient via the bot. "
                        "These messages are logged to the configured admin channel.",
                        view=action_view # Add the view here
                    )
                    dm_success_count += 1
                    dm_confirm[santa_id_str] = True
                except discord.Forbidden:
                    dm_confirm[santa_id_str] = False # User has DMs blocked
                except Exception:
                    dm_confirm[santa_id_str] = False # Other DM errors

        # Store DM confirmation status
        await self.config.dm_confirm.set(dm_confirm)
        
        # 5. Report results
        await ctx.send(
            f"✅ **Secret Santa Matching Complete!**\n"
            f"Total Participants: **{len(participant_ids)}**\n"
            f"Successful DMs: **{dm_success_count}/{len(participant_ids)}**\n"
            "Use `[p]secretsanta listmatches` to view the results and DM confirmations."
        )

    @ss.command(name="sendactionuser")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_send_action_user(self, ctx: commands.Context, user: discord.User):
        """
        Sends a dedicated DM with ONLY the Anonymous Gifting Action buttons
        to a single specified user (who must be a Santa).
        
        <user>: The user to send the buttons to (mention or ID).
        """
        user_id_str = str(user.id)
        matches = await self.config.matches()
        
        if user_id_str not in matches:
            return await ctx.send(f"❌ User **{user.name}** is either not signed up or has not been matched as a Secret Santa (giver).")
            
        await ctx.send(f"🔄 Attempting to send anonymous action buttons to **{user.name}**...")
        
        try:
            action_view = SantaActionView(self, user.id)
            await user.send(
                "--- **Anonymous Gifting Actions Update** ---\n\n"
                "The Secret Santa bot has been updated with new anonymous communication features. "
                "Use the buttons below to send anonymous status updates or requests to your recipient. "
                "These actions will be logged by the server administration.",
                view=action_view
            )
            await ctx.send(f"✅ Successfully sent anonymous action buttons to **{user.name}**.")
        except discord.Forbidden:
            await ctx.send(f"❌ Failed to send DM to **{user.name}**. They likely have DMs disabled.")
        except Exception as e:
            await ctx.send(f"❌ An unknown error occurred while trying to send the DM to **{user.name}**: {e}")

    @ss.command(name="sendrecipientactions")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_send_recipient_actions(self, ctx: commands.Context, recipient: discord.User):
        """
        Sends a dedicated DM with the 'Wishlist Fixed' button to a specific recipient.
        
        This allows the recipient to anonymously notify their Santa that they have updated their wishlist.
        <recipient>: The user receiving the gift (recipient).
        """
        recipient_id_str = str(recipient.id)
        matches = await self.config.matches()
        
        if not matches:
             return await ctx.send("❌ No matches found. You must run `[p]secretsanta match` first.")

        # Find the Santa for this recipient
        santa_id_str = None
        for s_id, r_id in matches.items():
            if r_id == recipient_id_str:
                santa_id_str = s_id
                break
        
        if not santa_id_str:
            return await ctx.send(f"❌ User **{recipient.name}** does not appear to be a recipient in the current match list.")

        await ctx.send(f"🔄 Attempting to send recipient action buttons to **{recipient.name}**...")

        try:
            # Create the view for the recipient to reply to the santa
            action_view = RecipientActionView(self, santa_id=int(santa_id_str), recipient_id=recipient.id)
            await recipient.send(
                "--- **Anonymous Recipient Actions** ---\n\n"
                "An administrator has triggered this message to provide you with communication options.\n"
                "If your Santa previously reported an issue with your wishlist, you can use the button below to anonymously notify them that it has been fixed.",
                view=action_view
            )
            await ctx.send(f"✅ Successfully sent recipient action buttons to **{recipient.name}**.")
        except discord.Forbidden:
            await ctx.send(f"❌ Failed to send DM to **{recipient.name}**. They likely have DMs disabled.")
        except Exception as e:
            await ctx.send(f"❌ An unknown error occurred while trying to send the DM to **{recipient.name}**: {e}")

    @ss.command(name="sendactions")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_send_actions(self, ctx: commands.Context):
        """
        Sends a dedicated DM with ONLY the Anonymous Gifting Action buttons
        to all users who have been successfully matched (Santas).
        
        This is useful for providing the buttons to users matched before this feature existed.
        """
        matches = await self.config.matches()
        
        if not matches:
            return await ctx.send("❌ No matches found. You must run `[p]secretsanta match` first.")
            
        santa_ids = list(matches.keys())
        await ctx.send(f"🔄 Attempting to send anonymous action buttons to **{len(santa_ids)}** matched Santas...")
        
        success_count = 0
        fail_count = 0
        
        for santa_id_str in santa_ids:
            santa = self.bot.get_user(int(santa_id_str))
            
            if santa:
                try:
                    action_view = SantaActionView(self, santa.id)
                    await santa.send(
                        "--- **Anonymous Gifting Actions Update** ---\n\n"
                        "The Secret Santa bot has been updated with new anonymous communication features. "
                        "Use the buttons below to send anonymous status updates or requests to your recipient. "
                        "These actions will be logged by the server administration.",
                        view=action_view
                    )
                    success_count += 1
                except discord.Forbidden:
                    fail_count += 1
                except Exception:
                    fail_count += 1
            else:
                # User left server or bot can't find them
                fail_count += 1
                
        await ctx.send(
            f"📊 **Anonymous Action Button Distribution Complete**\n"
            f"✅ Successfully Sent: **{success_count}**\n"
            f"❌ Failed (DMs blocked or user unavailable): **{fail_count}**"
        )


    @ss.command(name="retrydms")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_retry_dms(self, ctx: commands.Context):
        """
        Attempts to resend matching DMs to users who failed to receive them previously.
        Posts a summary of successes and remaining failures.
        """
        matches = await self.config.matches()
        dm_confirm = await self.config.dm_confirm()
        signups = await self.config.signups()
        
        if not matches:
            return await ctx.send("❌ No matches found. You must run `[p]secretsanta match` first.")
            
        retry_candidates = [sid for sid in matches if not dm_confirm.get(sid, False)]
        
        if not retry_candidates:
            return await ctx.send("✅ All participants have already successfully received their DMs!")
            
        await ctx.send(f"🔄 Attempting to resend DMs to **{len(retry_candidates)}** participants who didn't receive them...")
        
        retry_success = 0
        still_failed = 0
        
        for santa_id_str in retry_candidates:
            recipient_id_str = matches[santa_id_str]
            santa = self.bot.get_user(int(santa_id_str))
            
            recipient_info = signups.get(recipient_id_str, {})
            recipient_username = recipient_info.get("username", f"Unknown User (ID: {recipient_id_str})")
            recipient_country = recipient_info.get("country", "Unknown")
            recipient_wishlist = recipient_info.get("wishlist", "No wishlist URL provided.")
            
            if santa:
                try:
                    # Resend the DM with the new SantaActionView
                    action_view = SantaActionView(self, santa.id)
                    await santa.send(
                        f"🎉 **Your Secret Santa Recipient!** 🎉\n\n"
                        f"Your recipient is **{recipient_username}**.\n"
                        f"Their location is **{recipient_country}**.\n"
                        f"Their Wishlist: <{recipient_wishlist}>\n\n"
                        "It is important that this is kept a secret! Happy gifting!\n\n"
                        "--- **Anonymous Gifting Actions** ---\n"
                        "Use the buttons below to communicate anonymously with your recipient via the bot. "
                        "These messages are logged to the configured admin channel.",
                        view=action_view
                    )
                    dm_confirm[santa_id_str] = True
                    retry_success += 1
                except (discord.Forbidden, Exception):
                    still_failed += 1
            else:
                # User left server or bot can't find them
                still_failed += 1
                
        # Update config with new successes
        await self.config.dm_confirm.set(dm_confirm)
        
        await ctx.send(
            f"📊 **Retry Complete**\n"
            f"✅ Successfully Resent: **{retry_success}**\n"
            f"❌ Still Failed: **{still_failed}**\n"
            f"Use `[p]secretsanta listmatches` to see updated details."
        )

    @ss.command(name="userstatus")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_user_status(self, ctx: commands.Context):
        """Displays a status table of all Secret Santa participants."""
        signups = await self.config.signups()
        matches = await self.config.matches()
        dm_confirm = await self.config.dm_confirm()

        if not signups:
            return await ctx.send("❌ No participants have signed up yet.")

        # Check if any user has a timestamp
        has_timestamps = any(data.get("timestamp") for data in signups.values())

        table_data = []
        
        if has_timestamps:
            headers = ["Username", "Joined (UTC)", "Wishlist?", "Matched?", "DM Sent?"]
        else:
            headers = ["Username", "Wishlist?", "Matched?", "DM Sent?"]

        for user_id, data in signups.items():
            username = data.get("username", "Unknown")
            
            # Timestamp handling (only if at least one user has one)
            ts = data.get("timestamp")
            if has_timestamps:
                if ts:
                    joined_str = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")
                else:
                    joined_str = "N/A"

            # Wishlist check
            wishlist_ok = "✅" if data.get("wishlist") else "❌"

            # Match check
            matched_ok = "✅" if user_id in matches else "❌"

            # DM check
            # Note: dm_confirm contains {santa_id: bool}
            dm_ok = "✅" if dm_confirm.get(user_id) else "❌"

            if has_timestamps:
                table_data.append([username, joined_str, wishlist_ok, matched_ok, dm_ok])
            else:
                table_data.append([username, wishlist_ok, matched_ok, dm_ok])

        # Use tabulate for clean output. tablefmt="simple" works best inside Discord code blocks.
        output = tabulate(table_data, headers=headers, tablefmt="simple")
        
        await ctx.send(box(output))

    @ss.command(name="listwishlists")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_list_wishlists(self, ctx: commands.Context):
        """
        Displays a list of all participants, their country, and a clickable link
        to their Amazon Wishlist, split into multiple embeds if necessary.
        """
        signups = await self.config.signups()

        if not signups:
            return await ctx.send("❌ No participants have signed up yet.")

        # Prepare list of entries: [Username (Country)](<Wishlist URL>)
        entries = []
        for data in signups.values():
            username = data.get("username", "Unknown User")
            country = data.get("country", "N/A")
            wishlist_url = data.get("wishlist")
            
            if wishlist_url:
                # Use Markdown link format: [Text](<URL>) with angle brackets for suppression
                entry = f"**[{username} ({country})](<{wishlist_url}>)**"
            else:
                entry = f"**{username} ({country})** - *No Wishlist Provided*"
            
            entries.append(entry)

        # Splitting logic: Max entries per embed for readability and safety
        CHUNKS_PER_MESSAGE = 25
        
        # Split the list of entries into chunks
        chunks = [entries[i:i + CHUNKS_PER_MESSAGE] for i in range(0, len(entries), CHUNKS_PER_MESSAGE)]
        
        await ctx.send(f"Found {len(entries)} participants. Generating {len(chunks)} message(s)...")

        for i, chunk in enumerate(chunks):
            description = "\n".join(chunk)
            
            embed = discord.Embed(
                title=f"🎁 Secret Santa Wishlists (Part {i+1}/{len(chunks)})",
                description=description,
                color=await ctx.embed_color()
            )
            embed.set_footer(text="Wishlists are crucial for successful Secret Santa matching.")
            
            # Send each chunk as a separate message
            await ctx.send(embed=embed)

        await ctx.send("✅ All wishlists have been displayed.")

    @ss.command(name="listmatches")
    @commands.admin_or_permissions(manage_guild=True)
    async def ss_list_matches(self, ctx: commands.Context):
        """Lists who got who and the DM confirmation status (for admin use)."""
        
        matches = await self.config.matches()
        dm_confirm = await self.config.dm_confirm()
        signups = await self.config.signups()
        
        if not matches:
            return await ctx.send("❌ No Secret Santa matches have been generated yet.")

        output = ["Secret Santa Match Results (Giver -> Recipient)"]
        for santa_id_str, recipient_id_str in matches.items():
            santa_info = signups.get(santa_id_str, {})
            recipient_info = signups.get(recipient_id_str, {})
            
            # Fallback names
            santa_name = santa_info.get("username", f"User ID: {santa_id_str}")
            recipient_name = recipient_info.get("username", f"User ID: {recipient_id_str}")
            
            # DM Status
            dm_status = "SUCCESS" if dm_confirm.get(santa_id_str) else "FAILED"
            
            # Wishlist Preview
            wishlist_preview = recipient_info.get('wishlist', 'N/A')[:40] + '...'
            
            output.append(
                f"{santa_name} (Country: {santa_info.get('country', 'N/A')}) "
                f"--> {recipient_name} (Country: {recipient_info.get('country', 'N/A')}) [DM: {dm_status}]\n"
                f"    Recipient Wishlist: {wishlist_preview}"
            )
            
        await ctx.send(box('\n'.join(output), lang="css"))
        
    @ss.command(name="reset", hidden=True)
    @commands.is_owner()
    async def ss_reset(self, ctx: commands.Context):
        """DANGEROUS: Fully resets all Secret Santa data (signups, matches, config)."""
        await self.config.clear_all_global()
        # The bot will no longer add the persistent view because the config is cleared.
        await ctx.send("⚠️ All Secret Santa data has been completely **RESET**. You will need to use `[p]secretsanta setup` again.")


async def setup(bot: Red):
    """Entry point for RedBot to load the cog."""
    await bot.add_cog(SecretSanta(bot))