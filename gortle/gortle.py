import discord
import random
import asyncio
import re
import datetime
import json
import os # <-- Added this import
from typing import Optional, Literal
from collections import Counter

from redbot.core import commands, Config, bank, checks
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.chat_formatting import pagify, box

class Gortle(commands.Cog):
    """A communal 6-letter Wordle-style game for Discord."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473629103, force_registration=True)

        # Initialize lists and load them immediately
        self.solutions = []
        self.guesses = []
        self._load_word_lists()

        default_global = {
            "game_schedule_min": 0, 
            "next_game_timestamp": 0,
            "current_game_id": 0,
            "current_word": None,
            "used_words": [],
            "game_number": 0,
            "game_active": False,
            "game_state": {
                "solved_indices": [],
                "found_letters": [],
                "guessed_letters": [],
                "guesses_made": 0
            },
            "win_amount": 100,
            "weekly_role_id": None,
            "weekly_role_day": 0,
            "weekly_role_hour": 9,
            "last_weekly_award": 0
        }

        default_guild = {
            "channel_id": None,
            "mention_role": None,
            "cooldown_seconds": 60
        }

        default_member = {
            "score": 0,
            "weekly_score": 0,
            "words_guessed": {},
            "last_guess_time": 0
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        self.game_loop_task = self.bot.loop.create_task(self.game_loop())
        self.lock = asyncio.Lock()

    def cog_unload(self):
        if self.game_loop_task:
            self.game_loop_task.cancel()

    def _load_word_lists(self):
        """Loads words from JSON files in the data directory."""
        # bundled_data_path(self) points to the root of the cog folder: .../cogs/gortle/
        base_path = bundled_data_path(self)
        
        # Correct path construction: .../gortle/data/solutions.json
        solutions_path = base_path / "data" / "solutions.json"
        guesses_path = base_path / "data" / "guesses.json"

        # --- Enhanced Debugging ---
        print("-" * 40)
        print(f"Gortle: Checking Solutions Path: {solutions_path}")
        print(f"Gortle: Solutions File Exists: {os.path.exists(solutions_path)}")
        print(f"Gortle: Checking Guesses Path: {guesses_path}")
        print(f"Gortle: Guesses File Exists: {os.path.exists(guesses_path)}")
        print("-" * 40)
        # --------------------------
        
        try:
            # Check if the file exists before trying to open it
            if not os.path.exists(solutions_path) or not os.path.exists(guesses_path):
                raise FileNotFoundError("One or both word list files are missing or path is wrong.")
            
            # Note: Pathlib support (using / operator for joining) is robust
            with open(solutions_path, "r", encoding="utf-8") as f:
                self.solutions = json.load(f)
            
            with open(guesses_path, "r", encoding="utf-8") as f:
                raw_guesses = json.load(f)
                
            # Combine and deduplicate to ensure solutions are valid guesses
            combined = set(raw_guesses + self.solutions)
            self.guesses = list(combined)
            
        except FileNotFoundError as e:
            print(f"[Gortle] CRITICAL: Word list files not found. Check cog/data/ structure. Error: {e}")
            self.solutions = ["failed"]
            self.guesses = ["failed"]
        except json.JSONDecodeError as e:
            print(f"[Gortle] CRITICAL: Error parsing JSON in word lists. Error: {e}")
            self.solutions = ["failed"]
            self.guesses = ["failed"]
        
        # Add the final check print here to confirm the action was taken
        print(f"Gortle: _load_word_lists finished. Solutions: {len(self.solutions)}, Guesses: {len(self.guesses)}")


    async def game_loop(self):
        """Checks schedule for new games and weekly roles."""
        await self.bot.wait_until_ready()
        while True:
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                timestamp = int(now.timestamp())

                # 1. Check Weekly Role
                await self.check_weekly_role(now)

                # 2. Check Game Schedule
                next_game = await self.config.next_game_timestamp()
                schedule_min = await self.config.game_schedule_min()
                
                if schedule_min > 0:
                    if timestamp >= next_game:
                        await self.start_new_game()
                        next_ts = timestamp + (schedule_min * 60)
                        await self.config.next_game_timestamp.set(next_ts)

            except Exception as e:
                print(f"Error in Gortle loop: {e}")
            
            await asyncio.sleep(60)

    async def start_new_game(self, manual=False):
        async with self.lock:
            # Check if previous game needs revealing
            active = await self.config.game_active()
            old_word = await self.config.current_word()
            
            guild_config = await self.config.all_guilds()
            target_channel = None
            
            # Find a guild with a configured channel to post the new game
            for gid, data in guild_config.items():
                if data['channel_id']:
                    g = self.bot.get_guild(gid)
                    if g:
                        c = g.get_channel(data['channel_id'])
                        if c:
                            target_channel = c
                            break
            
            if not target_channel:
                return

            if active and old_word:
                embed = discord.Embed(title="Gortle Expired!", description=f"The word was **{old_word.upper()}**.", color=discord.Color.red())
                await target_channel.send(embed=embed)

            # Pick new word
            used = await self.config.used_words()
            available = [w for w in self.solutions if w not in used]
            
            if not available:
                used = []
                await self.config.used_words.set([])
                available = self.solutions

            if not available:
                print("[Gortle] No words available in solutions.json! Check _load_word_lists output.")
                return

            new_word = random.choice(available)
            
            async with self.config.used_words() as u:
                u.append(new_word)

            game_num = await self.config.game_number() + 1
            await self.config.game_number.set(game_num)
            await self.config.current_word.set(new_word)
            await self.config.game_active.set(True)
            
            # Reset State
            new_state = {
                "solved_indices": [],
                "found_letters": [],
                "guessed_letters": [],
                "guesses_made": 0
            }
            await self.config.game_state.set(new_state)

            # Announce
            role_id = await self.config.guild(target_channel.guild).mention_role()
            mention = f"<@&{role_id}>" if role_id else ""
            
            embed = discord.Embed(title=f"New Gortle Started! (#{game_num})", description="Guess the 6-letter word by mentioning me! (`@BotName [6-letter-word]`)", color=discord.Color.green())
            await target_channel.send(content=mention, embed=embed)

    async def check_weekly_role(self, now):
        target_day = await self.config.weekly_role_day()
        target_hour = await self.config.weekly_role_hour()
        last_award = await self.config.last_weekly_award()
        role_id = await self.config.weekly_role_id()

        if not role_id:
            return

        # Check if it's the target day and hour, and if a full week has passed since the last award
        if now.weekday() == target_day and now.hour >= target_hour:
            # 500000 seconds is approx 5.7 days, ensures we only run once per week
            if (now.timestamp() - last_award) > 500000: 
                await self.award_weekly_role(role_id)
                await self.config.last_weekly_award.set(int(now.timestamp()))

    async def award_weekly_role(self, role_id):
        # Get all member data across all guilds
        members_data = await self.config.all_members()
        
        guild_config = await self.config.all_guilds()
        target_guild = None
        target_channel = None
        
        # Try to find a guild and channel to use for announcements and role management
        for gid, data in guild_config.items():
            if data['channel_id']:
                g = self.bot.get_guild(gid)
                if g:
                    target_guild = g
                    target_channel = g.get_channel(data['channel_id'])
                    break
        
        if not target_channel or not target_guild:
            return

        role = target_guild.get_role(role_id)
        if not role:
            print(f"[Gortle] Weekly role ID {role_id} not found in guild {target_guild.id}.")
            return

        # 1. Remove role from all current holders
        for member in role.members:
            try:
                await member.remove_roles(role, reason="Gortle weekly reset")
            except discord.Forbidden:
                print(f"[Gortle] Cannot remove role from {member.name}, forbidden.")
            except Exception:
                pass

        # 2. Find the top scorer across the bot instance's tracked members
        top_scorer_id = None
        top_score = -1
        
        for g_id, m_data in members_data.items():
            for m_id, stats in m_data.items():
                if stats.get('weekly_score', 0) > top_score:
                    top_score = stats['weekly_score']
                    top_scorer_id = m_id
        
        top_scorer = target_guild.get_member(top_scorer_id) if top_scorer_id else None

        if top_scorer and top_score > 0:
            try:
                await top_scorer.add_roles(role, reason="Gortle weekly winner")
                await target_channel.send(f"🏆 **{top_scorer.mention}** is the Gortle Champion of the week with **{top_score}** points! The weekly score has been reset.")
            except discord.Forbidden:
                await target_channel.send("I tried to give the weekly role but lack permissions. Please check my role hierarchy.")
        else:
             await target_channel.send("Weekly Gortle scores have been reset. No winner found this week.")

        # 3. Reset all weekly scores
        for g_id, m_data in members_data.items():
            for m_id in m_data.keys():
                await self.config.member_from_ids(g_id, m_id).weekly_score.set(0)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not self.bot.user in message.mentions:
            return

        conf_channel = await self.config.guild(message.guild).channel_id()
        if message.channel.id != conf_channel:
            return

        content = message.content.lower()
        # Regex to find exactly a 6-letter word boundary (to ignore prefixes/suffixes like "itsword")
        match = re.search(r"\b[a-z]{6}\b", content)
        
        if not match:
            return 

        guess = match.group(0)
        
        if not await self.config.game_active():
            return

        # Check if the word is in the combined dictionary (self.guesses)
        if guess not in self.guesses:
            await message.channel.send("I do not think that word is in my dictionary.", delete_after=5)
            return

        # Cooldown check
        cooldown = await self.config.guild(message.guild).cooldown_seconds()
        last_guess = await self.config.member(message.author).last_guess_time()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        if (now - last_guess) < cooldown:
            try:
                await message.delete()
                next_time = int(last_guess + cooldown)
                await message.channel.send(f"{message.author.mention}, you need to wait. Next guess: <t:{next_time}:R>", delete_after=5)
            except discord.Forbidden:
                # Bot doesn't have permissions to delete or send messages quickly
                pass
            return

        # Update last guess time
        await self.config.member(message.author).last_guess_time.set(int(now))
        
        # Process the guess
        async with self.lock:
            await self.process_guess(message, guess)

    async def process_guess(self, message, guess):
        solution = await self.config.current_word()
        state = await self.config.game_state()
        solved_indices = set(state['solved_indices'])
        
        emojis = [""] * 6
        points = 0
        
        sol_chars = list(solution)
        guess_chars = list(guess)
        sol_remaining = list(solution) 
        
        # First Pass: Check for 🔵 (Correct letter, correct position)
        for i, char in enumerate(guess_chars):
            if char == sol_chars[i]:
                emojis[i] = "🔵" # Blue Circle for perfect match
                sol_remaining[i] = None # Remove from remaining pool
                
                if i not in solved_indices:
                    points += 2
                    state['solved_indices'].append(i)
                    state['found_letters'].append(char)
                # Else: already found, no extra points

        # Second Pass: Check for 🟠 (Correct letter, wrong position)
        for i, char in enumerate(guess_chars):
            if emojis[i] != "": continue # Skip already matched characters

            if char in sol_remaining:
                emojis[i] = "🟠" # Orange Circle for found letter
                sol_remaining[sol_remaining.index(char)] = None # Remove from remaining pool

                # Only award points if this letter hasn't been fully accounted for
                total_in_sol = solution.count(char)
                known_count = state['found_letters'].count(char)
                
                if known_count < total_in_sol:
                    points += 1
                    state['found_letters'].append(char)
            else:
                emojis[i] = "⚫" # Black Circle for not found letter

        # Update guessed letters for display
        current_guessed = set(state['guessed_letters'])
        for c in guess:
            current_guessed.add(c)
        state['guessed_letters'] = sorted(list(current_guessed))
        
        # Save state and update score
        await self.config.game_state.set(state)

        async with self.config.member(message.author).words_guessed() as wg:
            wg[guess] = wg.get(guess, 0) + 1
        
        if points > 0:
            await self.config.member(message.author).score.set(
                await self.config.member(message.author).score() + points
            )
            await self.config.member(message.author).weekly_score.set(
                await self.config.member(message.author).weekly_score() + points
            )

        # Build and send feedback embed
        game_num = await self.config.game_number()
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        
        # Determine guessed/pending letters for a status keyboard
        guessed_chars = set(state['guessed_letters'])
        
        keyboard_status = ""
        for char in alphabet:
            if char in guessed_chars:
                # Mark letters that were guessed. Cannot differentiate between 🔵 and 🟠 yet without more state
                keyboard_status += f"**{char}** " 
            else:
                keyboard_status += f"{char} "

        embed = discord.Embed(title=f"Gortle #{game_num}", color=discord.Color.blue())
        embed.add_field(name="Your Guess", value=f"`{guess.upper()}`\n{''.join(emojis)}", inline=False)
        embed.add_field(name="Points Gained", value=str(points), inline=True)
        embed.add_field(name="Total Score", value=str(await self.config.member(message.author).score()), inline=True)
        embed.add_field(name="Letters Guessed", value=keyboard_status, inline=False)
        
        await message.channel.send(embed=embed)

        if guess == solution:
            await self.handle_win(message.author, message.channel, game_num)

    async def handle_win(self, winner, channel, game_num):
        await self.config.game_active.set(False)
        prize = await self.config.win_amount()
        
        try:
            await bank.deposit_credits(winner, prize)
            currency = await bank.get_currency_name(channel.guild)
        except Exception:
            currency = "credits" # Fallback if bank is not configured

        embed = discord.Embed(title=f"Gortle #{game_num} Solved!", 
                              description=f"**{winner.mention}** guessed the word correctly!", 
                              color=discord.Color.gold())
        embed.add_field(name="Solution", value=await self.config.current_word())
        embed.add_field(name="Prize", value=f"{prize} {currency}")
        
        await channel.send(embed=embed)

    # --- Commands ---

    @commands.command()
    async def gortletop(self, ctx):
        """Shows the Gortle leaderboard."""
        members = await self.config.all_members(ctx.guild)
        if not members:
            return await ctx.send("No scores yet.")

        # Filter out members with score 0 and sort
        valid_members = {uid: data for uid, data in members.items() if data.get('score', 0) > 0}
        
        sorted_data = sorted(valid_members.items(), key=lambda x: x[1]['score'], reverse=True)
        
        msg = ""
        for i, (uid, data) in enumerate(sorted_data[:10], 1):
            user = ctx.guild.get_member(uid)
            name = user.display_name if user else f"Unknown User ({uid})"
            msg += f"**{i}.** **{name}**: {data.get('score', 0)} points (Weekly: {data.get('weekly_score', 0)})\n"
        
        if not msg:
            return await ctx.send("No users have scored any points yet!")

        embed = discord.Embed(title="Gortle Leaderboard (Top 10)", description=msg, color=discord.Color.blue())
        await ctx.send(embed=embed)

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def gortleset(self, ctx):
        """Configuration for Gortle."""
        pass

    @gortleset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for Gortle games."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Gortle channel set to {channel.mention}. Game announcements will be posted here.")

    @gortleset.command()
    async def role(self, ctx, role: discord.Role):
        """Set the role to mention for new games."""
        await self.config.guild(ctx.guild).mention_role.set(role.id)
        await ctx.send(f"Notification role set to {role.name}. This role will be mentioned when a new game starts.")

    @gortleset.command()
    async def schedule(self, ctx, minutes: int):
        """Set how often new games post (in minutes). Set to 0 to disable auto-posting."""
        if minutes < 0:
            return await ctx.send("Minutes must be 0 or greater.")
        await self.config.game_schedule_min.set(minutes)
        if minutes > 0:
            await ctx.send(f"Game schedule set to start a new game every **{minutes}** minutes.")
        else:
            await ctx.send("Automatic game starting is now **disabled**. Use `[p]gortleset manualstart` to begin games.")

    @gortleset.command()
    async def cooldown(self, ctx, seconds: int):
        """Set the user guess cooldown in seconds."""
        if seconds < 5:
            return await ctx.send("Cooldown must be 5 seconds or more to prevent spam.")
        await self.config.guild(ctx.guild).cooldown_seconds.set(seconds)
        await ctx.send(f"Cooldown set to **{seconds}** seconds per user guess.")

    @gortleset.command()
    async def prize(self, ctx, amount: int):
        """Set the bank credit prize for winning."""
        if amount < 0:
            return await ctx.send("Prize amount cannot be negative.")
        currency = await bank.get_currency_name(ctx.guild)
        await self.config.win_amount.set(amount)
        await ctx.send(f"Winner prize set to **{amount} {currency}**.")

    @gortleset.command()
    async def weekly(self, ctx, role: discord.Role, day: int, hour: int):
        """Configure the weekly winner role.
        Day: 0=Monday, 6=Sunday. Hour: 0-23 (UTC)."""
        if not (0 <= day <= 6) or not (0 <= hour <= 23):
            return await ctx.send("Day must be 0-6 (0=Monday, 6=Sunday). Hour must be 0-23 (UTC).")
        
        await self.config.weekly_role_id.set(role.id)
        await self.config.weekly_role_day.set(day)
        await self.config.weekly_role_hour.set(hour)
        await ctx.send(f"Weekly role {role.name} will be awarded on day **{day}** (UTC) at hour **{hour}** (UTC), and weekly scores will reset.")

    @gortleset.command()
    async def manualstart(self, ctx):
        """Force start a new game immediately."""
        await self.start_new_game(manual=True)
        await ctx.send("Game started.")

    @gortleset.command()
    async def reloadlists(self, ctx):
        """Reloads the word lists from the file system."""
        self._load_word_lists()
        await ctx.send(f"Lists reloaded. Solutions: {len(self.solutions)}, Guesses: {len(self.guesses)}")

    # --- Admin Cleanup ---

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def gortleadmin(self, ctx):
        """Admin management for leaderboards and state."""
        pass

    @gortleadmin.command()
    async def clearall(self, ctx):
        """Clear all member scores (global and weekly) in this guild."""
        
        # Confirmation check (since we can't use confirm())
        await ctx.send("Are you absolutely sure you want to clear ALL Gortle scores and stats for every member in this server? Type 'yes' to confirm.")
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'yes'

        try:
            await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return await ctx.send("Score clearing cancelled.")
        
        await self.config.clear_all_members(ctx.guild)
        await ctx.send("All member scores and statistics have been cleared for this server.")

    @gortleadmin.command()
    async def removeuser(self, ctx, member: discord.Member):
        """Remove a specific user from stats."""
        await self.config.member(member).clear()
        await ctx.send(f"Stats cleared for {member.display_name}.")

    @gortleadmin.command()
    async def clean(self, ctx):
        """Remove users from the stats who are no longer in the server."""
        members = await self.config.all_members(ctx.guild)
        count = 0
        for uid in list(members.keys()):
            if not ctx.guild.get_member(uid):
                # Using member_from_ids to target the specific guild's member data
                await self.config.member_from_ids(ctx.guild.id, uid).clear()
                count += 1
        await ctx.send(f"Removed {count} users no longer in the server's Gortle statistics.")