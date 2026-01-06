import discord
import asyncio
import random
import time
from datetime import datetime, timedelta
from redbot.core import commands, Config, bank
from redbot.core.utils.chat_formatting import box, humanize_list
from discord.ext import tasks
from discord.ui import View, Button

class LeaderboardView(View):
    def __init__(self, ctx, all_data):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.all_data = all_data
        self.current_sort = "Damage Dealt"
        
        # Map readable names to config keys
        self.sort_map = {
            "Damage Dealt": "stat_damage_dealt",
            "Damage Taken": "stat_hits_taken",
            "Snowballs Made": "stat_snowballs_made",
            "Cookies Eaten": "stat_cookies_eaten",
            "Drinks Drunk": "stat_drinks_drunk",
            "Money Spent": "stat_credits_spent"
        }
        
        # Initialize buttons
        self._add_buttons()

    def _add_buttons(self):
        # Create a button for each sort option
        for label in self.sort_map.keys():
            style = discord.ButtonStyle.primary if label == self.current_sort else discord.ButtonStyle.secondary
            # Ensure custom_id is set to the label
            btn = Button(label=label, style=style, custom_id=label)
            btn.callback = self.button_callback
            self.add_item(btn)

    async def button_callback(self, interaction: discord.Interaction):
        self.current_sort = interaction.data["custom_id"]
        
        # Update button styles to show active selection
        for child in self.children:
            if isinstance(child, Button):
                child.style = discord.ButtonStyle.primary if child.custom_id == self.current_sort else discord.ButtonStyle.secondary
        
        embed = self.generate_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def generate_embed(self):
        sort_key = self.sort_map[self.current_sort]
        return self._build_embed(self.current_sort, sort_key)

    def _build_embed(self, title_suffix, sort_key):
        # Shared logic for building the embed so we can use it for end-of-season too
        sorted_members = sorted(
            self.all_data.items(), 
            key=lambda x: x[1].get(sort_key, 0), 
            reverse=True
        )[:10]

        embed = discord.Embed(title=f"🏆 Snowball Championships: {title_suffix}", color=discord.Color.gold())
        
        desc = ""
        for index, (user_id, data) in enumerate(sorted_members, 1):
            user = self.ctx.guild.get_member(user_id)
            name = user.display_name if user else "Unknown User"
            val = data.get(sort_key, 0)
            
            val_str = f"{val}"

            desc += f"**{index}. {name}**: {val_str}\n"
            
        if not desc:
            desc = "No stats recorded yet!"
            
        embed.description = desc
        embed.set_footer(text="Top 10 Players")
        return embed

