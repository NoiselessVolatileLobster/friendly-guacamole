import discord
import asyncio
import datetime
import random
from typing import Optional, Union, Dict, List

from redbot.core import commands, Config, bank, checks
from redbot.core.utils.chat_formatting import humanize_list, box
from discord.ui import View, Button

# Import the image generator
from .drawer import generate_holiday_image

class HolidayButton(discord.ui.Button):
    def __init__(self, cog):
        super().__init__(style=discord.ButtonStyle.green, label="🎁 Open", custom_id="holiday_open_button")
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.process_open(interaction)

class HolidayGifts(commands.Cog):
    """
    A daily Holiday Gift system with rewards, image generation, and timezone support.
    """

    def __init__(self, bot):
        self.bot = bot
        # Identifier changed slightly to ensure fresh config for the rename
        self.config = Config.get_conf(self, identifier=9812374124, force_registration=True)
        
        default_guild = {
            "channel_id": None,
            "role_id": None, # If set, limits who can use it (optional)
            "rewards": {}, # "1": {"credits": 100, "xp": 50, "role": 123, "temp_role": 123, "temp_timer": 3600}
            "testing_mode": False,
            "testing_start": 0, # Timestamp
            "stats": {
                "opened_total": 0,
                "users_completed": 0,
                "total_xp": 0,
                "total_credits": 0,
                "daily_opens": {} # "1": 50
            },
            "temp_roles": {} # user_id-role_id : timestamp_end
        }
        
        default_user = {
            "opened_days": [],
            "year_record": 0 # To reset logic annually
        }

        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        
        self.bg_loop = self.bot.loop.create_task(self.check_temp_roles())

    def cog_unload(self):
        if self.bg_loop:
            self.bg_loop.cancel()

    async def check_temp_roles(self):
        """Background loop to remove temporary roles."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Iterate all guilds
                all_guilds = await self.config.all_guilds()
                now = datetime.datetime.now(datetime.timezone.utc).timestamp()
                
                for guild_id, data in all_guilds.items():
                    temp_roles = data.get("temp_roles", {}).copy()
                    changed = False
                    
                    for key, end_time in temp_roles.items():
                        if now > end_time:
                            # Expired
                            user_id, role_id = map(int, key.split("-"))
                            guild = self.bot.get_guild(guild_id)
                            if guild:
                                member = guild.get_member(user_id)
                                role = guild.get_role(role_id)
                                if member and role:
                                    try:
                                        await member.remove_roles(role, reason="Holiday Gifts temp role expired")
                                    except discord.HTTPException:
                                        pass
                            del data["temp_roles"][key]
                            changed = True
                    
                    if changed:
                        await self.config.guild_from_id(guild_id).temp_roles.set(data["temp_roles"])
                        
            except Exception as e:
                print(f"Error in Holiday Gifts temp role loop: {e}")
            
            await asyncio.sleep(60)

    # -------------------------------------------------------------------------
    # DATE & TIME LOGIC
    # -------------------------------------------------------------------------

    async def get_user_time(self, user: discord.Member) -> datetime.datetime:
        """
        Get user's local time via Timezone cog or default to UTC.
        """
        tz_cog = self.bot.get_cog("Timezone")
        if tz_cog:
            try:
                # Assuming standard v3 Timezone cog method
                tz = await tz_cog.timezone_for_user(user)
                if tz:
                    # tz is usually a pytz object or timezone info
                    return datetime.datetime.now(tz)
            except AttributeError:
                pass # Method might differ slightly depending on version
            
        return datetime.datetime.now(datetime.timezone.utc)

    async def get_holiday_day(self, guild: discord.Guild) -> Optional[int]:
        """
        Returns the current Holiday Day (1-25) or None if inactive.
        """
        testing = await self.config.guild(guild).testing_mode()
        
        if testing:
            start_ts = await self.config.guild(guild).testing_start()
            if start_ts == 0:
                return None
            start_date = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            diff = (now - start_date).days + 1
            if 1 <= diff <= 25:
                return diff
            return None
        else:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now.month == 12 and 1 <= now.day <= 25:
                return now.day
            return None

    # -------------------------------------------------------------------------
    # INTERACTION HANDLER
    # -------------------------------------------------------------------------

    async def process_open(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        
        # 1. Check Date Availability
        holiday_day = await self.get_holiday_day(guild)
        if not holiday_day:
            return await interaction.response.send_message("The Holiday Gifts event is locked right now.", ephemeral=True)

        # 2. Check Time Logic (4 AM local)
        user_time = await self.get_user_time(user)
        if user_time.hour < 4:
            # Calculate time until 4 AM
            return await interaction.response.send_message(f"You can't open today's gift yet! Wait until 4:00 AM your local time ({user_time.strftime('%H:%M')}).", ephemeral=True)

        # 3. Data Consistency Check
        user_data = await self.config.user(user).all()
        current_year = datetime.datetime.now().year
        
        if user_data['year_record'] != current_year:
            # Reset for new year
            user_data['opened_days'] = []
            user_data['year_record'] = current_year
            await self.config.user(user).set(user_data)

        # 4. Check if already opened today
        if holiday_day in user_data['opened_days']:
            # Generate image anyway so they can see their progress
            img_file = await generate_holiday_image(self.bot, user_data['opened_days'], holiday_day)
            return await interaction.response.send_message("You have already opened today's gift! Here is your calendar:", file=img_file, ephemeral=True)

        # 5. SPECIAL: Day 25 Logic
        if holiday_day == 25:
            # Must have opened 1-24
            needed = set(range(1, 25))
            opened = set(user_data['opened_days'])
            if not needed.issubset(opened):
                img_file = await generate_holiday_image(self.bot, user_data['opened_days'], holiday_day)
                return await interaction.response.send_message("Day 25 is locked! You needed to open all previous 24 gifts to claim the grand prize.", file=img_file, ephemeral=True)

        # 6. Grant Rewards
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        rewards_config = await self.config.guild(guild).rewards()
        day_rewards = rewards_config.get(str(holiday_day), {})
        
        reward_text = []
        
        # Bank
        credits_amt = day_rewards.get("credits", 0)
        if credits_amt > 0:
            try:
                await bank.deposit_credits(user, credits_amt)
                currency = await bank.get_currency_name(guild)
                reward_text.append(f"• {credits_amt} {currency}")
            except Exception:
                pass

        # XP / Levels (LevelUp Integration)
        xp_amt = day_rewards.get("xp", 0)
        levels_amt = day_rewards.get("levels", 0)
        
        if xp_amt > 0 or levels_amt > 0:
            lvl_cog = self.bot.get_cog("LevelUp")
            if lvl_cog:
                try:
                    # Attempting to use shared method if available, else manual config edit
                    if hasattr(lvl_cog, "add_xp"):
                        await lvl_cog.add_xp(user.id, guild.id, xp_amt)
                        if xp_amt > 0: reward_text.append(f"• {xp_amt} XP")
                    if levels_amt > 0:
                         # Placeholder for level logic
                         pass 
                except Exception:
                    pass

        # Roles
        role_id = day_rewards.get("role_id")
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await user.add_roles(role, reason=f"Holiday Gifts Day {holiday_day}")
                    reward_text.append(f"• Role: {role.name}")
                except discord.Forbidden:
                    reward_text.append(f"• (Failed to add role {role.name} - Permission Error)")

        # Temp Roles
        temp_role_id = day_rewards.get("temp_role_id")
        temp_time = day_rewards.get("temp_timer", 0) # Seconds
        if temp_role_id and temp_time > 0:
            role = guild.get_role(temp_role_id)
            if role:
                try:
                    await user.add_roles(role, reason=f"Holiday Temp Day {holiday_day}")
                    expiry = datetime.datetime.now(datetime.timezone.utc).timestamp() + temp_time
                    async with self.config.guild(guild).temp_roles() as tr:
                        tr[f"{user.id}-{role.id}"] = expiry
                    reward_text.append(f"• Temp Role: {role.name} ({int(temp_time/3600)}h)")
                except discord.Forbidden:
                    pass

        # 7. Update Stats & User Data
        async with self.config.user(user).opened_days() as days:
            days.append(holiday_day)
            
        async with self.config.guild(guild).stats() as stats:
            stats["opened_total"] += 1
            stats["total_credits"] += credits_amt
            stats["total_xp"] += xp_amt
            
            day_str = str(holiday_day)
            if day_str not in stats["daily_opens"]:
                stats["daily_opens"][day_str] = 0
            stats["daily_opens"][day_str] += 1
            
            if holiday_day == 25:
                stats["users_completed"] += 1

        # 8. Send Image and Message
        img_file = await generate_holiday_image(self.bot, user_data['opened_days'] + [holiday_day], holiday_day)
        
        msg = f"**Day {holiday_day} Opened!** 🎄\n"
        if reward_text:
            msg += "You received:\n" + "\n".join(reward_text)
        else:
            msg += "You received a warm holiday feeling! (No rewards configured)."
            
        await interaction.followup.send(msg, file=img_file, ephemeral=True)

    # -------------------------------------------------------------------------
    # COMMANDS
    # -------------------------------------------------------------------------

    @commands.group(name="holidaygiftsset", aliases=["holidayset"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def hg_set(self, ctx):
        """Configuration for the Holiday Gifts system."""
        pass

    @hg_set.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where the daily embed will be posted."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Holiday Gifts channel set to {channel.mention}")

    @hg_set.command(name="post")
    async def manual_post(self, ctx):
        """Manually post the Holiday Gifts embed with the button."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            return await ctx.send("Please set a channel first.")
        
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("Channel not found.")
            
        embed = discord.Embed(
            title="🎄 Holiday Gifts",
            description="Press the button below to check your holiday gift calendar and claim today's reward!",
            color=discord.Color.green()
        )
        embed.set_footer(text="Open daily for a grand prize on the 25th!")
        
        view = View(timeout=None)
        view.add_item(HolidayButton(self))
        
        await channel.send(embed=embed, view=view)
        await ctx.tick()

    @hg_set.command(name="testmode")
    async def set_testmode(self, ctx, active: bool):
        """Turn testing mode on or off. If on, starts 'Day 1' now."""
        await self.config.guild(ctx.guild).testing_mode.set(active)
        if active:
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            await self.config.guild(ctx.guild).testing_start.set(now_ts)
            await ctx.send("Testing mode ENABLED. Day 1 is today.")
        else:
            await ctx.send("Testing mode DISABLED. Calendar follows Dec 1-25.")

    @hg_set.group(name="rewards")
    async def set_rewards(self, ctx):
        """Configure rewards for specific days."""
        pass

    @set_rewards.command(name="add")
    async def add_reward(self, ctx, day: int, type: str, value: int):
        """
        Add simple rewards (credits, xp).
        Usage: [p]holidayset rewards add <day> <credits|xp> <amount>
        """
        if not 1 <= day <= 25:
            return await ctx.send("Day must be 1-25.")
        
        if type.lower() not in ["credits", "xp"]:
            return await ctx.send("Type must be 'credits' or 'xp'.")
            
        async with self.config.guild(ctx.guild).rewards() as r:
            if str(day) not in r: r[str(day)] = {}
            r[str(day)][type.lower()] = value
            
        await ctx.send(f"Day {day} reward updated: {value} {type}.")

    @set_rewards.command(name="role")
    async def add_role_reward(self, ctx, day: int, role: discord.Role):
        """Set a permanent role reward for a day."""
        async with self.config.guild(ctx.guild).rewards() as r:
            if str(day) not in r: r[str(day)] = {}
            r[str(day)]["role_id"] = role.id
        await ctx.send(f"Day {day} will give role: {role.name}")

    @hg_set.command(name="budget")
    async def auto_budget(self, ctx, max_credits: int, max_xp: int):
        """
        Automatically distribute a budget of credits and XP across Days 1-24.
        Day 25 is left untouched (manual config required for grand prize).
        """
        if max_credits < 24 and max_xp < 24:
            return await ctx.send("Budget too small to distribute.")

        # Algorithm: Generate 24 random weights, normalize, multiply by total
        weights = [random.random() for _ in range(24)]
        sum_weights = sum(weights)
        norm_weights = [w / sum_weights for w in weights]
        
        credit_dist = [int(w * max_credits) for w in norm_weights]
        xp_dist = [int(w * max_xp) for w in norm_weights]
        
        async with self.config.guild(ctx.guild).rewards() as r:
            for i in range(24):
                day = str(i + 1)
                if day not in r: r[day] = {}
                
                if max_credits > 0: r[day]["credits"] = credit_dist[i]
                if max_xp > 0: r[day]["xp"] = xp_dist[i]
                
        await ctx.send(f"Distributed {sum(credit_dist)} credits and {sum(xp_dist)} XP across days 1-24.")

    @hg_set.command(name="view")
    async def view_settings(self, ctx):
        """View all current settings."""
        data = await self.config.guild(ctx.guild).all()
        
        channel_mention = f"<#{data['channel_id']}>" if data['channel_id'] else "Not Set"
        
        desc = (
            f"**Channel:** {channel_mention}\n"
            f"**Test Mode:** {data['testing_mode']}\n"
            f"**Total Opens:** {data['stats']['opened_total']}\n"
            f"**XP Given:** {data['stats']['total_xp']}\n"
            f"**Credits Given:** {data['stats']['total_credits']}\n\n"
            "**Rewards Configured:**\n"
        )
        
        rewards_list = []
        for day, rew in sorted(data['rewards'].items(), key=lambda x: int(x[0])):
            rew_str = []
            if "credits" in rew: rew_str.append(f"{rew['credits']} Credits")
            if "xp" in rew: rew_str.append(f"{rew['xp']} XP")
            if "role_id" in rew: rew_str.append(f"RoleID: {rew['role_id']}")
            rewards_list.append(f"**Day {day}:** {', '.join(rew_str)}")
            
        if not rewards_list:
            rewards_list = ["None"]
            
        # Chunking if too long
        msg = desc + "\n".join(rewards_list[:10])
        if len(rewards_list) > 10:
            msg += "\n... (more)"
            
        await ctx.send(embed=discord.Embed(title="Holiday Gifts Settings", description=msg, color=discord.Color.blue()))