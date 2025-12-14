import discord
import asyncio
import random
import logging
import time
import datetime
from typing import Optional, Literal
from redbot.core import commands, Config, checks, bank
from redbot.core.utils.chat_formatting import box, pagify, humanize_timedelta
from redbot.core.utils.predicates import ReactionPredicate

log = logging.getLogger("red.bang")

class Bang(commands.Cog):
    """
    A reaction-based hunting game.
    Wait for the creature's cry, then be the first to BANG!
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        default_creatures = {
            "dove": {"name": "Dove", "type": "normal", "emoji": ":dove:", "cry": "**_Coo!_**", "difficulty": 1},
            "penguin": {"name": "Penguin", "type": "normal", "emoji": ":penguin:", "cry": "**_Noot!_**", "difficulty": 1},
            "chicken": {"name": "Chicken", "type": "normal", "emoji": ":chicken:", "cry": "**_Bah-gawk!_**", "difficulty": 1},
            "duck": {"name": "Duck", "type": "normal", "emoji": ":duck:", "cry": "**_Quack!_**", "difficulty": 1},
            "turkey": {"name": "Turkey", "type": "normal", "emoji": ":turkey:", "cry": "**_Gobble-Gobble!_**", "difficulty": 1},
            "owl": {"name": "Owl", "type": "normal", "emoji": ":owl:", "cry": "**_Hoo-Hooo!_**", "difficulty": 1},
            "parrot": {"name": "Parrot", "type": "normal", "emoji": ":parrot:", "cry": "**CACAOOOOOO**", "difficulty": 1},
            "bee": {"name": "Bee", "type": "normal", "emoji": ":bee:", "cry": "**BUZZ OFF**", "difficulty": 1},
            "flamingo": {"name": "Flamingo", "type": "normal", "emoji": ":flamingo:", "cry": "**_SQUONK_**", "difficulty": 1},
            "rabbit_god": {"name": "Dude with a god complex wearing a rabbit costume", "type": "normal", "emoji": ":rabbit:", "cry": "**_I CAN FIX YOU_**", "difficulty": 1},
            "peacock": {"name": "Peacock", "type": "normal", "emoji": ":peacock:", "cry": "**_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA_**", "difficulty": 1},
            "goose": {"name": "Goose", "type": "normal", "emoji": "<:canadagoose:1325943169279332443>", "cry": "**_HONK HONK MOTHERHONKERS_**", "difficulty": 1},
            "normal_goose": {"name": "Totally Normal Goose", "type": "normal", "emoji": ":goose:", "cry": "**_HONK HONK FELLOW NORMAL GOOSE PEOPLE_**", "difficulty": 1},
            "australian_chef": {"name": "Australian Chef", "type": "normal", "emoji": ":cook:", "cry": "**g'day mate!**", "difficulty": 1},
            "ostrich": {"name": "Ostrich", "type": "normal", "emoji": "<:ostrich:1211039583173615686>", "cry": "**HI! I don't know what sound an ostrich makes!**", "difficulty": 1},
            "santaur": {"name": "Santaur", "type": "normal", "emoji": "<:santaur:1321157516289376322>", "cry": "**HO HO HO** Hoes hoes hoes! All of you!", "difficulty": 1},
            "harambe": {"name": "Harambe", "type": "normal", "emoji": ":gorilla:", "cry": "**_LISTEN CLOSELY, I ONLY HAVE A FEW MINUTES. YOU SHOULD START STOCKING UP TOILET PAPER AND HAND SANITIZER...._**", "difficulty": 1},
            "dodo": {"name": "Dodo", "type": "normal", "emoji": ":dodo:", "cry": "**_Squak!_**", "difficulty": 1},
        }

        default_guild = {
            "channel_id": None,
            "keyword": "bang",
            "min_interval": 600,  # 10 minutes
            "max_interval": 3600, # 1 hour
            "bang_timeout": 60,   # 60 seconds to kill it
            "base_hit_chance": 75, # Percent
            "reward": 0,          # Credits reward
            "creatures": default_creatures, 
            "enabled": False,
            "next_spawn_timestamp": 0
        }

        default_member = {
            "score": 0,
            "rifle_level": 1,
            "rifle_name": "Rusty Musket"
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        # Temp storage for active hunts: {guild_id: {'creature': dict, 'task': task}}
        self.active_creatures = {}
        # Locks to prevent race conditions on "first" bang
        self.locks = {}
        
        self.spawn_loop_task = self.bot.loop.create_task(self.spawn_loop())

    def cog_unload(self):
        if self.spawn_loop_task:
            self.spawn_loop_task.cancel()
        # Cancel any active fleeing tasks
        for data in self.active_creatures.values():
            if 'task' in data:
                data['task'].cancel()

    async def spawn_loop(self):
        """
        Main loop handling creature spawning across all guilds.
        """
        await self.bot.wait_until_ready()
        while True:
            try:
                now = time.time()
                all_guilds = await self.config.all_guilds()
                
                for guild_id, data in all_guilds.items():
                    if not data["enabled"] or not data["channel_id"] or not data["creatures"]:
                        continue

                    # Skip if something is already alive there
                    if guild_id in self.active_creatures:
                        continue
                    
                    # Check schedule
                    next_spawn = data.get("next_spawn_timestamp", 0)
                    
                    # If not initialized, schedule it now
                    if next_spawn == 0:
                        await self.schedule_next_spawn(guild_id, data)
                        continue

                    if now >= next_spawn:
                        await self.spawn_creature(guild_id, data)
                        # Schedule the next one immediately after spawning
                        await self.schedule_next_spawn(guild_id, data)

                await asyncio.sleep(5) 
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Error in spawn loop", exc_info=e)
                await asyncio.sleep(60)

    async def schedule_next_spawn(self, guild_id, data):
        """Calculates and saves the next spawn timestamp."""
        mn = data["min_interval"]
        mx = data["max_interval"]
        if mn > mx: mn = mx
        
        # Seconds until next spawn
        delay = random.randint(mn, mx)
        next_ts = time.time() + delay
        
        await self.config.guild_from_id(guild_id).next_spawn_timestamp.set(next_ts)

    async def spawn_creature(self, guild_id, data):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        channel = guild.get_channel(data["channel_id"])
        if not channel:
            return

        creature_list = list(data["creatures"].values())
        if not creature_list:
            return

        creature = random.choice(creature_list)
        timeout = data.get("bang_timeout", 60)
        
        # Post the cry
        try:
            embed = discord.Embed(
                title=f"{creature['emoji']} A wild {creature['name']} appears!",
                description=f"{creature['cry']}\n\n*You have {timeout} seconds to hunt it!*",
                color=discord.Color.green() if creature['type'] == 'normal' else discord.Color.red()
            )
            await channel.send(embed=embed)
            
            # Start despawn timer
            task = self.bot.loop.create_task(self.despawn_creature(guild_id, timeout))
            
            self.active_creatures[guild_id] = {
                "creature": creature,
                "task": task,
                "channel_id": channel.id
            }
        except discord.Forbidden:
            log.warning(f"Missing permissions to send to {channel.id} in {guild.name}")

    async def despawn_creature(self, guild_id, timeout):
        """Waits for timeout then removes creature if not hunted."""
        try:
            await asyncio.sleep(timeout)
            if guild_id in self.active_creatures:
                data = self.active_creatures.pop(guild_id)
                creature = data['creature']
                
                guild = self.bot.get_guild(guild_id)
                if guild:
                    channel = guild.get_channel(data["channel_id"])
                    if channel:
                        embed = discord.Embed(
                            description=f"The **{creature['name']}** got away!",
                            color=discord.Color.dark_grey()
                        )
                        await channel.send(embed=embed)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Error in despawn task for guild {guild_id}", exc_info=e)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id
        
        # Check if there is an active creature
        if guild_id not in self.active_creatures:
            return
            
        # Check channel
        conf = await self.config.guild(message.guild).all()
        if message.channel.id != conf["channel_id"]:
            return

        # Check keyword
        if message.content.lower().strip() == conf["keyword"].lower():
            # Use lock to ensure only one person claims the kill
            if guild_id not in self.locks:
                self.locks[guild_id] = asyncio.Lock()
            
            async with self.locks[guild_id]:
                # Double check inside lock
                if guild_id in self.active_creatures:
                    creature_data = self.active_creatures.pop(guild_id)
                    
                    # Cancel the flee timer
                    if 'task' in creature_data:
                        creature_data['task'].cancel()
                        
                    await self.process_bang(message, creature_data['creature'], conf)

    async def process_bang(self, message, creature, conf):
        user = message.author
        user_conf = await self.config.member(user).all()
        
        # Mechanics
        is_illegal = creature['type'] == 'illegal'
        rifle_lvl = user_conf['rifle_level']
        
        # Math: Chance calculation
        # Base chance + (Rifle Level * 5). Cap at 95%.
        hit_chance = conf['base_hit_chance'] + (rifle_lvl * 5)
        hit_chance = min(hit_chance, 95)
        
        roll = random.randint(1, 100)
        hit_success = roll <= hit_chance

        if is_illegal:
            # Penalty
            penalty = 50
            new_score = user_conf['score'] - penalty
            await self.config.member(user).score.set(new_score)
            
            embed = discord.Embed(
                title="⛔ ILLEGAL TARGET HIT!",
                description=f"{user.mention} shot the **{creature['name']}**!\nThat was an ILLEGAL target.\n\n**Penalty:** -{penalty} points.",
                color=discord.Color.dark_red()
            )
            await message.channel.send(embed=embed)
        
        elif hit_success:
            # Success logic
            points = 10 * creature.get('difficulty', 1) # Default multiplier 1
            new_score = user_conf['score'] + points
            await self.config.member(user).score.set(new_score)
            
            reward_msg = f"**+ {points} points**"
            
            # Currency Reward
            reward_amt = conf.get("reward", 0)
            if reward_amt > 0:
                try:
                    await bank.deposit_credits(user, reward_amt)
                    currency_name = await bank.get_currency_name(message.guild)
                    reward_msg += f"\n**+ {reward_amt} {currency_name}**"
                except Exception as e:
                    log.error(f"Failed to deposit credits to {user.id}", exc_info=e)
            
            embed = discord.Embed(
                title="🎯 BANG!",
                description=f"{user.mention} successfully hunted the **{creature['name']}**!\n\n{reward_msg}",
                color=discord.Color.gold()
            )
            await message.channel.send(embed=embed)
            
        else:
            # Miss logic
            embed = discord.Embed(
                title="☁️ Missed!",
                description=f"{user.mention} fired their {user_conf['rifle_name']} but missed the **{creature['name']}**!\nIt got away safely.",
                color=discord.Color.light_grey()
            )
            await message.channel.send(embed=embed)

    # ---------------------------------------------------------------------
    # COMMANDS
    # ---------------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    async def bangset(self, ctx):
        """
        Settings for the Bang game.
        """
        pass

    @bangset.command(name="start")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_start(self, ctx, channel: discord.TextChannel):
        """
        Set the hunting channel and ensure the game is ready.
        Note: Game must be toggled ON for this to spawn creatures.
        """
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        conf = await self.config.guild(ctx.guild).all()
        if not conf["enabled"]:
            await ctx.send(f"Channel set to {channel.mention}.\n**Warning:** The game is currently disabled. Use `{ctx.clean_prefix}bangset toggle` to turn it on.")
        else:
            # If enabled, reset the timestamp to 0 to trigger a schedule check immediately
            await self.config.guild(ctx.guild).next_spawn_timestamp.set(0)
            await ctx.send(f"Hunt enabled in {channel.mention}! Use `{ctx.clean_prefix}bangset next` to see when the creature arrives.")

    @bangset.command(name="timing")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_timing(self, ctx, min_interval: int, max_interval: int, bang_timeout: int):
        """
        Set the spawn intervals and flee timeout (in seconds).
        
        <min_interval>: Minimum seconds between spawns (e.g. 600)
        <max_interval>: Maximum seconds between spawns (e.g. 3600)
        <bang_timeout>: Seconds before the creature runs away (e.g. 60)
        """
        if min_interval < 60:
            return await ctx.send("Minimum interval must be at least 60 seconds.")
        if min_interval > max_interval:
            return await ctx.send("Minimum interval cannot be larger than maximum interval.")
            
        await self.config.guild(ctx.guild).min_interval.set(min_interval)
        await self.config.guild(ctx.guild).max_interval.set(max_interval)
        await self.config.guild(ctx.guild).bang_timeout.set(bang_timeout)
        
        # Reset schedule
        await self.config.guild(ctx.guild).next_spawn_timestamp.set(0)
        
        await ctx.send(f"Timing updated:\nSpawns every {min_interval}-{max_interval}s.\nCreatures flee after {bang_timeout}s.")

    @bangset.command(name="reward")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_reward(self, ctx, amount: int):
        """
        Set the credit reward for a successful hunt. Set to 0 to disable.
        """
        if amount < 0:
            return await ctx.send("Reward cannot be negative.")
            
        await self.config.guild(ctx.guild).reward.set(amount)
        currency = await bank.get_currency_name(ctx.guild)
        await ctx.send(f"Reward set to {amount} {currency}.")

    @bangset.command(name="resetall")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_resetall(self, ctx):
        """
        Resets ALL user scores and rifle levels.
        """
        msg = await ctx.send("Are you sure you want to reset **ALL** user stats for this guild? This cannot be undone.")
        start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(msg, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Action cancelled.")

        if pred.result is True:
            await self.config.clear_all_members(ctx.guild)
            await ctx.send("All stats have been reset.")
        else:
            await ctx.send("Action cancelled.")

    @bangset.command(name="next")
    async def bangset_next(self, ctx):
        """
        Shows time remaining until the next creature spawn.
        """
        if ctx.guild.id in self.active_creatures:
            creature = self.active_creatures[ctx.guild.id]['creature']
            return await ctx.send(f"There is a **{creature['name']}** active RIGHT NOW! Find it!")

        conf = await self.config.guild(ctx.guild).all()
        if not conf["enabled"]:
            return await ctx.send("The game is currently disabled.")
            
        timestamp = conf["next_spawn_timestamp"]
        now = time.time()
        
        if timestamp == 0:
            return await ctx.send("Scheduling next spawn...")
            
        remaining = timestamp - now
        if remaining < 0:
            return await ctx.send("Any second now...")
            
        # Format seconds to string
        delta = discord.utils.utcnow() + datetime.timedelta(seconds=remaining)
        relative = discord.utils.format_dt(delta, 'R')
        
        await ctx.send(f"Next creature expected {relative}.")

    @bangset.command(name="keyword")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_keyword(self, ctx, keyword: str):
        """
        Set the trigger keyword (e.g., "bang", "shoot", "pow").
        """
        await self.config.guild(ctx.guild).keyword.set(keyword)
        await ctx.send(f"The hunting keyword has been set to: `{keyword}`")

    @bangset.command(name="channel")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_channel(self, ctx, channel: discord.TextChannel):
        """
        Set the channel where creatures will spawn.
        """
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Creatures will now spawn in {channel.mention}.")

    @bangset.command(name="toggle")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_toggle(self, ctx):
        """
        Toggle the game on or off.
        """
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        if new_state:
             # Reset timestamp to trigger immediate schedule
             await self.config.guild(ctx.guild).next_spawn_timestamp.set(0)
        
        state = "enabled" if new_state else "disabled"
        await ctx.send(f"Bang game is now **{state}**.")

    @bangset.group(name="creature")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_creature(self, ctx):
        """
        Manage creatures.
        """
        pass

    @bangset_creature.command(name="add")
    async def creature_add(self, ctx, name: str, type: Literal["normal", "illegal"], emoji: str, *, cry: str):
        """
        Add a creature.
        
        Usage: [p]bangset creature add <name> <type> <emoji> <cry>
        Type must be 'normal' or 'illegal'.
        """
        async with self.config.guild(ctx.guild).creatures() as creatures:
            creatures[name.lower()] = {
                "name": name,
                "type": type,
                "emoji": emoji,
                "cry": cry,
                "difficulty": 1 # Placeholder for future expansion
            }
        await ctx.send(f"Added creature **{name}** ({type}).")

    @bangset_creature.command(name="remove")
    async def creature_remove(self, ctx, name: str):
        """
        Remove a creature.
        """
        async with self.config.guild(ctx.guild).creatures() as creatures:
            if name.lower() in creatures:
                del creatures[name.lower()]
                await ctx.send(f"Removed **{name}**.")
            else:
                await ctx.send(f"Creature **{name}** not found.")

    @bangset_creature.command(name="list")
    async def creature_list(self, ctx):
        """
        List all configured creatures.
        """
        creatures = await self.config.guild(ctx.guild).creatures()
        if not creatures:
            return await ctx.send("No creatures configured.")
        
        msg = ""
        for name, data in creatures.items():
            msg += f"{data['emoji']} **{data['name']}** ({data['type']})\nCry: {data['cry']}\n\n"
        
        for page in pagify(msg):
            await ctx.send(box(page))

    @bangset.command(name="view")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_view(self, ctx):
        """
        View all settings.
        """
        conf = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(conf['channel_id']) if conf['channel_id'] else "Not Set"
        currency = await bank.get_currency_name(ctx.guild)
        
        embed = discord.Embed(title="Bang! Settings", color=discord.Color.blue())
        embed.add_field(name="Status", value="Enabled" if conf['enabled'] else "Disabled")
        embed.add_field(name="Channel", value=getattr(channel, 'mention', channel))
        embed.add_field(name="Keyword", value=conf['keyword'])
        embed.add_field(name="Timing", value=f"Spawn: {conf['min_interval']}-{conf['max_interval']}s\nFlee Timeout: {conf['bang_timeout']}s")
        embed.add_field(name="Reward", value=f"{conf['reward']} {currency}")
        embed.add_field(name="Creature Count", value=len(conf['creatures']))
        embed.add_field(name="Base Hit Chance", value=f"{conf['base_hit_chance']}%")
        
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    async def bangboard(self, ctx):
        """
        Show the hunting leaderboard.
        """
        # Get all members in config
        all_members = await self.config.all_members(ctx.guild)
        
        # Sort by score
        sorted_members = sorted(all_members.items(), key=lambda x: x[1]['score'], reverse=True)
        
        if not sorted_members:
            return await ctx.send("No scores recorded yet.")
            
        msg = ""
        for i, (uid, data) in enumerate(sorted_members[:10], 1):
            user = ctx.guild.get_member(uid)
            name = user.display_name if user else f"Unknown User ({uid})"
            msg += f"{i}. {name}: {data['score']} points (Rifle Lv {data['rifle_level']})\n"
            
        embed = discord.Embed(title="Bang! Leaderboard", description=box(msg), color=discord.Color.orange())
        await ctx.send(embed=embed)

    # Placeholder for upgrade system
    @bangset.command(name="upgradechance")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_upgradechance(self, ctx, chance: int):
        """
        Set the base hit chance (0-100).
        """
        if not 0 <= chance <= 100:
            return await ctx.send("Chance must be between 0 and 100.")
        await self.config.guild(ctx.guild).base_hit_chance.set(chance)
        await ctx.send(f"Base hit chance set to {chance}%.")