class Snowball(commands.Cog):
    """
    A Snowball fighting system with items, health, hilarity, and weather.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        # Default Settings
        default_guild = {
            "items": {
                # --- DRINKS (Bonus Dmg, Duration) ---
                "Tinsel Tea": {
                    "type": "drink", "rarity": 1, "bonus": 1, "price": 1000, "duration": 60, "durability": 1
                },
                "Jingle Java": {
                    "type": "drink", "rarity": 2, "bonus": 2, "price": 2500, "duration": 120, "durability": 1
                },
                "Lit up Latte": {
                    "type": "drink", "rarity": 3, "bonus": 3, "price": 4000, "duration": 180, "durability": 1
                },
                "Peppermint Pour Over": {
                    "type": "drink", "rarity": 4, "bonus": 4, "price": 6000, "duration": 240, "durability": 1
                },
                "Merry Mocha": {
                    "type": "drink", "rarity": 5, "bonus": 5, "price": 8000, "duration": 300, "durability": 1
                },
                "Candy Cane Cappucino": {
                    "type": "drink", "rarity": 6, "bonus": 6, "price": 10000, "duration": 360, "durability": 1
                },
                "Ho Ho Hot Chocolate": {
                    "type": "drink", "rarity": 7, "bonus": 7, "price": 12500, "duration": 480, "durability": 1
                },
                "Jolly Joe": {
                    "type": "drink", "rarity": 8, "bonus": 8, "price": 15000, "duration": 600, "durability": 1
                },
                "Glowing Glühwein": {
                    "type": "drink", "rarity": 9, "bonus": 9, "price": 17500, "duration": 750, "durability": 1
                },
                "Excellent Eggnog": {
                    "type": "drink", "rarity": 10, "bonus": 10, "price": 20000, "duration": 900, "durability": 1
                },

                # --- COOKIES (Heal HP) ---
                "Sugar Cookie": {
                    "type": "cookie", "rarity": 1, "bonus": 1, "price": 1000, "durability": 1, "duration": 0
                },
                "Shortbread": {
                    "type": "cookie", "rarity": 2, "bonus": 2, "price": 2000, "durability": 1, "duration": 0
                },
                "Gingerbread": {
                    "type": "cookie", "rarity": 3, "bonus": 3, "price": 3500, "durability": 1, "duration": 0
                },
                "Chocolate Chip": {
                    "type": "cookie", "rarity": 4, "bonus": 4, "price": 5000, "durability": 1, "duration": 0
                },
                "Snickerdoodle": {
                    "type": "cookie", "rarity": 5, "bonus": 5, "price": 7000, "durability": 1, "duration": 0
                },
                "Molasses Cookie": {
                    "type": "cookie", "rarity": 6, "bonus": 6, "price": 9000, "durability": 1, "duration": 0
                },
                "Thumbprint Cookies": {
                    "type": "cookie", "rarity": 7, "bonus": 7, "price": 11000, "durability": 1, "duration": 0
                },
                "Pecan Shortbread": {
                    "type": "cookie", "rarity": 8, "bonus": 8, "price": 14000, "durability": 1, "duration": 0
                },
                "Cranberry Orange Cookies": {
                    "type": "cookie", "rarity": 9, "bonus": 9, "price": 17000, "durability": 1, "duration": 0
                },
                "Peanut Butter Cookie": {
                    "type": "cookie", "rarity": 10, "bonus": 10, "price": 20000, "durability": 1, "duration": 0
                },

                # --- BOOSTERS (Extra Balls + Speed) ---
                "Ice Cream Scoop": {
                    "type": "booster", "rarity": 7, "bonus": 1, "price": 5000, "durability": 2, "duration": 0
                },
                "Duck Mold": {
                    "type": "booster", "rarity": 8, "bonus": 2, "price": 10000, "durability": 3, "duration": 0
                },
                "Garbage Mitts": {
                    "type": "booster", "rarity": 9, "bonus": 3, "price": 15000, "durability": 4, "duration": 0
                },
                "Snow Shovel": {
                    "type": "booster", "rarity": 10, "bonus": 4, "price": 20000, "durability": 5, "duration": 0
                }
            },
            "shop_inventory": [], 
            "shop_last_refresh": 0,
            "snowball_roll_time": 60,
            "channel_id": None,
            "snowfall_probability": 50,
            # Season config (Stores MM-DD as strings)
            "season_start_str": "0",
            "season_end_str": "0",
            # Tracks the year we last processed rewards for to prevent duplicates
            "last_season_year": 0 
        }

        default_member = {
            "hp": 100,
            "snowballs": 0,
            "inventory": {}, # {item_name: quantity}
            "active_booster": {}, # {name, current_durability, max_durability}
            "active_drink": {},   # {name, bonus, expires_at}
            "frostbite_end": 0, # Timestamp
            "gathering_end": 0, # Timestamp to prevent spam
            
            # Stats
            "stat_damage_dealt": 0,
            "stat_cookies_eaten": 0,
            "stat_drinks_drunk": 0,
            "stat_snowballs_made": 0,
            "stat_hits_taken": 0,
            "stat_hp_lost": 0,
            "stat_hp_gained": 0,
            "stat_credits_spent": 0,
            "stat_frostbites_inflicted": 0,
            "stat_frostbites_taken": 0
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        # Start the snowfall loop
        self.snowfall_loop.start()

    def cog_unload(self):
        self.snowfall_loop.cancel()

    # --- Tasks ---

    @tasks.loop(minutes=15)
    async def snowfall_loop(self):
        """Calculates snowfall probability every 15 minutes and checks for season end."""
        for guild in self.bot.guilds:
            # 1. Check Season Status
            conf = self.config.guild(guild)
            start_str = await conf.season_start_str()
            end_str = await conf.season_end_str()
            last_year = await conf.last_season_year()
            
            # Skip if not configured
            if start_str == "0" or end_str == "0":
                pass
            else:
                current_start, current_end = self.get_season_dates(start_str, end_str)
                now = datetime.now()
                
                # Check if we just passed the end date
                # We identify the season by its start year.
                # If now > end, and we haven't processed this specific end year yet.
                if now > current_end:
                    # Determine the "season year" identifier. 
                    # If the season is Dec 2025 - Jan 2026, we can call it season 2025.
                    season_id = current_end.year
                    
                    if season_id > last_year:
                        await self.run_end_of_season(guild)
                        await conf.last_season_year.set(season_id)
                        continue # Skip snowfall if ended

            # 2. Snowfall Logic
            # Generate probability 0-100
            probability = random.randint(0, 100)
            await conf.snowfall_probability.set(probability)
            
            # Check for heavy snow
            if probability > 85:
                channel_id = await conf.channel_id()
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    # Ensure bot can speak there and channel exists
                    if channel and channel.permissions_for(guild.me).send_messages:
                        await channel.send("🌨️**It's snowing!**❄️")

    @snowfall_loop.before_loop
    async def before_snowfall_loop(self):
        await self.bot.wait_until_red_ready()

    async def run_end_of_season(self, guild):
        """Posts the end of season message and leaderboards."""
        channel_id = await self.config.guild(guild).channel_id()
        if not channel_id:
            return
            
        channel = guild.get_channel(channel_id)
        if not channel or not channel.permissions_for(guild.me).send_messages:
            return

        # 1. Announce End
        await channel.send("🎉 **The Snowball Season has officially ended!** 🎉\nThanks for playing! Come back next year!")
        await asyncio.sleep(2)

        # 2. Fetch Data for Leaderboards
        all_members = await self.config.all_members(guild)
        if not all_members:
            return

        # 3. Stats Map
        sort_map = {
            "Damage Dealt": "stat_damage_dealt",
            "Damage Taken": "stat_hits_taken",
            "Snowballs Made": "stat_snowballs_made",
            "Cookies Eaten": "stat_cookies_eaten",
            "Drinks Drunk": "stat_drinks_drunk",
            "Money Spent": "stat_credits_spent"
        }

        # 4. Loop through categories and post
        for pretty_name, stat_key in sort_map.items():
            sorted_data = sorted(
                all_members.items(), 
                key=lambda x: x[1].get(stat_key, 0), 
                reverse=True
            )[:10]

            embed = discord.Embed(title=f"🏆 Final Leaderboard: {pretty_name}", color=discord.Color.gold())
            desc = ""
            for index, (user_id, data) in enumerate(sorted_data, 1):
                user = guild.get_member(user_id)
                name = user.display_name if user else "Unknown User"
                val = data.get(stat_key, 0)
                desc += f"**{index}. {name}**: {val}\n"
            
            if not desc:
                desc = "No stats recorded."
            
            embed.description = desc
            await channel.send(embed=embed)
            
            # Delay to avoid throttling
            await asyncio.sleep(5)


    # --- Helper Functions ---

    def get_season_dates(self, start_md: str, end_md: str):
        """
        Calculates the start and end datetime objects for the current or upcoming season.
        Handles years wrapping around (e.g. Dec to Jan).
        Returns (start_dt, end_dt).
        """
        now = datetime.now()
        current_year = now.year
        
        # Parse Month/Day
        try:
            s_m, s_d = map(int, start_md.split("-"))
            e_m, e_d = map(int, end_md.split("-"))
        except ValueError:
            return now, now # Fail safe

        # Construct potential dates for this year
        start_this_year = datetime(current_year, s_m, s_d)
        end_this_year = datetime(current_year, e_m, e_d)
        
        # Determine if season wraps around year end (e.g. Dec 1 to Jan 7)
        wraps = start_this_year > end_this_year

        if wraps:
            # If wrapping, we have two possible windows relevant to 'now':
            # 1. The one starting late last year (e.g. Dec 2024 - Jan 2025)
            # 2. The one starting late this year (e.g. Dec 2025 - Jan 2026)
            
            # Check window 1 (Last Year Start -> This Year End)
            start_prev = datetime(current_year - 1, s_m, s_d)
            end_curr = end_this_year # Jan 2025
            
            # Check window 2 (This Year Start -> Next Year End)
            start_curr = start_this_year # Dec 2025
            end_next = datetime(current_year + 1, e_m, e_d) # Jan 2026
            
            # Logic:
            # If we are in Jan (before end_curr), the season is active (started prev year).
            # If we are in Dec (after start_curr), the season is active (ends next year).
            # If we are in between (Feb - Nov), we look at start_curr (Next season).
            
            if now <= end_curr:
                return start_prev, end_curr
            elif now >= start_curr:
                return start_curr, end_next
            else:
                # We are in the off-season. Return the UPCOMING season.
                return start_curr, end_next
                
        else:
            # Simple case: Season within same year (e.g. June to August)
            if now > end_this_year:
                # Season finished this year. Return next year's dates.
                start_next = datetime(current_year + 1, s_m, s_d)
                end_next = datetime(current_year + 1, e_m, e_d)
                return start_next, end_next
            else:
                # Season hasn't happened or is happening.
                return start_this_year, end_this_year

    async def check_channel(self, ctx):
        """Ensures the command is used in the allowed channel."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        
        if not channel_id:
            return True
        
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return True

        if ctx.channel.id != channel_id:
            await ctx.send(f"🚫 Run Snowball commands in {channel.mention}!", delete_after=5)
            return False
            
        return True

    async def check_season(self, ctx):
        """Checks if the current time is within the season dates."""
        if await self.bot.is_owner(ctx.author) or ctx.author.guild_permissions.administrator:
            # Optional bypass for testing
            pass

        data = await self.config.guild(ctx.guild).all()
        start_str = data.get('season_start_str', "0")
        end_str = data.get('season_end_str', "0")
        
        # If dates are 0, assume season is always open
        if start_str == "0" and end_str == "0":
            return True

        now = datetime.now()
        start_dt, end_dt = self.get_season_dates(start_str, end_str)
        
        # If now is before start, it's upcoming
        if now < start_dt:
            # Use timestamp for localized time
            ts = int(start_dt.timestamp())
            await ctx.send(f"🛑 The Snowball season hasn't started yet! It begins <t:{ts}:F>.")
            return False
        
        # If now is after end, it's over
        if now > end_dt:
            await ctx.send(f"🛑 The Snowball season has ended! See you next year.")
            return False

        return True

    async def check_status(self, ctx):
        """Checks if the user is frozen."""
        member_conf = self.config.member(ctx.author)
        data = await member_conf.all()
        now = int(time.time())

        if data["frostbite_end"] > now:
            relative = f"<t:{data['frostbite_end']}:R>"
            await ctx.send(f"🥶 You've got Frostbite. Chill for {relative}.")
            return False
        
        if data["frostbite_end"] != 0 and data["frostbite_end"] <= now and data["hp"] <= 0:
            await member_conf.hp.set(100)
            await member_conf.frostbite_end.set(0)
            await ctx.send(f"🔥 **{ctx.author.display_name}** has thawed out and is ready to fight again!")

        return True

    async def get_equipped_booster_bonus(self, user):
        """Returns the bonus stats of the currently equipped item."""
        data = await self.config.member(user).all()
        active = data.get("active_booster", {})
        
        if not active:
            return 0, 0, None # bonus, time_red, name
        
        name = active['name']
        items = await self.config.guild(user.guild).items()
        
        if name in items:
            item_data = items[name]
            bonus = item_data['bonus']
            time_red = item_data['bonus'] * 15
            return bonus, time_red, name
            
        return 0, 0, None

    # --- Commands: Inventory & Equipping ---

    @commands.command()
    async def snowequip(self, ctx, *, item_name: str):
        """
        Equip a booster item from your inventory.
        You must unequip your current item first.
        """
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return

        inventory = await self.config.member(ctx.author).inventory()
        # Find exact casing
        found_name = None
        for k in inventory.keys():
            if k.lower() == item_name.lower():
                found_name = k
                break
        
        if not found_name:
            return await ctx.send("You don't have that item in your inventory.")

        guild_items = await self.config.guild(ctx.guild).items()
        if found_name not in guild_items or guild_items[found_name]['type'] != 'booster':
            return await ctx.send("You can only equip **Booster** items. Use `[p]eat` for cookies or `[p]drink` for drinks.")

        async with self.config.member(ctx.author).all() as data:
            if data['active_booster']:
                current = data['active_booster']['name']
                return await ctx.send(f"You already have **{current}** equipped! Run `[p]snowunequip` first.")

            data['inventory'][found_name] -= 1
            if data['inventory'][found_name] <= 0:
                del data['inventory'][found_name]
            
            max_dura = guild_items[found_name].get('durability', 1)
            
            data['active_booster'] = {
                "name": found_name,
                "current_durability": max_dura,
                "max_durability": max_dura
            }
        
        await ctx.send(f"✅ You equipped **{found_name}**!")

    @commands.command()
    async def snowunequip(self, ctx):
        """Unequip your current booster and return it to inventory."""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return

        async with self.config.member(ctx.author).all() as data:
            active = data.get('active_booster')
            if not active:
                return await ctx.send("You aren't holding anything.")
            
            name = active['name']
            
            if name in data['inventory']:
                data['inventory'][name] += 1
            else:
                data['inventory'][name] = 1
            
            data['active_booster'] = {}
        
        await ctx.send(f"You put away your **{name}**.")

    # --- Commands: Snowball Making ---

    @commands.command()
    async def makesnowballs(self, ctx):
        """Start making snowballs."""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return
        if not await self.check_status(ctx): return

        member_conf = self.config.member(ctx.author)
        gathering_end = await member_conf.gathering_end()
        if gathering_end > time.time():
            return await ctx.send(f"❄️ You are already busy gathering snow! Done <t:{int(gathering_end)}:R>.")

        item_bonus, time_reduction, booster_name = await self.get_equipped_booster_bonus(ctx.author)
        
        snow_prob = await self.config.guild(ctx.guild).snowfall_probability()
        weather_mod = int((snow_prob - 50) / 10)

        base_time = await self.config.guild(ctx.guild).snowball_roll_time()
        actual_time = max(5, base_time - time_reduction)
        
        # Lock the user
        await member_conf.gathering_end.set(int(time.time() + actual_time))
        
        await ctx.send(f"❄️ gathering snow... (Probability: {snow_prob}% | Time: {actual_time}s)")
        
        await asyncio.sleep(actual_time)
            
        if not await self.check_status(ctx):
            return

        base_roll = random.randint(1, 6)
        
        total_balls = base_roll + item_bonus + weather_mod
        if total_balls < 1:
            total_balls = 1 
        
        async with self.config.member(ctx.author).all() as data:
            data['snowballs'] += total_balls
            data['stat_snowballs_made'] += total_balls
            
            broke_msg = ""
            if booster_name and data['active_booster']:
                data['active_booster']['current_durability'] -= 1
                curr = data['active_booster']['current_durability']
                
                if curr <= 0:
                    data['active_booster'] = {} 
                    broke_msg = f"\n⚠️ **Your {booster_name} broke!**"

        calc_str = f"Base: {base_roll} + Items: {item_bonus} + Weather: {weather_mod}"
        
        booster_msg = ""
        if booster_name:
            booster_msg = f"\nUsed equipped **{booster_name}**."
        
        await ctx.send(f"{ctx.author.mention} ☃️ You made **{total_balls}** snowballs! ({calc_str}){booster_msg}{broke_msg}")

    # --- Commands: Consumables (Eat/Drink) ---

    @commands.command()
    async def eat(self, ctx, *, item_name: str):
        """Eat a cookie to regain HP."""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return
        if not await self.check_status(ctx): return

        inventory = await self.config.member(ctx.author).inventory()
        # Find exact casing
        found_name = None
        for k in inventory.keys():
            if k.lower() == item_name.lower():
                found_name = k
                break

        if not found_name:
             return await ctx.send(f"You don't have any **{item_name}**.")

        guild_items = await self.config.guild(ctx.guild).items()
        
        if found_name not in guild_items or guild_items[found_name]['type'] != 'cookie':
             return await ctx.send(f"**{found_name}** is not a cookie! You cannot eat this to heal.")

        item_data = guild_items[found_name]
        heal_amount = random.randint(5, 15) + item_data['bonus']
        
        async with self.config.member(ctx.author).all() as data:
            if data['hp'] >= 100:
                return await ctx.send("😋 You are already fully healthy! Save the cookie for later.")

            data['inventory'][found_name] -= 1
            if data['inventory'][found_name] <= 0:
                del data['inventory'][found_name]
                
            old_hp = data['hp']
            data['hp'] = min(100, old_hp + heal_amount)
            data['stat_cookies_eaten'] += 1
            data['stat_hp_gained'] += heal_amount
            actual_heal = data['hp'] - old_hp

        await ctx.send(f"🍪 You ate **{found_name}** and recovered **{actual_heal} HP**. (Current: {data['hp']}/100)")

    @commands.command()
    async def drink(self, ctx, *, item_name: str):
        """Drink a beverage to gain a temporary damage boost."""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return
        if not await self.check_status(ctx): return

        inventory = await self.config.member(ctx.author).inventory()
        found_name = None
        for k in inventory.keys():
            if k.lower() == item_name.lower():
                found_name = k
                break

        if not found_name:
             return await ctx.send(f"You don't have any **{item_name}**.")

        guild_items = await self.config.guild(ctx.guild).items()
        
        # Check type
        if found_name not in guild_items or guild_items[found_name]['type'] != 'drink':
             return await ctx.send(f"**{found_name}** is not a drink!")

        item_data = guild_items[found_name]
        duration = item_data.get('duration', 60) # Default 60s
        bonus = item_data['bonus']
        
        async with self.config.member(ctx.author).all() as data:
            data['inventory'][found_name] -= 1
            if data['inventory'][found_name] <= 0:
                del data['inventory'][found_name]
            
            # Apply Buff
            expires = int(time.time()) + duration
            data['active_drink'] = {
                "name": found_name,
                "bonus": bonus,
                "expires_at": expires
            }
            data['stat_drinks_drunk'] += 1

        await ctx.send(f"☕ You drank **{found_name}**! You feel powered up (+{bonus} Dmg) for {duration} seconds.")

    # --- Commands: Fighting ---

    @commands.command(aliases=["throw"])
    async def throwball(self, ctx, target: discord.Member):
        """Throw a snowball at someone!"""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return
        if not await self.check_status(ctx): return
        
        if target.bot:
            return await ctx.send("🤖 Robots don't feel the cold. Save your ammo!")
        
        if target.id == ctx.author.id:
            return await ctx.send("Don't hit yourself.")

        author_data = await self.config.member(ctx.author).all()
        if author_data['snowballs'] < 1:
            return await ctx.send("You have no snowballs! Run `[p]makesnowballs` first.")

        target_data = await self.config.member(target).all()
        if target_data['frostbite_end'] > int(time.time()):
            return await ctx.send(f"{target.display_name} is already frozen solid! Leave them alone.")

        # Calculate Damage
        damage = random.randint(1, 6)
        
        # Check Drink Bonus
        drink_bonus = 0
        drink_name = None
        active_drink = author_data.get('active_drink')
        
        if active_drink and active_drink['expires_at'] > int(time.time()):
            drink_bonus = active_drink['bonus']
            drink_name = active_drink['name']
        
        total_damage = damage + drink_bonus

        async with self.config.member(ctx.author).all() as a_data:
            a_data['snowballs'] -= 1
            a_data['stat_damage_dealt'] += total_damage
            
            # Clear expired drink data if needed (lazy cleanup)
            if active_drink and active_drink['expires_at'] <= int(time.time()):
                 a_data['active_drink'] = {}

        async with self.config.member(target).all() as t_data:
            t_data['hp'] -= total_damage
            t_data['stat_hits_taken'] += 1
            t_data['stat_hp_lost'] += total_damage
            current_hp = t_data['hp']

        msg = f"☄️ **{ctx.author.display_name}** hit **{target.display_name}** for **{total_damage}** damage! (HP: {current_hp}/100)"
        
        if drink_bonus > 0:
            msg += f"\n(Buffed by {drink_name})"

        if current_hp <= 0:
            minutes = 15 + abs(current_hp)
            finish_time = int(time.time()) + (minutes * 60)
            
            # --- FROSTBITE LOGIC ---
            # Update Target Stats (Taken)
            async with self.config.member(target).all() as t_stats:
                t_stats['frostbite_end'] = finish_time
                current_taken = t_stats.get('stat_frostbites_taken', 0)
                t_stats['stat_frostbites_taken'] = current_taken + 1
            
            # Update Attacker Stats (Inflicted)
            async with self.config.member(ctx.author).all() as a_stats:
                current_inflicted = a_stats.get('stat_frostbites_inflicted', 0)
                a_stats['stat_frostbites_inflicted'] = current_inflicted + 1
            
            msg += f"\n🥶 **{target.display_name}** has succumbed to **Frostbite**! They are out for {minutes} minutes."

        await ctx.send(msg)

    # --- Commands: Shop & Items ---

    async def _refresh_shop(self, guild):
        """Refreshes the shop based on weighted rarity."""
        items = await self.config.guild(guild).items()
        if not items:
            return []
        
        pool = []
        for name, data in items.items():
            # Inverted Rarity: 10 = Rare (1 ticket), 1 = Common (10 tickets)
            weight = max(1, 11 - data['rarity'])
            for _ in range(weight):
                pool.append(name)
        
        if not pool:
            return []

        unique_items = list(set(pool))
        selection = []
        
        for _ in range(5):
            pick = random.choice(pool)
            selection.append(pick)
        
        await self.config.guild(guild).shop_inventory.set(selection)
        await self.config.guild(guild).shop_last_refresh.set(int(time.time()))
        return selection

    @commands.command()
    async def snowshop(self, ctx):
        """Open the Snowball Shop."""
        if not await self.check_channel(ctx): return
        if not await self.check_season(ctx): return
        if not await self.check_status(ctx): return

        guild_conf = self.config.guild(ctx.guild)
        last_refresh = await guild_conf.shop_last_refresh()
        
        if int(time.time()) - last_refresh > 600:
            shop_items = await self._refresh_shop(ctx.guild)
        else:
            shop_items = await guild_conf.shop_inventory()
            if not shop_items:
                 shop_items = await self._refresh_shop(ctx.guild)

        if not shop_items:
            return await ctx.send("The shop is empty! An admin needs to add items via `[p]snowballset item add`.")

        currency = await bank.get_currency_name(ctx.guild)
        all_items = await guild_conf.items()

        embed = discord.Embed(title="❄️ The Snowball Shop", color=discord.Color.blue())
        embed.description = f"Refreshes every 10 minutes. You have: {await bank.get_balance(ctx.author)} {currency}"

        view = View(timeout=600)
        unique_shop = list(set(shop_items))

        for item_name in unique_shop:
            if item_name not in all_items:
                continue 
                
            item = all_items[item_name]
            price = item['price']
            i_type = item['type']
            
            # Dynamic Description based on type
            if i_type == 'booster':
                durability = item.get('durability', 1) 
                desc_str = f"Type: Booster | Bonus: +{item['bonus']} Balls | Durability: {durability}"
            elif i_type == 'drink':
                duration = item.get('duration', 60)
                desc_str = f"Type: Drink | Bonus: +{item['bonus']} Dmg | Duration: {duration}s"
            elif i_type == 'cookie':
                desc_str = f"Type: Cookie | Heals: 5-15 + {item['bonus']} HP"
            else:
                desc_str = f"Type: {i_type} | Bonus: {item['bonus']}"

            embed.add_field(name=f"{item_name} - {price} {currency}", value=desc_str, inline=False)

            async def button_callback(interaction, i_name=item_name, i_price=price):
                # Re-check season
                conf = self.config.guild(interaction.guild)
                s_str = await conf.season_start_str()
                e_str = await conf.season_end_str()
                
                if s_str != "0" and e_str != "0":
                    start_dt, end_dt = self.get_season_dates(s_str, e_str)
                    if datetime.now() > end_dt:
                        return await interaction.response.send_message("The season has ended! Shop closed.", ephemeral=True)

                if not await bank.can_spend(interaction.user, i_price):
                    return await interaction.response.send_message("You cannot afford this!", ephemeral=True)
                
                await bank.withdraw_credits(interaction.user, i_price)
                
                async with self.config.member(interaction.user).inventory() as inv:
                    if i_name in inv:
                        inv[i_name] += 1
                    else:
                        inv[i_name] = 1
                
                async with self.config.member(interaction.user).all() as stats:
                    stats['stat_credits_spent'] += i_price
                
                await interaction.response.send_message(f"You bought **{i_name}**!", ephemeral=True)

            button = Button(label=f"Buy {item_name}", style=discord.ButtonStyle.primary)
            button.callback = button_callback
            view.add_item(button)

        await ctx.send(embed=embed, view=view)

    # --- Commands: Leaderboard & Stats ---
    
    @commands.command()
    async def snowstats(self, ctx):
        """View the Snowball Leaderboard."""
        if not await self.check_channel(ctx):
            return

        all_members = await self.config.all_members(ctx.guild)
        if not all_members:
            return await ctx.send("No stats recorded yet!")

        view = LeaderboardView(ctx, all_members)
        embed = view.generate_embed()
        
        await ctx.send(embed=embed, view=view)

    @commands.command()
    async def mysnowstats(self, ctx):
        """Check your own stats and HP."""
        if not await self.check_channel(ctx):
            return

        data = await self.config.member(ctx.author).all()
        inv = data['inventory']
        
        inv_str = humanize_list([f"{k} (x{v})" for k, v in inv.items() if v > 0])
        if not inv_str:
            inv_str = "Empty"

        active_booster = data.get('active_booster')
        if active_booster:
            active_str = f"{active_booster['name']} ({active_booster['current_durability']}/{active_booster['max_durability']} dur)"
        else:
            active_str = "None"
        
        snow_prob = await self.config.guild(ctx.guild).snowfall_probability()

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Snow Profile", color=discord.Color.green())
        
        # Row 1
        embed.add_field(name="Health", value=f"{data['hp']}/100", inline=True)
        embed.add_field(name="Snowballs", value=data['snowballs'], inline=True)
        embed.add_field(name="Weather", value=f"{snow_prob}% Chance", inline=True)
        
        # Row 2
        embed.add_field(name="Equipped", value=active_str, inline=False)
        embed.add_field(name="Inventory", value=inv_str, inline=False)
        
        # Row 3: Frostbite Stats
        inflicted = data.get("stat_frostbites_inflicted", 0)
        taken = data.get("stat_frostbites_taken", 0)
        embed.add_field(name="Frostbite Stats", value=f"❄️ Inflicted: {inflicted}\n🥶 Received: {taken}", inline=True)
        
        # Row 4: Career Stats
        career_stats = (
            f"⚔️ Damage Dealt: {data['stat_damage_dealt']}\n"
            f"🤕 Hits Taken: {data['stat_hits_taken']}\n"
            f"🍪 Cookies Eaten: {data.get('stat_cookies_eaten', 0)}\n"
            f"☕ Drinks Drunk: {data.get('stat_drinks_drunk', 0)}\n"
            f"❄️ Total Balls Made: {data['stat_snowballs_made']}\n"
            f"💰 Credits Spent: {data['stat_credits_spent']}"
        )
        embed.add_field(name="Career Stats", value=career_stats, inline=True)
        
        active_drink = data.get('active_drink')
        if active_drink and active_drink['expires_at'] > time.time():
            embed.add_field(name="Active Effects", value=f"☕ **{active_drink['name']}** (+{active_drink['bonus']} Dmg) - Ends <t:{active_drink['expires_at']}:R>", inline=False)
        
        if data['frostbite_end'] > time.time():
            embed.add_field(name="Status", value=f"🥶 Frostbite (<t:{data['frostbite_end']}:R>)", inline=False)

        await ctx.send(embed=embed)


    # --- Admin Configuration ---

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def snowballset(self, ctx):
        """Configuration for the Snowball system."""
        pass

    @snowballset.command(name="reset")
    async def reset_game(self, ctx):
        """
        DANGER: Resets ALL player stats, inventories, and snowballs.
        This does not remove the items from the shop.
        """
        await self.config.clear_all_members(ctx.guild)
        await self.config.guild(ctx.guild).last_season_year.set(0) # Reset season tracker
        await ctx.send("🚨 **GAME RESET!** 🚨\nAll player HP, stats, snowballs, and inventories have been wiped. Let the new games begin!")

    @snowballset.command(name="dates")
    async def set_dates(self, ctx, start_md: str, end_md: str):
        """
        Set the recurring start and end dates for the Snowball season.
        Format: MM-DD (e.g. 12-01 01-07).
        Use '0' for both to disable the date check.
        """
        if start_md == "0" and end_md == "0":
            await self.config.guild(ctx.guild).season_start_str.set("0")
            await self.config.guild(ctx.guild).season_end_str.set("0")
            return await ctx.send("📅 Season date checks disabled. The game is open indefinitely.")

        try:
            # Validate format
            datetime.strptime(f"2020-{start_md}", "%Y-%m-%d")
            datetime.strptime(f"2020-{end_md}", "%Y-%m-%d")
        except ValueError:
            return await ctx.send("⚠️ Invalid format. Please use `MM-DD` (e.g. `12-01`).")

        await self.config.guild(ctx.guild).season_start_str.set(start_md)
        await self.config.guild(ctx.guild).season_end_str.set(end_md)
        
        # Calculate next dates for display
        s_dt, e_dt = self.get_season_dates(start_md, end_md)
        
        await ctx.send(f"📅 Season configured!\nNext Window: <t:{int(s_dt.timestamp())}:D> to <t:{int(e_dt.timestamp())}:D>")

    @snowballset.command(name="view")
    async def view_settings(self, ctx):
        """View the current game configuration and shop items."""
        guild_data = await self.config.guild(ctx.guild).all()
        
        channel_id = guild_data['channel_id']
        channel_obj = ctx.guild.get_channel(channel_id) if channel_id else None
        channel_str = channel_obj.mention if channel_obj else "Anywhere (None set)"
        
        start_str = guild_data.get('season_start_str', "0")
        end_str = guild_data.get('season_end_str', "0")
        
        if start_str == "0":
            date_str = "Indefinite (Always Open)"
        else:
            date_str = f"Recurring: {start_str} to {end_str}"

        embed = discord.Embed(title="⚙️ Snowball Settings", color=discord.Color.light_grey())
        embed.add_field(name="Fight Channel", value=channel_str, inline=True)
        embed.add_field(name="Season Dates", value=date_str, inline=True)
        embed.add_field(name="Snowball Roll Time", value=f"{guild_data['snowball_roll_time']} seconds", inline=True)
        
        items = guild_data['items']
        
        # Categorize items to avoid Field Limits
        cookies = {k: v for k, v in items.items() if v['type'] == 'cookie'}
        drinks = {k: v for k, v in items.items() if v['type'] == 'drink'}
        boosters = {k: v for k, v in items.items() if v['type'] == 'booster'}
        
        def format_list(item_dict):
            if not item_dict: return "None"
            lines = []
            for name, data in item_dict.items():
                extra = ""
                if data['type'] == 'booster':
                    extra = f" | Dur: {data.get('durability', 1)}"
                elif data['type'] == 'drink':
                    extra = f" | Time: {data.get('duration', 60)}s"
                
                lines.append(f"**{name}**: Cost {data['price']} | Bonus +{data['bonus']}{extra}")
            return "\n".join(lines)

        embed.add_field(name="🍪 Cookies", value=format_list(cookies), inline=False)
        embed.add_field(name="☕ Drinks", value=format_list(drinks), inline=False)
        embed.add_field(name="⚡ Boosters", value=format_list(boosters), inline=False)
        
        await ctx.send(embed=embed)

    @snowballset.group(name="item")
    async def snowballset_item(self, ctx):
        """Manage items."""
        pass

    @snowballset_item.command(name="add")
    async def item_add(self, ctx, type: str, rarity: int, name: str, bonus: int, price: int, durability: int = 1, duration: int = 60):
        """
        Add an item to the store.
        Type: booster, drink, cookie
        Rarity: 1 (Common) to 10 (Rare)
        Durability: Uses (Boosters only)
        Duration: Seconds (Drinks only)
        """
        type = type.lower()
        if type not in ["booster", "drink", "cookie"]:
            return await ctx.send("Type must be one of: booster, drink, cookie")
        
        if not (1 <= rarity <= 10):
            return await ctx.send("Rarity must be between 1 and 10.")
        
        if durability < 1:
            return await ctx.send("Durability must be at least 1.")
            
        if duration < 10:
             return await ctx.send("Duration must be at least 10 seconds.")

        async with self.config.guild(ctx.guild).items() as items:
            items[name] = {
                "type": type,
                "rarity": rarity,
                "bonus": bonus,
                "price": price,
                "durability": durability,
                "duration": duration
            }
        
        await ctx.send(f"Added item **{name}** ({type}) - Cost: {price}, Rarity: {rarity}")

    @snowballset_item.command(name="remove")
    async def item_remove(self, ctx, name: str):
        """Remove an item from the registry."""
        async with self.config.guild(ctx.guild).items() as items:
            if name in items:
                del items[name]
                await ctx.send(f"Removed **{name}**.")
            else:
                await ctx.send("Item not found.")

    @snowballset.command(name="rolltime")
    async def set_rolltime(self, ctx, seconds: int):
        """Set the base time (in seconds) it takes to make snowballs."""
        if seconds < 5:
            return await ctx.send("Minimum time is 5 seconds.")
        await self.config.guild(ctx.guild).snowball_roll_time.set(seconds)
        await ctx.send(f"Snowball making time set to {seconds} seconds.")

    @snowballset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """
        Set the channel where snowball fights are allowed..
        Leave blank to allow fights in all channels.
        """
        if channel is None:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("Snowball fight restriction removed. You can fight anywhere!")
        else:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"Snowball fights are now restricted to {channel.mention}.")