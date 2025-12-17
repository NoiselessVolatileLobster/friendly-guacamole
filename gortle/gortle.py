import discord
import random
import asyncio
import re
import datetime
import json
import os
from typing import Optional, Literal
from collections import Counter
import math

from redbot.core import commands, Config, bank, checks
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.chat_formatting import pagify, box

class Gortle(commands.Cog):
    """A communal 6-letter Wordle-style game for Discord."""
    
    # Configuration for Emoji colors
    EMOJI_UNUSED = "white"   # Letters that have not been guessed
    EMOJI_CORRECT = "green" # Guessed and in right position
    EMOJI_PRESENT = "yellow" # Guessed and in wrong position
    EMOJI_ABSENT = "grey"  # Guessed and NOT in the word (inferred)
    
    MAX_GUESSES = 9
    CREDITS_PER_POINT = 10

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473629103, force_registration=True)

        # Initialize lists
        self.solutions = []
        self.guesses = []
        self._load_word_lists()

        default_global = {
            "schedule_auto_freq": 0, # Times per hour (0 to disable)
            "schedule_manual_max": 0, # Max manual games per clock hour
            "manual_log": {"hour": 0, "count": 0}, # Tracks manual usage: {hour_timestamp: int, count: int}
            "next_game_timestamp": 0,
            "current_game_id": 0,
            "current_word": None,
            "used_words": [],
            "game_number": 0,
            "game_active": False,
            "cooldown_reset_timestamp": 0, # Timestamp when cooldowns were last "cleared" (new game start)
            "consecutive_no_guesses": 0, # Tracks how many games in a row had zero interaction
            "game_state": {
                "solved_indices": [],
                "found_letters": [], # Letters found (Yellow or Green)
                "guessed_letters": [], # Letters guessed at least once
                "guesses_made": 0,
                "history": [], # Stores list of {visual: str, user_id: int}
                "round_scores": {} # Tracks points earned specifically in this round {user_id: points}
            },
            "win_amount": 100, # Legacy, kept for config safety but unused
            "weekly_role_id": None,
            "weekly_role_day": 0,
            "weekly_role_hour": 9,
            "last_weekly_award": 0
        }

        default_guild = {
            "channel_id": None,
            "mention_role": None,
            "cooldown_seconds": 60,
            "thumbnail_url": None 
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

    def _find_emoji(self, name_query: str) -> Optional[discord.Emoji]:
        """Case-insensitive search for an emoji."""
        target = name_query.lower()
        # Search all emojis the bot can see
        for emoji in self.bot.emojis:
            if emoji.name.lower() == target:
                return emoji
        return None

    def _get_emoji_str(self, char: str, color: str) -> str:
        """Helper to format the emoji string."""
        emoji_name = f"{color}{char.lower()}"
        emoji = self._find_emoji(emoji_name)
        if emoji:
            return str(emoji)
        return f":{emoji_name}:"

    def _get_keyboard_visual(self, state, solution) -> str:
        """Generates the QWERTY keyboard visual based on game state."""
        rows = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        visual_rows = []

        guessed_letters = set(state['guessed_letters'])
        letter_status = {}
        
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ".lower():
            if char not in guessed_letters:
                letter_status[char] = self.EMOJI_UNUSED
            else:
                if char not in solution:
                    letter_status[char] = self.EMOJI_ABSENT
                else:
                    is_solved = False
                    for idx in state['solved_indices']:
                        if solution[idx] == char:
                            is_solved = True
                            break
                    
                    if is_solved:
                        letter_status[char] = self.EMOJI_CORRECT
                    else:
                        letter_status[char] = self.EMOJI_PRESENT
        
        # Fetch spacer emoji
        spacer = self._find_emoji("greysquare")
        spacer_str = str(spacer) if spacer else ":greysquare:"

        for i, row in enumerate(rows):
            line = ""
            
            # Left padding for row 3 (before Z)
            if i == 2:
                line += f"{spacer_str}"

            for char in row.lower():
                color = letter_status.get(char, self.EMOJI_UNUSED)
                line += self._get_emoji_str(char, color)
            
            # Right padding for row 2 (after L)
            if i == 1:
                line += f"{spacer_str}"
            
            # Right padding for row 3 (after M) - two instances
            if i == 2:
                line += f"{spacer_str}{spacer_str}"

            visual_rows.append(line)
            
        return "\n".join(visual_rows)

    def _calculate_next_auto_time(self, now, freq):
        """Calculates the next timestamp for an auto-game based on frequency per hour."""
        if freq <= 0:
            return 0
            
        interval_minutes = 60 / freq
        current_minute = now.minute
        
        # Calculate which 'slot' we are in or passed
        # e.g. freq=2 (30 min), current=15. Slots: 0, 30. Next: 30.
        # e.g. freq=2 (30 min), current=45. Slots: 0, 30. Next: 00 (next hour).
        
        next_slot_index = math.ceil((current_minute + 1) / interval_minutes) # +1 to avoid immediate re-trigger if logic runs fast
        next_minute = int(next_slot_index * interval_minutes)
        
        if next_minute >= 60:
            # Move to next hour
            next_time = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        else:
            # Same hour
            next_time = now.replace(minute=next_minute, second=0, microsecond=0)
            
        return int(next_time.timestamp())

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
                next_game_ts = await self.config.next_game_timestamp()
                auto_freq = await self.config.schedule_auto_freq()
                
                # Check Sleep Status:
                # If we have 3 or more games with 0 guesses, we are "asleep".
                # We do NOT schedule new games until a manual game is started.
                sleep_streak = await self.config.consecutive_no_guesses()
                
                if auto_freq > 0 and sleep_streak < 3:
                    if next_game_ts == 0 or timestamp >= next_game_ts:
                        # Swap order: Calculate next time FIRST, then start game.
                        # This allows start_new_game to see the valid FUTURE timestamp for the embed.
                        new_next_ts = self._calculate_next_auto_time(now, auto_freq)
                        await self.config.next_game_timestamp.set(new_next_ts)

                        # Only start if next_game_ts was actually set to a valid past time (not 0 initialization)
                        if next_game_ts != 0:
                            await self.start_new_game(manual=False)

            except Exception as e:
                print(f"Error in Gortle loop: {e}")
            
            await asyncio.sleep(60)

    async def start_new_game(self, manual=False):
        async with self.lock:
            # Update Reset Timestamp for Cooldowns (Everyone starts fresh)
            await self.config.cooldown_reset_timestamp.set(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))

            # Check if previous game needs revealing or counting for sleep
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
                # Handle Expiration of previous game
                embed = discord.Embed(title="Gortle Expired!", description=f"The word was **{old_word.upper()}**.", color=discord.Color.red())
                thumb = await self.config.guild(target_channel.guild).thumbnail_url()
                if thumb:
                    embed.set_thumbnail(url=thumb)
                await target_channel.send(embed=embed)

                # SLEEP LOGIC: Check if the expired game had 0 guesses
                state = await self.config.game_state()
                history = state.get("history", [])
                
                if len(history) == 0:
                    # No guesses made, increment sleep counter
                    current_streak = await self.config.consecutive_no_guesses() + 1
                    await self.config.consecutive_no_guesses.set(current_streak)
                    
                    if current_streak >= 3 and not manual:
                        # Go to sleep
                        prefixes = await self.bot.get_valid_prefixes(target_channel.guild)
                        prefix = prefixes[0] if prefixes else "[p]"
                        
                        sleep_embed = discord.Embed(
                            title="Gortle's Gone To Sleep", 
                            description=f"Three games with no guesses. Zzz...\nType `{prefix}newgortle` to wake me up!",
                            color=discord.Color.dark_grey()
                        )
                        if thumb:
                            sleep_embed.set_thumbnail(url=thumb)
                            
                        await target_channel.send(embed=sleep_embed)
                        
                        # Disable auto-scheduler and mark inactive
                        await self.config.next_game_timestamp.set(0)
                        await self.config.game_active.set(False)
                        return
                else:
                    # Guesses were made, reset counter
                    await self.config.consecutive_no_guesses.set(0)

            # If starting manually, always wake up (reset sleep counter)
            if manual:
                await self.config.consecutive_no_guesses.set(0)

            # Pick new word
            used = await self.config.used_words()
            # Filter: unused AND exactly 6 chars long
            available = [w for w in self.solutions if w not in used and len(w) == 6]
            
            if not available:
                used = []
                await self.config.used_words.set([])
                # Reset and ensure we still filter by length
                available = [w for w in self.solutions if len(w) == 6]

            if not available:
                print("[Gortle] No valid 6-letter words available in solutions.json!")
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
                "guesses_made": 0,
                "history": [],
                "round_scores": {} # Reset round scores
            }
            await self.config.game_state.set(new_state)

            # Announce
            role_id = await self.config.guild(target_channel.guild).mention_role()
            mention = f"<@&{role_id}>" if role_id else ""
            
            keyboard_view = self._get_keyboard_visual(new_state, new_word)

            # Add expiration timestamp if schedule is active
            next_ts = await self.config.next_game_timestamp()
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            
            # Note: If we just woke up from sleep manually, next_ts might be 0 here because
            # the loop hasn't run yet to set the new schedule. That is intended behavior.
            if next_ts > now_ts:
                 keyboard_view += f"\n\n**Next Game:** <t:{next_ts}:R>"
            
            desc = "Guess the 6-letter word by typing `!word`!"
            
            embed = discord.Embed(title=f"New Gortle Started! (#{game_num})", description=desc, color=discord.Color.green())
            # Use zero-width space for title to effectively remove it
            embed.add_field(name="\u200b", value=keyboard_view, inline=False)
            
            thumb = await self.config.guild(target_channel.guild).thumbnail_url()
            if thumb:
                embed.set_thumbnail(url=thumb)
                
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
        if not message.guild:
            return

        conf_channel = await self.config.guild(message.guild).channel_id()
        if not conf_channel or message.channel.id != conf_channel:
            return

        content = message.content.lower().strip()
        if not content.startswith("!"):
            return

        guess = content[1:]
        if not re.fullmatch(r"[a-z]{6}", guess):
            return 
        
        if not await self.config.game_active():
            return
            
        # 1. Cooldown Check FIRST
        cooldown = await self.config.guild(message.guild).cooldown_seconds()
        last_guess = await self.config.member(message.author).last_guess_time()
        reset_ts = await self.config.cooldown_reset_timestamp()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        # If last guess was before the last reset, ignore it (treat as no cooldown)
        if last_guess < reset_ts:
            last_guess = 0
        
        if (now - last_guess) < cooldown:
            try:
                await message.delete()
                next_time = int(last_guess + cooldown)
                await message.channel.send(f"{message.author.mention}, you need to wait. Next guess: <t:{next_time}:R>", delete_after=5)
            except discord.Forbidden:
                pass
            return

        # 2. Dictionary Check SECOND
        if guess not in self.guesses:
            await message.channel.send("I do not think that word is in my dictionary.", delete_after=5)
            return

        await self.config.member(message.author).last_guess_time.set(int(now))
        async with self.lock:
            await self.process_guess(message, guess)

    async def process_guess(self, message, guess):
        solution = await self.config.current_word()
        state = await self.config.game_state()
        solved_indices = set(state['solved_indices'])
        
        # FIX START: Pre-calculate counts of letters that are ALREADY locked (Green)
        # This prevents "Green" letters from previous rounds from counting as "Floating" knowledge.
        locked_chars = Counter()
        for idx in solved_indices:
            locked_chars[solution[idx]] += 1
        # FIX END

        # Track how many of each letter we have matched IN THIS GUESS
        matched_in_guess = Counter()
        
        guess_visual = [""] * 6
        points = 0
        
        sol_chars = list(solution)
        guess_chars = list(guess)
        sol_remaining = list(solution) 
        
        # 1. Pass for GREENS
        for i, char in enumerate(guess_chars):
            if char == sol_chars[i]:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_CORRECT)
                sol_remaining[i] = None 
                
                # Mark this instance as matched in this guess
                matched_in_guess[char] += 1
                
                if i not in solved_indices:
                    # Determine if this is a "new" instance or an "upgrade"
                    k = matched_in_guess[char]
                    
                    # FIX START: Calculate how many "Floating" (Yellow) instances of this char we knew about
                    total_known = state['found_letters'].count(char)
                    locked_count = locked_chars[char]
                    floating_known = max(0, total_known - locked_count)
                    
                    if floating_known >= k:
                        # We knew about 'k' floating instances. This Green consumes one.
                        # Upgrading from Yellow -> Green = 1 point
                        points += 1
                    else:
                        # We didn't know about this many instances. New discovery.
                        # Finding new Green = 2 points
                        points += 2
                        state['found_letters'].append(char)
                    # FIX END
                    
                    state['solved_indices'].append(i)
                else:
                    points += 0 

        # 2. Pass for YELLOWS / ABSENT
        for i, char in enumerate(guess_chars):
            if guess_visual[i] != "": continue

            if char in sol_remaining:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_PRESENT)
                sol_remaining[sol_remaining.index(char)] = None 
                
                # Mark match
                matched_in_guess[char] += 1
                k = matched_in_guess[char]
                current_known = state['found_letters'].count(char)
                
                # Point Logic (Yellow logic remains mostly same, as it deals with total quantity)
                if current_known >= k:
                    # We already knew about K instances of this letter.
                    # Since this is just Yellow (re-finding), NO POINTS.
                    points += 0
                else:
                    # New instance found.
                    # Finding new Yellow = 1 point
                    points += 1
                    state['found_letters'].append(char)
            else:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_ABSENT)

        # Update History
        history_entry = {
            "visual": ' '.join(guess_visual),
            "user_id": message.author.id,
            "word": guess
        }
        # ... rest of the function remains identical ...
        if 'history' not in state:
            state['history'] = []
        state['history'].append(history_entry)

        # Update guessed letters
        current_guessed = set(state['guessed_letters'])
        for c in guess:
            current_guessed.add(c)
        state['guessed_letters'] = sorted(list(current_guessed))
        
        # Update Round Scores
        round_scores = state.get('round_scores', {})
        str_uid = str(message.author.id)
        round_scores[str_uid] = round_scores.get(str_uid, 0) + points
        state['round_scores'] = round_scores

        await self.config.game_state.set(state)

        # Update Score
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
        keyboard_view = self._get_keyboard_visual(state, solution)

        # Add expiration timestamp if schedule is active
        next_ts = await self.config.next_game_timestamp()
        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        if next_ts > now_ts:
             keyboard_view += f"\n\n**Next Game:** <t:{next_ts}:R>"
        
        # Build History Display
        full_history = state['history']
        display_history = full_history if len(full_history) < 20 else full_history[-20:]
        
        history_lines = []
        for idx, entry in enumerate(display_history, 1):
             actual_idx = idx if len(full_history) < 20 else (len(full_history) - 20 + idx)
             user = message.guild.get_member(entry['user_id'])
             user_name = user.display_name if user else "Unknown"
             history_lines.append(f"`{actual_idx}.` {entry['visual']} (**{user_name}**)")

        description = "\n".join(history_lines)
        if len(full_history) >= 20:
             description = f"*(Previous guesses hidden)*\n{description}"

        embed = discord.Embed(title=f"Gortle #{game_num}", description=description, color=discord.Color.blue())
        
        total_round_points = round_scores.get(str_uid, 0)
        
        embed.add_field(name="Points Gained", value=f"+{points} ({total_round_points} points this round)", inline=True)
        embed.add_field(name="\u200b", value=keyboard_view, inline=False)
        
        thumb = await self.config.guild(message.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)

        await message.channel.send(embed=embed)

        if guess == solution:
            await self.handle_win(message.author, message.channel, game_num)
        elif len(state['history']) >= self.MAX_GUESSES:
            await self.handle_loss(message.channel, solution)

    async def handle_win(self, winner, channel, game_num):
        # Game finished, so reset streak of no-guesses
        await self.config.consecutive_no_guesses.set(0)
        await self.config.game_active.set(False)
        
        state = await self.config.game_state()
        round_scores = state.get('round_scores', {})
        participants = set()
        
        # Identify participants from history
        for entry in state.get('history', []):
            participants.add(entry['user_id'])
            
        # Collect results for display and sort
        results = []
        
        for uid in participants:
            member = channel.guild.get_member(uid)
            if not member:
                continue
                
            # 1. Award Participation Points (+2)
            await self.config.member(member).score.set(
                await self.config.member(member).score() + 2
            )
            await self.config.member(member).weekly_score.set(
                await self.config.member(member).weekly_score() + 2
            )
            
            # 2. Calculate Payout
            # Get points earned during guesses
            guess_points = round_scores.get(str(uid), 0)
            
            # Total Round Points = Guess Points + 2 (Participation)
            total_round_points = guess_points + 2
            
            results.append((total_round_points, member))
            
            # Credits = Total Points * 10
            credits_to_give = total_round_points * self.CREDITS_PER_POINT
            
            if credits_to_give > 0:
                try:
                    await bank.deposit_credits(member, credits_to_give)
                except Exception as e:
                    print(f"Failed to deposit credits for {member}: {e}")

        # Sort results ascending by points (lowest first)
        results.sort(key=lambda x: x[0])
        
        # Generate display string
        score_lines = []
        for points, member in results:
            score_lines.append(f"{member.display_name}: **{points}**")
            
        score_str = "\n".join(score_lines)

        # Currency name for display
        try:
            currency = await bank.get_currency_name(channel.guild)
        except:
            currency = "credits"

        # Fetch custom title emojis (Case-insensitive)
        yay2 = self._find_emoji("yay2")
        yay = self._find_emoji("yay")
        
        # Use str() for emoji objects to get proper ID format, else fallback to text
        yay2_str = str(yay2) if yay2 else ":yay2:"
        yay_str = str(yay) if yay else ":yay:"

        embed = discord.Embed(title=f"{yay2_str} Gortle #{game_num} Solved! {yay_str}", 
                              description=f"**{winner.mention}** guessed the word correctly!", 
                              color=discord.Color.gold())
        embed.add_field(name="Solution", value=await self.config.current_word(), inline=False)
        
        if score_str:
            embed.add_field(name="Round Scores", value=score_str, inline=False)
            
        embed.set_footer(text=f"Participants received +2 points and {self.CREDITS_PER_POINT} {currency} per point earned!")
        
        thumb = await self.config.guild(channel.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)

        await channel.send(embed=embed)

    async def handle_loss(self, channel, solution):
        # Game finished (lost), so reset streak of no-guesses
        await self.config.consecutive_no_guesses.set(0)
        await self.config.game_active.set(False)
        embed = discord.Embed(title="Gortle Failed!", 
                              description=f"Max guesses reached! The word was **{solution.upper()}**.", 
                              color=discord.Color.red())
        
        thumb = await self.config.guild(channel.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)
            
        await channel.send(embed=embed)

    # --- Commands ---

    @commands.command()
    async def newgortle(self, ctx):
        """Manually start a new Gortle game.
        This is subject to a rate limit set by the server admins.
        """
        # Check if game is already active
        if await self.config.game_active():
            return await ctx.send("A Gortle game is already active!")

        # Check Manual Limit
        limit = await self.config.schedule_manual_max()
        if limit > 0:
            now = datetime.datetime.now(datetime.timezone.utc)
            current_hour_ts = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
            
            log = await self.config.manual_log()
            
            # Check if stored log is for a previous hour
            if log['hour'] != current_hour_ts:
                # Reset
                log = {"hour": current_hour_ts, "count": 0}
            
            if log['count'] >= limit:
                return await ctx.send(f"The maximum number of manual games for this hour ({limit}) has been reached. Please wait for the next hour or an auto-scheduled game.")
            
            # Increment and Save
            log['count'] += 1
            await self.config.manual_log.set(log)

        await self.start_new_game(manual=True)
        await ctx.send("Game started.")

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
    async def thumbnail(self, ctx, url: str = None):
        """Set the thumbnail URL for the game embeds. Leave empty to clear."""
        if not url:
            await self.config.guild(ctx.guild).thumbnail_url.set(None)
            await ctx.send("Thumbnail cleared.")
        else:
            if not url.startswith("http"):
                 return await ctx.send("That doesn't look like a valid URL.")
            await self.config.guild(ctx.guild).thumbnail_url.set(url)
            await ctx.send(f"Thumbnail set to: <{url}>")

    @gortleset.command()
    async def schedule(self, ctx, auto_freq: int, manual_max: int):
        """Set the game schedule logic.
        
        auto_freq: How many times per hour to auto-post a game (e.g., 2 = every 30 mins). Set 0 to disable.
        manual_max: How many manual games users can start per hour. Set 0 for unlimited.
        """
        if auto_freq < 0:
            return await ctx.send("Auto frequency cannot be negative.")
        if manual_max < 0:
            return await ctx.send("Manual max cannot be negative.")
            
        await self.config.schedule_auto_freq.set(auto_freq)
        await self.config.schedule_manual_max.set(manual_max)
        
        # Reset next game timestamp so logic recalculates immediately
        await self.config.next_game_timestamp.set(0)
        
        msg = f"Schedule updated:\n- Auto-post: {auto_freq} times/hour\n- Manual limit: {manual_max} games/hour"
        await ctx.send(msg)

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
    async def hardreset(self, ctx):
        """Resets the game number, used words, and all user scores."""
        await ctx.send("Are you sure you want to reset EVERYTHING? This includes the game count, history of used words, and all user leaderboards. Type `yes` to confirm.")
        try:
            pred = lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Reset cancelled.")

        # Reset Global Data
        await self.config.game_number.set(0)
        await self.config.used_words.set([])
        await self.config.game_active.set(False)
        await self.config.current_word.set(None)
        
        # Reset Member Data (Leaderboard)
        await self.config.clear_all_members(ctx.guild)
        
        await ctx.send("Gortle has been completely reset.")

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