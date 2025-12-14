import discord
import asyncio
import random
import logging
from typing import Optional, Literal
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import start_adding_reactions
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

        default_guild = {
            "channel_id": None,
            "keyword": "bang",
            "min_interval": 600,  # 10 minutes
            "max_interval": 3600, # 1 hour
            "base_hit_chance": 75, # Percent
            "creatures": {}, # Dict of creature data
            "enabled": False
        }

        default_member = {
            "score": 0,
            "rifle_level": 1,
            "rifle_name": "Rusty Musket"
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        # Temp storage for active hunts: {guild_id: creature_dict}
        self.active_creatures = {}
        # Locks to prevent race conditions on "first" bang
        self.locks = {}
        
        self.spawn_loop_task = self.bot.loop.create_task(self.spawn_loop())

    def cog_unload(self):
        if self.spawn_loop_task:
            self.spawn_loop_task.cancel()

    async def spawn_loop(self):
        """
        Main loop handling creature spawning across all guilds.
        """
        await self.bot.wait_until_ready()
        while True:
            try:
                # We check every minute if a guild is ready to spawn
                # This is a simplified approach; a more complex one would schedule per guild.
                # To keep it efficient, we just iterate enabled guilds.
                all_guilds = await self.config.all_guilds()
                
                for guild_id, data in all_guilds.items():
                    if not data["enabled"] or not data["channel_id"] or not data["creatures"]:
                        continue

                    # Skip if something is already alive there
                    if guild_id in self.active_creatures:
                        continue

                    # Random chance to spawn this tick? 
                    # To make intervals work, we'd usually use timestamps. 
                    # For simplicity in this implementation, we rely on a global low-freq tick 
                    # and individual probability, OR we just sleep random times per guild.
                    # Since this is a single loop, we'll randomize a sleep.
                    
                    # Logic: We actually want to spawn rarely. 
                    # Let's verify if we should spawn.
                    # (In a production env, I'd use specific timestamps, but here we roll dice).
                    
                    if random.randint(1, 20) == 1: # ~5% chance per minute check
                        await self.spawn_creature(guild_id, data)

                await asyncio.sleep(60) 
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Error in spawn loop", exc_info=e)
                await asyncio.sleep(60)

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
        
        # Post the cry
        try:
            embed = discord.Embed(
                title=f"{creature['emoji']} A wild {creature['name']} appears!",
                description=f"**{creature['cry']}**",
                color=discord.Color.green() if creature['type'] == 'normal' else discord.Color.red()
            )
            await channel.send(embed=embed)
            self.active_creatures[guild_id] = creature
        except discord.Forbidden:
            log.warning(f"Missing permissions to send to {channel.id} in {guild.name}")

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
                    creature = self.active_creatures.pop(guild_id)
                    await self.process_bang(message, creature, conf)

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

        # Illegal Logic: You ALWAYS hit illegal targets (penalty) or normal hit logic?
        # Standard trope: You accidentally shot the civilian.
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
            
            embed = discord.Embed(
                title="🎯 BANG!",
                description=f"{user.mention} successfully hunted the **{creature['name']}**!\n\n_{creature['message']}_\n\n**+ {points} points**",
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
        await self.config.guild(ctx.guild).enabled.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(f"Bang game is now **{state}**.")

    @bangset.group(name="creature")
    @checks.admin_or_permissions(manage_guild=True)
    async def bangset_creature(self, ctx):
        """
        Manage creatures.
        """
        pass

    @bangset_creature.command(name="add")
    async def creature_add(self, ctx, name: str, type: Literal["normal", "illegal"], emoji: str, cry: str, *, message: str):
        """
        Add a creature.
        
        Usage: [p]bangset creature add <name> <type> <emoji> <cry> <message>
        Type must be 'normal' or 'illegal'.
        """
        async with self.config.guild(ctx.guild).creatures() as creatures:
            creatures[name.lower()] = {
                "name": name,
                "type": type,
                "emoji": emoji,
                "cry": cry,
                "message": message,
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
        
        embed = discord.Embed(title="Bang! Settings", color=discord.Color.blue())
        embed.add_field(name="Status", value="Enabled" if conf['enabled'] else "Disabled")
        embed.add_field(name="Channel", value=getattr(channel, 'mention', channel))
        embed.add_field(name="Keyword", value=conf['keyword'])
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