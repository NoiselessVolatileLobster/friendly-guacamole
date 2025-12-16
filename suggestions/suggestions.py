import discord
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import box, humanize_list
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate
import asyncio
import datetime
from collections import defaultdict, Counter

class SuggestionModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Make a Suggestion")
        self.cog = cog

        self.short_title = discord.ui.TextInput(
            label="Short Title",
            placeholder="e.g. Add Emotes (Max 20 chars)",
            max_length=20,
            style=discord.TextStyle.short,
            required=True
        )
        self.suggestion_text = discord.ui.TextInput(
            label="Suggestion Details",
            placeholder="Describe your suggestion in detail...",
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.add_item(self.short_title)
        self.add_item(self.suggestion_text)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.process_suggestion(
            interaction, 
            interaction.channel_id, 
            self.short_title.value, 
            self.suggestion_text.value
        )

class EntryView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="📩 Make a suggestion", style=discord.ButtonStyle.primary, custom_id="suggestions:create_btn")
    async def create_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Security: Ensure this is actually the suggestions channel
        conf_channel = await self.cog.config.guild(interaction.guild).channel_id()
        if interaction.channel_id != conf_channel:
            await interaction.response.send_message("This button is not active in this channel.", ephemeral=True)
            return

        # Level Check
        guild = interaction.guild
        user = interaction.user
        req_level = await self.cog.config.guild(guild).req_level_create()
        
        if req_level > 0:
            user_level = await self.cog.get_user_level(user)
            if user_level < req_level:
                await interaction.response.send_message(
                    f"You need to be Level {req_level} to make a suggestion. (Current: {user_level})", 
                    ephemeral=True
                )
                return

        await interaction.response.send_modal(SuggestionModal(self.cog))

class VoteView(discord.ui.View):
    def __init__(self, cog, suggestion_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.suggestion_id = str(suggestion_id)

    async def handle_vote(self, interaction: discord.Interaction, vote_type: str):
        guild = interaction.guild
        user = interaction.user
        
        # Level Check
        req_level = await self.cog.config.guild(guild).req_level_vote()
        if req_level > 0:
            user_level = await self.cog.get_user_level(user)
            if user_level < req_level:
                await interaction.response.send_message(
                    f"You need to be Level {req_level} to vote. (Current: {user_level})", 
                    ephemeral=True
                )
                return

        async with self.cog.config.guild(guild).suggestions() as suggestions:
            if self.suggestion_id not in suggestions:
                await interaction.response.send_message("This suggestion no longer exists.", ephemeral=True)
                return
            
            data = suggestions[self.suggestion_id]
            if data['status'] != 'open':
                await interaction.response.send_message("Voting is closed for this suggestion.", ephemeral=True)
                return

            uid = user.id
            ups = set(data['upvotes'])
            downs = set(data['downvotes'])
            
            message = "Vote recorded."
            
            if vote_type == "up":
                if uid in ups:
                    ups.remove(uid)
                    message = "Upvote removed."
                else:
                    ups.add(uid)
                    if uid in downs: downs.remove(uid)
                    message = "Upvoted!"
            elif vote_type == "down":
                if uid in downs:
                    downs.remove(uid)
                    message = "Downvote removed."
                else:
                    downs.add(uid)
                    if uid in ups: ups.remove(uid)
                    message = "Downvoted."

            data['upvotes'] = list(ups)
            data['downvotes'] = list(downs)
            suggestions[self.suggestion_id] = data
            
            # Update embed
            await self.cog.update_suggestion_message(guild, data)
            await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="suggestion:vote:up")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_vote(interaction, "up")

    @discord.ui.button(style=discord.ButtonStyle.danger, custom_id="suggestion:vote:down")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_vote(interaction, "down")

