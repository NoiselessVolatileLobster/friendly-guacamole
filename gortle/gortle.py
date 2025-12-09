import discord
import random
import asyncio
import re
import datetime
import json
import os
from typing import Optional, Literal
from collections import Counter

from redbot.core import commands, Config, bank, checks
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.chat_formatting import pagify, box

class Gortle(commands.Cog):
    """A communal 6-letter Wordle-style game for Discord."""
    
    # Configuration for Emoji colors
    # You can change these strings if your emoji names differ
    EMOJI_UNUSED = "pastelred"   # Letters that have not been guessed
    EMOJI_CORRECT = "pastelgreen" # Guessed and in right position
    EMOJI_PRESENT = "pastelyellow" # Guessed and in wrong position
    EMOJI_ABSENT = "pastelblack"  # Guessed and NOT in the word (inferred)

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473629103, force_registration=True)

        # Initialize lists
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
                "guessed_letters": [], # Letters guessed at least once
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
        data_path = bundled_data_path(self)
        
        try:
            with open(data_path / "solutions.json", "r", encoding="utf-8") as f:
                self.solutions = json.load(f)
            
            with open(data_path / "guesses.json", "r", encoding="utf-8") as f:
                raw_guesses = json.load(f)
                
            # Combine and deduplicate to ensure solutions are valid guesses
            combined = set(raw_guesses + self.solutions)
            self.guesses = list(combined)
            
        except FileNotFoundError as e:
            print(f"[Gortle] Error loading word lists: {e}")
            self.solutions = ["failed"]
            self.guesses = ["failed"]
        except json.JSONDecodeError as e:
            print(f"[Gortle] Error parsing JSON: {e}")
            self.solutions = ["failed"]
            self.guesses = ["failed"]

    def _get_emoji_str(self, char: str, color: str) -> str:
        """Helper to format the emoji string."""
        return f":{color}{char.upper()}:"

    def _get_keyboard_visual(self, state, solution) -> str:
        """Generates the QWERTY keyboard visual based on game state."""
        rows = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        visual_rows = []

        guessed_letters = set(state['guessed_letters'])
        # Solved indices allows us to know which letters are definitively Green
        # However, to color the keyboard Green, we need to know if a letter is solved *anywhere*
        
        # Determine global status for each letter in the alphabet
        # Status priority: Green > Yellow > Black > Red (Default)
        
        # Pre-calculate status for efficiency
        letter_status = {}
        
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ".lower():
            if char not in guessed_letters:
                letter_status[char] = self.EMOJI_UNUSED
            else:
                if char not in solution:
                    letter_status[char] = self.EMOJI_ABSENT
                else:
                    # It is in the solution and has been guessed.
                    # Is it solved (Green) or just found (Yellow)?
                    # Check if this char exists in any solved position
                    is_solved = False
                    for idx in state['solved_indices']:
                        if solution[idx] == char:
                            is_solved = True
                            break
                    
                    if is_solved:
                        letter_status[char] = self.EMOJI_CORRECT
                    else:
                        letter_status[char] = self.EMOJI_PRESENT

        for row in rows:
            line = ""
            for char in row.lower():
                color = letter_status.get(char, self.EMOJI_UNUSED)
                line += self._get_emoji_str(char, color) + " "
            visual_rows.append(line.strip())
            
        return "\n".join(visual_rows)

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
                print("[Gortle] No words available in solutions.json!")
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
            
            # Show empty keyboard for new game
            keyboard_view = self._get_keyboard_visual(new_state, new_word)
            
            embed = discord.Embed(title=f"New Gortle Started! (#{game_num})", description="Guess the 6-letter word by mentioning me!", color=discord.Color.green())
            embed.add_field(name="Keyboard", value=keyboard_view, inline=False)
            await target_channel.send(content=mention, embed=embed)

    async def check_weekly_role(self, now):
        target_day = await self.config.weekly_role_day()
        target_hour = await self.config.weekly_role_hour()
        last_award = await self.config.last_weekly_award()
        role_id = await self.config.weekly_role_id()

        if not role_id:
            return

        if now.weekday() == target_day and now.hour >= target_hour:
            if (now.timestamp() - last_award) > 500000: 
                await self.award_weekly_role(role_id)
                await self.config.last_weekly_award.set(int(now.timestamp()))

    async def award_weekly_role(self, role_id):
        members = await self.config.all_members()
        
        guild_config = await self.config.all_guilds()
        target_channel = None
        for gid, data in guild_config.items():
            if data['channel_id']:
                g = self.bot.get_guild(gid)
                if g:
                    target_channel = g.get_channel(data['channel_id'])
                    break
        
        if not target_channel:
            return

        role = target_channel.guild.get_role(role_id)
        if not role:
            return

        for member in role.members:
            try:
                await member.remove_roles(role, reason="Gortle weekly reset")
            except:
                pass

        top_scorer = None
        top_score = -1
        
        for g_id, m_data in members.items():
            for m_id, stats in m_data.items():
                if stats['weekly_score'] > top_score:
                    top_score = stats['weekly_score']
                    top_scorer = target_channel.guild.get_member(m_id)

        if top_scorer and top_score > 0:
            try:
                await top_scorer.add_roles(role, reason="Gortle weekly winner")
                await target_channel.send(f"醇 **{top_scorer.mention}** is the Gortle Champion of the week with {top_score} points!")
            except discord.Forbidden:
                await target_channel.send("I tried to give the weekly role but lack permissions.")
        
        for g_id, m_data in members.items():
            for m_id, stats in m_data.items():
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
        match = re.search(r"\b[a-z]{6}\b", content)
        
        if not match:
            return 

        guess = match.group(0)
        
        if not await self.config.game_active():
            return

        # Use self.guesses instead of constant
        if guess not in self.guesses:
            await message.channel.send("I do not think that word is in my dictionary.", delete_after=5)
            return

        cooldown = await self.config.guild(message.guild).cooldown_seconds()
        last_guess = await self.config.member(message.author).last_guess_time()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        if (now - last_guess) < cooldown:
            try:
                await message.delete()
                next_time = int(last_guess + cooldown)
                await message.channel.send(f"{message.author.mention}, you need to wait. Next guess: <t:{next_time}:R>", delete_after=5)
            except discord.Forbidden:
                pass
            return

        await self.config.member(message.author).last_guess_time.set(int(now))
        async with self.lock:
            await self.process_guess(message, guess)

    async def process_guess(self, message, guess):
        solution = await self.config.current_word()
        state = await self.config.game_state()
        solved_indices = set(state['solved_indices'])
        
        # Prepare list for the row visual (the specific guess)
        guess_visual = [""] * 6
        points = 0
        
        sol_chars = list(solution)
        guess_chars = list(guess)
        sol_remaining = list(solution) 
        
        # 1. Pass for GREENS (Correct Position)
        for i, char in enumerate(guess_chars):
            if char == sol_chars[i]:
                # Correct!
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_CORRECT)
                sol_remaining[i] = None 
                
                if i not in solved_indices:
                    points += 2
                    state['solved_indices'].append(i)
                    state['found_letters'].append(char)
                else:
                    points += 0 

        # 2. Pass for YELLOWS (Wrong Position) and ABSENT (Wrong Word)
        for i, char in enumerate(guess_chars):
            if guess_visual[i] != "": 
                continue # Skip already marked greens

            if char in sol_remaining:
                # Present but wrong spot
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_PRESENT)
                sol_remaining[sol_remaining.index(char)] = None 
                
                total_in_sol = solution.count(char)
                known_count = state['found_letters'].count(char)
                
                if known_count < total_in_sol:
                    points += 1
                    state['found_letters'].append(char)
            else:
                # Absent
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_ABSENT)

        # Update guessed letters history
        current_guessed = set(state['guessed_letters'])
        for c in guess:
            current_guessed.add(c)
        state['guessed_letters'] = sorted(list(current_guessed))
        
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

        game_num = await self.config.game_number()
        
        # Generate the Keyboard View
        keyboard_view = self._get_keyboard_visual(state, solution)

        embed = discord.Embed(title=f"Gortle #{game_num}", color=discord.Color.blue())
        # Display the guess using the new emoji row
        embed.add_field(name="Your Guess", value=' '.join(guess_visual), inline=False)
        embed.add_field(name="Points Gained", value=str(points), inline=True)
        # Add the keyboard visual
        embed.add_field(name="Keyboard", value=keyboard_view, inline=False)
        
        await message.channel.send(embed=embed)

        if guess == solution:
            await self.handle_win(message.author, message.channel, game_num)

    async def handle_win(self, winner, channel, game_num):
        await self.config.game_active.set(False)
        prize = await self.config.win_amount()
        
        try:
            await bank.deposit_credits(winner, prize)
            currency = await bank.get_currency_name(channel.guild)
        except:
            currency = "credits"

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

        sorted_data = sorted(members.items(), key=lambda x: x[1]['score'], reverse=True)
        msg = ""
        for i, (uid, data) in enumerate(sorted_data[:10], 1):
            user = ctx.guild.get_member(uid)
            name = user.display_name if user else "Unknown User"
            msg += f"{i}. **{name}**: {data['score']} points (Weekly: {data['weekly_score']})\n"

        embed = discord.Embed(title="Gortle Leaderboard", description=msg, color=discord.Color.blue())
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
        await ctx.send(f"Gortle channel set to {channel.mention}")

    @gortleset.command()
    async def role(self, ctx, role: discord.Role):
        """Set the role to verify/mention for new games."""
        await self.config.guild(ctx.guild).mention_role.set(role.id)
        await ctx.send(f"Notification role set to {role.name}")

    @gortleset.command()
    async def schedule(self, ctx, minutes: int):
        """Set how often new games post (in minutes). Set to 0 to disable auto-posting."""
        await self.config.game_schedule_min.set(minutes)
        await ctx.send(f"Schedule set to every {minutes} minutes.")

    @gortleset.command()
    async def cooldown(self, ctx, seconds: int):
        """Set the user guess cooldown in seconds."""
        await self.config.guild(ctx.guild).cooldown_seconds.set(seconds)
        await ctx.send(f"Cooldown set to {seconds} seconds.")

    @gortleset.command()
    async def prize(self, ctx, amount: int):
        """Set the bank credit prize for winning."""
        await self.config.win_amount.set(amount)
        await ctx.send(f"Winner prize set to {amount}.")

    @gortleset.command()
    async def weekly(self, ctx, role: discord.Role, day: int, hour: int):
        """Configure the weekly winner role.
        Day: 0=Monday, 6=Sunday. Hour: 0-23 (UTC)."""
        if not (0 <= day <= 6) or not (0 <= hour <= 23):
            return await ctx.send("Day must be 0-6, Hour must be 0-23.")
        
        await self.config.weekly_role_id.set(role.id)
        await self.config.weekly_role_day.set(day)
        await self.config.weekly_role_hour.set(hour)
        await ctx.send(f"Weekly role {role.name} will be awarded on day {day} at hour {hour} UTC.")

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
        """Admin management for leaderboards."""
        pass

    @gortleadmin.command()
    async def clearall(self, ctx):
        """Clear all scores."""
        await self.config.clear_all_members(ctx.guild)
        await ctx.send("All scores cleared.")

    @gortleadmin.command()
    async def removeuser(self, ctx, member: discord.Member):
        """Remove a specific user from stats."""
        await self.config.member(member).clear()
        await ctx.send(f"Stats cleared for {member.display_name}.")

    @gortleadmin.command()
    async def clean(self, ctx):
        """Remove users who are no longer in the server."""
        members = await self.config.all_members(ctx.guild)
        count = 0
        for uid in list(members.keys()):
            if not ctx.guild.get_member(uid):
                await self.config.member_from_ids(ctx.guild.id, uid).clear()
                count += 1
        await ctx.send(f"Removed {count} users no longer in the server.")