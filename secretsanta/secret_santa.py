import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
import random
from typing import Optional, List, Dict, Set

# --- UI Components ---

class SecretSantaModal(discord.ui.Modal, title="Secret Santa Sign-Up"):
    """Discord Modal for users to enter their sign-up information."""
    
    # User's current username is captured automatically via interaction.user.
    
    country = discord.ui.TextInput(
        label="Your Country",
        placeholder="e.g., Canada, United States, Japan",
        max_length=100,
        style=discord.TextStyle.short,
    )

    confirmation = discord.ui.TextInput(
        label="Type 'YES' to confirm sign-up",
        placeholder="This confirms you understand the commitment.",
        max_length=5,
        style=discord.TextStyle.short,
    )
    
    def __init__(self, cog: "SecretSanta") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        # Basic validation
        if self.confirmation.value.upper() != "YES":
            await interaction.response.send_message(
                "❌ You must type 'YES' to confirm your understanding and sign up.", 
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        country = self.country.value.strip()

        # Check if sign-ups are open
        if not await self.cog.config.ss_open():
            await interaction.response.send_message(
                "❌ Secret Santa sign-ups are currently closed.", 
                ephemeral=True
            )
            return

        # Record the sign-up
        async with self.cog.config.signups() as signups:
            # Store the User ID as a string key because JSON keys must be strings
            signups[str(user_id)] = {"country": country, "username": str(interaction.user)}
        
        await interaction.response.send_message(
            f"✅ You have successfully signed up for Secret Santa! Country: **{country}**", 
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
            "signups": {},     # {user_id_str: {"country": "...", "username": "..."}}
            "matches": {},     # {santa_user_id_str: recipient_user_id_str}
            "dm_confirm": {},  # {santa_user_id_str: True/False}
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
            
            if santa:
                try:
                    await santa.send(
                        f"🎉 **Your Secret Santa Recipient!** 🎉\n\n"
                        f"Your recipient is **{recipient_username}**.\n"
                        f"Their location is **{recipient_country}**.\n\n"
                        "It is important that this is kept a secret! Happy gifting!"
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
            
            output.append(
                f"{santa_name} (Country: {santa_info.get('country', 'N/A')}) "
                f"--> {recipient_name} (Country: {recipient_info.get('country', 'N/A')}) [DM: {dm_status}]"
            )
            
        await ctx.send(box('\n'.join(output), lang="css"))
        
    @ss.command(name="reset", hidden=True)
    @commands.is_owner()
    async def ss_reset(self, ctx: commands.Context):
        """DANGEROUS: Fully resets all Secret Santa data (signups, matches, config)."""
        await self.config.clear_all_global()
        # Remove the persistent view so it doesn't get re-added on restart
        self.bot.remove_view("secret_santa_signup_button")
        await ctx.send("⚠️ All Secret Santa data has been completely **RESET**. You will need to use `[p]secretsanta setup` again.")


async def setup(bot: Red):
    """Entry point for RedBot to load the cog."""
    await bot.add_cog(SecretSanta(bot))