class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "channel_id": None,
            "req_level_create": 0,
            "req_level_vote": 0,
            "next_id": 1,
            "emoji_up": "👍",
            "emoji_down": "👎",
            "suggestions": {} # ID -> Dict
        }
        self.config.register_guild(**default_guild)
        
        # Initialize Persistent View
        self.entry_view = EntryView(self)
        self.bot.add_view(self.entry_view)

    def cog_unload(self):
        self.entry_view.stop()

    async def get_user_level(self, member: discord.Member) -> int:
        """Integrates with LevelUp cog to get user level."""
        cog = self.bot.get_cog("LevelUp")
        if not cog:
            return 0
        try:
            # FIX: Added 'await' here because get_level is async
            return await cog.get_level(member)
        except AttributeError:
            return 0
        except TypeError:
            # Fallback in case it's not awaitable in some version (unlikely based on error)
            return 0

    async def update_suggestion_message(self, guild, data):
        channel_id = await self.config.guild(guild).channel_id()
        channel = guild.get_channel(channel_id)
        if not channel: return

        try:
            thread = guild.get_thread(data['thread_id'])
            if not thread:
                thread = await guild.fetch_channel(data['thread_id'])
        except:
            return

        try:
            message = await thread.fetch_message(data['message_id'])
        except:
            return

        emoji_up = await self.config.guild(guild).emoji_up()
        emoji_down = await self.config.guild(guild).emoji_down()
        
        up_count = len(data['upvotes'])
        down_count = len(data['downvotes'])

        embed = message.embeds[0]
        
        view = VoteView(self, data['id'])
        view.children[0].label = str(up_count)
        view.children[0].emoji = emoji_up
        view.children[1].label = str(down_count)
        view.children[1].emoji = emoji_down
        
        if data['status'] != 'open':
            for child in view.children:
                child.disabled = True

        await message.edit(embed=embed, view=view)

    async def process_suggestion(self, interaction, channel_id, title, text):
        guild = interaction.guild
        async with self.config.guild(guild).next_id() as nid:
            s_id = nid
            await self.config.guild(guild).next_id.set(nid + 1)
        
        emoji_up = await self.config.guild(guild).emoji_up()
        emoji_down = await self.config.guild(guild).emoji_down()

        embed = discord.Embed(
            title=f"#{s_id} - {title}",
            description=text,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"ID: {s_id}")

        channel = guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("Configuration error: Channel not found.", ephemeral=True)
            return

        thread_name = f"{s_id} - {title}"
        thread = await channel.create_thread(name=thread_name, type=discord.ChannelType.public_thread)
        
        view = VoteView(self, s_id)
        view.children[0].label = "0"
        view.children[0].emoji = emoji_up
        view.children[1].label = "0"
        view.children[1].emoji = emoji_down

        msg = await thread.send(embed=embed, view=view)
        
        s_data = {
            "id": s_id,
            "author_id": interaction.user.id,
            "title": title,
            "content": text,
            "timestamp": datetime.datetime.now().timestamp(),
            "thread_id": thread.id,
            "message_id": msg.id,
            "status": "open",
            "upvotes": [],
            "downvotes": []
        }
        
        async with self.config.guild(guild).suggestions() as s:
            s[str(s_id)] = s_data

        try:
            await interaction.user.send(
                f"Your suggestion has been created and is open for voting!\n"
                f"Link: {thread.jump_url}"
            )
        except:
            pass 
        
        await interaction.response.send_message(f"Suggestion created in {thread.mention}", ephemeral=True)

    @commands.group(name="suggestionsset", aliases=["suggestion set"])
    @commands.admin_or_permissions(administrator=True)
    async def suggestionsset(self, ctx):
        """Configure the Suggestions system."""
        pass

    @suggestionsset.command(name="channel")
    async def ss_channel(self, ctx, channel: discord.TextChannel):
        """Set the suggestions channel and post the menu."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        embed = discord.Embed(
            title="Suggestions",
            description="Have an idea for the server? Click the button below to submit a suggestion!\n\n"
                        "Please keep titles short and provide details in the description.",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=self.entry_view)
        await ctx.tick()

    @suggestionsset.command(name="levelcreate")
    async def ss_levelcreate(self, ctx, level: int):
        """Set required LevelUp level to create suggestions."""
        await self.config.guild(ctx.guild).req_level_create.set(level)
        await ctx.tick()

    @suggestionsset.command(name="levelvote")
    async def ss_levelvote(self, ctx, level: int):
        """Set required LevelUp level to vote."""
        await self.config.guild(ctx.guild).req_level_vote.set(level)
        await ctx.tick()

    @suggestionsset.command(name="emojis")
    async def ss_emojis(self, ctx, upvote: str, downvote: str):
        """Set the upvote and downvote emojis."""
        await self.config.guild(ctx.guild).emoji_up.set(upvote)
        await self.config.guild(ctx.guild).emoji_down.set(downvote)
        await ctx.tick()

    @suggestionsset.command(name="view")
    async def ss_view(self, ctx):
        """View current configuration."""
        cfg = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(cfg['channel_id'])
        ch_name = channel.mention if channel else "Not Set"
        
        msg = f"""
