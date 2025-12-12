import discord
import asyncio
import random
import time
from redbot.core import commands, Config, bank
from redbot.core.utils.chat_formatting import box, humanize_list
from discord.ext import tasks
from discord.ui import View, Button

class Snowball(commands.Cog):
    """
    A Snowball fighting system with items, health, hilarity, and weather.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        # Default Settings
        default_guild = {
            "items": {},  # Stores defined items
            "shop_inventory": [], # Current 5 items in rotation
            "shop_last_refresh": 0,
            "snowball_roll_time": 60, # Seconds to make snowballs
            "channel_id": None, # The designated channel ID
            "snowfall_probability": 50 # 0-100, defaults to average
        }

        default_member = {
            "hp": 100,
            "snowballs": 0,
            "coffee_drunk": 0,
            "inventory": {}, # {item_name: count}
            "frostbite_end": 0, # Timestamp
            "pooped_end": 0,    # Timestamp
            
            # Stats
            "stat_damage_dealt": 0,
            "stat_cocoa_drunk": 0,
            "stat_coffee_drunk": 0,
            "stat_snowballs_made": 0,
            "stat_hits_taken": 0,
            "stat_hp_lost": 0,
            "stat_hp_gained": 0,
            "stat_credits_spent": 0
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
        """Calculates snowfall probability every 15 minutes."""
        for guild in self.bot.guilds:
            # Generate probability 0-100
            probability = random.randint(0, 100)
            await self.config.guild(guild).snowfall_probability.set(probability)
            
            # Check for heavy snow
            if probability > 85:
                channel_id = await self.config.guild(guild).channel_id()
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    # Ensure bot can speak there and channel exists
                    if channel and channel.permissions_for(guild.me).send_messages:
                        await channel.send("🌨️**It's snowing!**❄️")

    @snowfall_loop.before_loop
    async def before_snowfall_loop(self):
        await self.bot.wait_until_red_ready()

    # --- Helper Functions ---

    async def check_channel(self, ctx):
        """Ensures the command is used in the allowed channel."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        
        if not channel_id:
            return True
        
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return True

        if ctx.channel.id != channel_id:
            await ctx.send(f"🚫 Snowball fights are only allowed in {channel.mention}!", delete_after=5)
            return False
            
        return True

    async def check_status(self, ctx):
        """Checks if the user is frozen or pooped."""
        member_conf = self.config.member(ctx.author)
        data = await member_conf.all()
        now = int(time.time())

        if data["pooped_end"] > now:
            relative = f"<t:{data['pooped_end']}:R>"
            await ctx.send(f"💩 You've pooped your pants. Come back in {relative}.")
            return False

        if data["frostbite_end"] > now:
            relative = f"<t:{data['frostbite_end']}:R>"
            await ctx.send(f"🥶 You've got Frostbite. Chill for {relative}.")
            return False
        
        if data["frostbite_end"] != 0 and data["frostbite_end"] <= now and data["hp"] <= 0:
            await member_conf.hp.set(100)
            await member_conf.frostbite_end.set(0)
            await ctx.send(f"🔥 **{ctx.author.display_name}** has thawed out and is ready to fight again!")

        return True

    async def get_active_booster(self, user):
        """Returns the best booster bonus."""
        inventory = await self.config.member(user).inventory()
        items = await self.config.guild(user.guild).items()
        
        best_bonus = 0
        best_time_red = 0 
        
        for item_name, count in inventory.items():
            if count > 0 and item_name in items:
                item = items[item_name]
                if item['type'] == 'booster':
                    if item['bonus'] > best_bonus:
                        best_bonus = item['bonus']
                        best_time_red = item['bonus'] * 15 
                        
        return best_bonus, best_time_red

    # --- Commands: Snowball Making ---

    @commands.command()
    async def makesnowballs(self, ctx):
        """Start making snowballs."""
        if not await self.check_channel(ctx):
            return
        if not await self.check_status(ctx):
            return

        # Boosters
        item_bonus, time_reduction = await self.get_active_booster(ctx.author)
        
        # Weather
        snow_prob = await self.config.guild(ctx.guild).snowfall_probability()
        # Calculation: (Prob - 50) / 10. 
        # 100% -> +5 balls. 50% -> 0 balls. 0% -> -5 balls.
        weather_mod = int((snow_prob - 50) / 10)

        # Time
        base_time = await self.config.guild(ctx.guild).snowfall_roll_time()
        actual_time = max(5, base_time - time_reduction)
        
        msg = await ctx.send(f"❄️ gathering snow... (Probability: {snow_prob}% | Time: {actual_time}s)")
        
        async with ctx.typing():
            await asyncio.sleep(actual_time)
            
        if not await self.check_status(ctx):
            return

        base_roll = random.randint(1, 6)
        
        # Calculate Total
        total_balls = base_roll + item_bonus + weather_mod
        if total_balls < 1:
            total_balls = 1 # Minimum 1
        
        async with self.config.member(ctx.author).all() as data:
            data['snowballs'] += total_balls
            data['stat_snowballs_made'] += total_balls

        # Breakdown string
        calc_str = f"Base: {base_roll} + Items: {item_bonus} + Weather: {weather_mod}"
        await msg.edit(content=f"☃️ You made **{total_balls}** snowballs! ({calc_str})")

    # --- Commands: Consumables ---

    @commands.command()
    async def hotchocolate(self, ctx):
        """Drink hot chocolate to heal."""
        if not await self.check_channel(ctx):
            return
        if not await self.check_status(ctx):
            return

        user_inv = await self.config.member(ctx.author).inventory()
        guild_items = await self.config.guild(ctx.guild).items()
        
        found_item_name = None
        item_data = None

        for name, count in user_inv.items():
            if count > 0 and name in guild_items:
                if guild_items[name]['type'] == 'chocolate':
                    found_item_name = name
                    item_data = guild_items[name]
                    break
        
        if not found_item_name:
            return await ctx.send("You don't have any Hot Chocolate! Buy some in the `[p]snowshop`.")

        heal_amount = random.randint(5, 15) + item_data['bonus']
        
        async with self.config.member(ctx.author).all() as data:
            data['inventory'][found_item_name] -= 1
            old_hp = data['hp']
            data['hp'] = min(100, old_hp + heal_amount)
            data['stat_cocoa_drunk'] += 1
            data['stat_hp_gained'] += heal_amount
            actual_heal = data['hp'] - old_hp

        await ctx.send(f"☕ You drank {found_item_name} and recovered **{actual_heal} HP**. Current HP: {data['hp']}")

    @commands.command()
    async def coffee(self, ctx):
        """Drink coffee to boost damage."""
        if not await self.check_channel(ctx):
            return
        if not await self.check_status(ctx):
            return

        user_inv = await self.config.member(ctx.author).inventory()
        guild_items = await self.config.guild(ctx.guild).items()
        
        found_item_name = None
        
        for name, count in user_inv.items():
            if count > 0 and name in guild_items:
                if guild_items[name]['type'] == 'coffee':
                    found_item_name = name
                    break
        
        if not found_item_name:
            return await ctx.send("You don't have any Coffee! Buy some in the `[p]snowshop`.")

        async with self.config.member(ctx.author).all() as data:
            if data['coffee_drunk'] >= 3:
                data['pooped_end'] = int(time.time()) + (15 * 60) # 15 mins
                data['coffee_drunk'] = 0 
                data['inventory'][found_item_name] -= 1
                relative = f"<t:{data['pooped_end']}:R>"
                return await ctx.send(f"💩 Oh no! You drank too much coffee and **Pooped Your Pants**! You are out of the game for {relative}.")
            
            data['inventory'][found_item_name] -= 1
            data['coffee_drunk'] += 1
            data['stat_coffee_drunk'] += 1

        await ctx.send(f"☕ You act jittery! Damage bonus active. (Cups: {data['coffee_drunk'] + 1})")

    # --- Commands: Fighting ---

    @commands.command(aliases=["throw"])
    async def throwball(self, ctx, target: discord.Member):
        """Throw a snowball at someone!"""
        if not await self.check_channel(ctx):
            return
        if not await self.check_status(ctx):
            return
        
        if target.id == ctx.author.id:
            return await ctx.send("Don't hit yourself.")

        author_data = await self.config.member(ctx.author).all()
        if author_data['snowballs'] < 1:
            return await ctx.send("You have no snowballs! Run `[p]makesnowballs` first.")

        target_data = await self.config.member(target).all()
        if target_data['frostbite_end'] > int(time.time()):
            return await ctx.send(f"{target.display_name} is already frozen solid! Leave them alone.")

        damage = random.randint(1, 6)
        coffee_bonus = author_data['coffee_drunk'] 
        total_damage = damage + coffee_bonus

        async with self.config.member(ctx.author).all() as a_data:
            a_data['snowballs'] -= 1
            a_data['stat_damage_dealt'] += total_damage

        async with self.config.member(target).all() as t_data:
            t_data['hp'] -= total_damage
            t_data['stat_hits_taken'] += 1
            t_data['stat_hp_lost'] += total_damage
            current_hp = t_data['hp']

        msg = f"☄️ **{ctx.author.display_name}** hit **{target.display_name}** for **{total_damage}** damage! (HP: {current_hp}/100)"

        if current_hp <= 0:
            minutes = 15 + abs(current_hp)
            finish_time = int(time.time()) + (minutes * 60)
            await self.config.member(target).frostbite_end.set(finish_time)
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
            for _ in range(data['rarity']):
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
        if not await self.check_channel(ctx):
            return
        if not await self.check_status(ctx):
            return

        guild_conf = self.config.guild(ctx.guild)
        last_refresh = await guild_conf.shop_last_refresh()
        
        if int(time.time()) - last_refresh > 3600:
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
        embed.description = f"Refreshes every hour. You have: {await bank.get_balance(ctx.author)} {currency}"

        view = View(timeout=60)
        unique_shop = list(set(shop_items))

        for item_name in unique_shop:
            if item_name not in all_items:
                continue 
                
            item = all_items[item_name]
            price = item['price']
            
            desc_str = f"Type: {item['type'].title()} | Bonus: +{item['bonus']}"
            embed.add_field(name=f"{item_name} - {price} {currency}", value=desc_str, inline=False)

            async def button_callback(interaction, i_name=item_name, i_price=price):
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

    # --- Commands: Leaderboard ---
    
    @commands.command()
    async def snowstats(self, ctx):
        """View the Snowball Leaderboard."""
        all_members = await self.config.all_members(ctx.guild)
        sorted_members = sorted(all_members.items(), key=lambda x: x[1]['stat_damage_dealt'], reverse=True)[:10]
        
        embed = discord.Embed(title="🏆 Snowball Championships", color=discord.Color.gold())
        
        desc = ""
        for index, (user_id, data) in enumerate(sorted_members, 1):
            user = ctx.guild.get_member(user_id)
            name = user.display_name if user else "Unknown User"
            
            desc += (
                f"**{index}. {name}**\n"
                f"⚔️ Dmg: {data['stat_damage_dealt']} | 🤕 Taken: {data['stat_hits_taken']}\n"
                f"☕ Coffee: {data['stat_coffee_drunk']} | 🍫 Cocoa: {data['stat_cocoa_drunk']}\n"
                f"❄️ Balls: {data['stat_snowballs_made']} | 💰 Spent: {data['stat_credits_spent']}\n\n"
            )
            
        embed.description = desc
        await ctx.send(embed=embed)

    @commands.command()
    async def mystats(self, ctx):
        """Check your own stats and HP."""
        data = await self.config.member(ctx.author).all()
        inv = data['inventory']
        
        inv_str = humanize_list([f"{k} (x{v})" for k, v in inv.items() if v > 0])
        if not inv_str:
            inv_str = "Empty"
        
        snow_prob = await self.config.guild(ctx.guild).snowfall_probability()

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Snow Profile", color=discord.Color.green())
        embed.add_field(name="Health", value=f"{data['hp']}/100")
        embed.add_field(name="Snowballs", value=data['snowballs'])
        embed.add_field(name="Inventory", value=inv_str, inline=False)
        embed.add_field(name="Weather Forecast", value=f"{snow_prob}% Chance of Snow", inline=False)
        
        if data['frostbite_end'] > time.time():
            embed.add_field(name="Status", value=f"🥶 Frostbite (<t:{data['frostbite_end']}:R>)", inline=False)
        elif data['pooped_end'] > time.time():
            embed.add_field(name="Status", value=f"💩 Pooped (<t:{data['pooped_end']}:R>)", inline=False)
        else:
             embed.add_field(name="Status", value="Healthy", inline=False)

        await ctx.send(embed=embed)


    # --- Admin Configuration ---

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def snowballset(self, ctx):
        """Configuration for the Snowball system."""
        pass

    @snowballset.group(name="item")
    async def snowballset_item(self, ctx):
        """Manage items."""
        pass

    @snowballset_item.command(name="add")
    async def item_add(self, ctx, type: str, rarity: int, name: str, bonus: int, price: int):
        """
        Add an item to the store.
        Type: booster, coffee, chocolate
        Rarity: 1 (Rare) to 10 (Common)
        """
        type = type.lower()
        if type not in ["booster", "coffee", "chocolate"]:
            return await ctx.send("Type must be one of: booster, coffee, chocolate")
        
        if not (1 <= rarity <= 10):
            return await ctx.send("Rarity must be between 1 and 10.")

        async with self.config.guild(ctx.guild).items() as items:
            items[name] = {
                "type": type,
                "rarity": rarity,
                "bonus": bonus,
                "price": price
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
        Set the channel where snowball fights are allowed. 
        Leave blank to allow fights in all channels.
        """
        if channel is None:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("Snowball fight restriction removed. You can fight anywhere!")
        else:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"Snowball fights are now restricted to {channel.mention}.")