**Suggestions Configuration**
---------------------------
Channel:        {ch_name}
Create Level:   {cfg['req_level_create']}
Vote Level:     {cfg['req_level_vote']}
Up Emoji:       {cfg['emoji_up']}
Down Emoji:     {cfg['emoji_down']}
Current ID:     {cfg['next_id']}
        """
        await ctx.send(msg)

    @suggestionsset.command(name="approve")
    async def ss_approve(self, ctx, suggestion_id: str, *, message: str):
        """Approve a suggestion."""
        async with self.config.guild(ctx.guild).suggestions() as suggestions:
            if suggestion_id not in suggestions:
                return await ctx.send("Suggestion ID not found.")
            
            data = suggestions[suggestion_id]
            data['status'] = 'approved'
            suggestions[suggestion_id] = data
            
            thread = ctx.guild.get_thread(data['thread_id'])
            if thread:
                embed = discord.Embed(title=f"Suggestion #{suggestion_id} Approved", description=message, color=discord.Color.green())
                await thread.send(embed=embed)
                await thread.edit(locked=True, archived=True)
                await self.update_suggestion_message(ctx.guild, data)
                await ctx.tick()
            else:
                await ctx.send("Thread not found, status updated in DB.")

    @suggestionsset.command(name="reject")
    async def ss_reject(self, ctx, suggestion_id: str, *, message: str):
        """Reject a suggestion."""
        async with self.config.guild(ctx.guild).suggestions() as suggestions:
            if suggestion_id not in suggestions:
                return await ctx.send("Suggestion ID not found.")
            
            data = suggestions[suggestion_id]
            data['status'] = 'rejected'
            suggestions[suggestion_id] = data
            
            thread = ctx.guild.get_thread(data['thread_id'])
            if thread:
                embed = discord.Embed(title=f"Suggestion #{suggestion_id} Rejected", description=message, color=discord.Color.red())
                await thread.send(embed=embed)
                await thread.edit(locked=True, archived=True)
                await self.update_suggestion_message(ctx.guild, data)
                await ctx.tick()
            else:
                await ctx.send("Thread not found, status updated in DB.")

    @suggestionsset.command(name="resetstats")
    async def ss_resetstats(self, ctx):
        """Reset all suggestion statistics (Wipes all suggestions)."""
        msg = await ctx.send("Are you sure you want to wipe all suggestions and statistics? Type `yes` to confirm.")
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out.")
        
        if pred.result:
            await self.config.guild(ctx.guild).suggestions.set({})
            await self.config.guild(ctx.guild).next_id.set(1)
            await ctx.send("All suggestions and stats reset.")
        else:
            await ctx.send("Cancelled.")

    @suggestionsset.command(name="stats")
    async def ss_stats(self, ctx):
        """View detailed suggestion statistics."""
        data = await self.config.guild(ctx.guild).suggestions()
        all_sugs = data.values()
        if not all_sugs:
            return await ctx.send("No data available.")

        user_sugs = Counter()
        user_approved = Counter()
        user_rejected = Counter()
        user_upvotes_given = Counter()
        user_downvotes_given = Counter()
        user_vote_net = defaultdict(int)

        for s in all_sugs:
            auth = s['author_id']
            user_sugs[auth] += 1
            
            if s['status'] == 'approved': user_approved[auth] += 1
            if s['status'] == 'rejected': user_rejected[auth] += 1
            
            for u in s['upvotes']: user_upvotes_given[u] += 1
            for u in s['downvotes']: user_downvotes_given[u] += 1
            
            score = len(s['upvotes']) - len(s['downvotes'])
            user_vote_net[auth] += score

        unique_users = len(user_sugs)
        total_sugs = len(all_sugs)
        avg_sug = total_sugs / unique_users if unique_users > 0 else 0

        def get_top(counter, n=5):
            return counter.most_common(n)

        def get_ratio_extremes():
            ratios = {}
            for u, total in user_sugs.items():
                if total < 2: continue
                app = user_approved[u]
                ratios[u] = (app / total) * 100
            
            if not ratios: return None, None
            highest = max(ratios.items(), key=lambda x: x[1])
            lowest = min(ratios.items(), key=lambda x: x[1])
            return highest, lowest

        top_makers = get_top(user_sugs)
        top_success = get_top(user_approved)
        top_fail = get_top(user_rejected)
        top_optimist = get_top(user_upvotes_given)
        top_pessimist = get_top(user_downvotes_given)
        
        sorted_net = sorted(user_vote_net.items(), key=lambda x: x[1], reverse=True)
        top_net = sorted_net[:5]
        low_net = sorted_net[-5:]

        high_ratio, low_ratio = get_ratio_extremes()

        def format_list(lst, suffix=""):
            if not lst: return "None"
            return "\n".join([f"<@{x[0]}>: {x[1]:.2f}{suffix}" if isinstance(x[1], float) else f"<@{x[0]}>: {x[1]}{suffix}" for x in lst])

        embed = discord.Embed(title="Suggestion Statistics", color=discord.Color.purple())
        embed.add_field(name="General", value=f"Total Suggestions: {total_sugs}\nUnique Authors: {unique_users}\nAvg per User: {avg_sug:.2f}", inline=False)
        
        embed.add_field(name="Most Suggestions", value=format_list(top_makers), inline=True)
        embed.add_field(name="Most Approved (Successful)", value=format_list(top_success), inline=True)
        embed.add_field(name="Most Rejected (Unsuccessful)", value=format_list(top_fail), inline=True)
        
        if high_ratio:
            embed.add_field(name="Highest Approval Ratio (Min 2)", value=f"<@{high_ratio[0]}>: {high_ratio[1]:.1f}%", inline=True)
            embed.add_field(name="Lowest Approval Ratio (Min 2)", value=f"<@{low_ratio[0]}>: {low_ratio[1]:.1f}%", inline=True)
        else:
            embed.add_field(name="Ratios", value="Not enough data", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(name="Most Optimistic (Upvotes Given)", value=format_list(top_optimist), inline=True)
        embed.add_field(name="Most Pessimistic (Downvotes Given)", value=format_list(top_pessimist), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(name="Highest Vote Score (Received)", value=format_list(top_net), inline=True)
        embed.add_field(name="Lowest Vote Score (Received)", value=format_list(low_net), inline=True)
        
        await ctx.send(embed=embed)

    @suggestionsset.command(name="dashboard")
    async def ss_dashboard(self, ctx):
        """View suggestion dashboard."""
        suggestions = await self.config.guild(ctx.guild).suggestions()
        if not suggestions:
            return await ctx.send("No suggestions found.")
            
        open_list = [v for k,v in suggestions.items() if v['status'] == 'open']
        approved_list = sorted([v for k,v in suggestions.items() if v['status'] == 'approved'], key=lambda x: x['timestamp'], reverse=True)[:5]
        rejected_list = sorted([v for k,v in suggestions.items() if v['status'] == 'rejected'], key=lambda x: x['timestamp'], reverse=True)[:5]

        embed = discord.Embed(title="Suggestions Dashboard", color=discord.Color.gold())
        
        open_str = "\n".join([f"#{x['id']} {x['title']} (<t:{int(x['timestamp'])}:R>)" for x in open_list]) or "None"
        embed.add_field(name=f"Open ({len(open_list)})", value=open_str, inline=False)
        
        app_str = "\n".join([f"#{x['id']} {x['title']}" for x in approved_list]) or "None"
        embed.add_field(name="Recently Approved", value=app_str, inline=False)
        
        rej_str = "\n".join([f"#{x['id']} {x['title']}" for x in rejected_list]) or "None"
        embed.add_field(name="Recently Rejected", value=rej_str, inline=False)
        
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            cid = interaction.data.get("custom_id", "")
            
            if cid.startswith("suggestion:vote:"):
                try:
                    s_type = cid.split(":")[-1] # up or down
                    msg_id = interaction.message.id
                    
                    target_s = None
                    async with self.config.guild(interaction.guild).suggestions() as s:
                        for sid, data in s.items():
                            if data['message_id'] == msg_id:
                                target_s = sid
                                break
                    
                    if target_s:
                        view = VoteView(self, target_s)
                        await view.handle_vote(interaction, s_type)
                except:
                    pass