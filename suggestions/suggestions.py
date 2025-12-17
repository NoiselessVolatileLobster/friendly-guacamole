import discord
from redbot.core import commands, Config, bank
from redbot.core.utils.chat_formatting import box, humanize_list
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
        conf_channel = await self.cog.config.guild(interaction.guild).channel_id()
        if interaction.channel_id != conf_channel:
            await interaction.response.send_message("This button is not active in this channel.", ephemeral=True)
            return

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
    """
    A purely visual view. Logic is handled by the Cog's on_interaction listener.
    """
    def __init__(self, suggestion_id, up_label, down_label, up_emoji, down_emoji, disabled=False):
        super().__init__(timeout=None)
        
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.success,
            label=str(up_label),
            emoji=up_emoji,
            custom_id=f"suggestion:vote:up:{suggestion_id}",
            disabled=disabled
        ))
        
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label=str(down_label),
            emoji=down_emoji,
            custom_id=f"suggestion:vote:down:{suggestion_id}",
            disabled=disabled
        ))

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
            "suggestions": {},
            # Economy Settings
            "credits_create": 0,
            "credits_approve": 0,
            "credits_vote": 0,
            "credits_thread": 0,
            "thread_min_msgs": 5
        }
        self.config.register_guild(**default_guild)
        
        self.entry_view = EntryView(self)
        self.bot.add_view(self.entry_view)

    def cog_unload(self):
        self.entry_view.stop()

    async def get_user_level(self, member: discord.Member) -> int:
        cog = self.bot.get_cog("LevelUp")
        if not cog:
            return 0
        try:
            return await cog.get_level(member)
        except AttributeError:
            return 0
        except TypeError:
            return 0

    async def update_suggestion_message(self, guild, data):
        channel_id = await self.config.guild(guild).channel_id()
        channel = guild.get_channel(channel_id)
        if not channel: return

        try:
            message = await channel.fetch_message(data['message_id'])
        except:
            return

        emoji_up = await self.config.guild(guild).emoji_up()
        emoji_down = await self.config.guild(guild).emoji_down()
        
        up_count = len(data['upvotes'])
        down_count = len(data['downvotes'])
        status = data['status']
        is_closed = status != 'open'
        reason = data.get('reason', '')

        view = VoteView(
            suggestion_id=data['id'],
            up_label=up_count,
            down_label=down_count,
            up_emoji=emoji_up,
            down_emoji=emoji_down,
            disabled=is_closed
        )

        embed = message.embeds[0]
        
        clean_title = embed.title.replace("[APPROVED] ", "").replace("[REJECTED] ", "")
        
        if status == 'approved':
            embed.color = discord.Color.green()
            embed.title = f"[APPROVED] {clean_title}"
        elif status == 'rejected':
            embed.color = discord.Color.red()
            embed.title = f"[REJECTED] {clean_title}"
        else:
            embed.color = discord.Color.blue()
            embed.title = clean_title

        new_desc = data['content']
        if is_closed and reason:
            new_desc += f"\n\n**Reason:** {reason}"
        
        embed.description = new_desc

        await message.edit(embed=embed, view=view)

    async def process_suggestion(self, interaction, channel_id, title, text):
        guild = interaction.guild
        
        nid = await self.config.guild(guild).next_id()
        s_id = nid
        await self.config.guild(guild).next_id.set(nid + 1)
        
        emoji_up = await self.config.guild(guild).emoji_up()
        emoji_down = await self.config.guild(guild).emoji_down()

        embed = discord.Embed(
            title=f"Suggestion {s_id} - {title}",
            description=text,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(text="Check the pinned message to make a suggestion.")

        channel = guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("Configuration error: Channel not found.", ephemeral=True)
            return

        view = VoteView(s_id, 0, 0, emoji_up, emoji_down)
        msg = await channel.send(embed=embed, view=view)

        thread_name = f"{s_id} - {title}"
        thread = await msg.create_thread(name=thread_name)

        thread_content = f"## Suggestion {s_id} - {title}\n{text}"
        await thread.send(content=thread_content)
        
        s_data = {
            "id": s_id,
            "author_id": interaction.user.id,
            "title": title,
            "content": text,
            "timestamp": datetime.datetime.now().timestamp(),
            "thread_id": thread.id,
            "message_id": msg.id, 
            "status": "open",
            "reason": "",
            "upvotes": [],
            "downvotes": []
        }
        
        async with self.config.guild(guild).suggestions() as s:
            s[str(s_id)] = s_data

        # --- ECONOMY: Create Reward ---
        create_amt = await self.config.guild(guild).credits_create()
        reward_msg = ""
        if create_amt > 0:
            try:
                await bank.deposit_credits(interaction.user, create_amt)
                currency = await bank.get_currency_name(guild)
                reward_msg = f"\n\n💰 **Reward:** You received {create_amt} {currency} for submitting a suggestion!"
            except:
                pass # Fail silently if bank errors

        try:
            await interaction.user.send(
                f"Your suggestion has been created and is open for voting!{reward_msg}\n"
                f"Link: {thread.jump_url}"
            )
        except:
            pass 
        
        await interaction.response.send_message(f"Suggestion created in {thread.mention}", ephemeral=True)

    async def distribute_approval_rewards(self, guild, data, thread):
        """Handles distributing credits for Approval, Voting, and Thread Participation."""
        currency = await bank.get_currency_name(guild)
        logs = []

        # 1. Author Reward
        author_amt = await self.config.guild(guild).credits_approve()
        if author_amt > 0:
            author = guild.get_member(data['author_id'])
            if author:
                try:
                    await bank.deposit_credits(author, author_amt)
                    logs.append(f"Author {author.mention}: +{author_amt} {currency}")
                except: pass

        # 2. Voter Reward (Union of up and down votes)
        vote_amt = await self.config.guild(guild).credits_vote()
        if vote_amt > 0:
            voters = set(data['upvotes']) | set(data['downvotes'])
            for user_id in voters:
                # Optional: prevent author from getting voter reward on own suggestion? 
                # Keeping simple: if they voted, they get paid.
                member = guild.get_member(user_id)
                if member:
                    try:
                        await bank.deposit_credits(member, vote_amt)
                    except: pass
            if voters:
                logs.append(f"{len(voters)} Voters: +{vote_amt} {currency} each")

        # 3. Thread Participation Reward
        thread_amt = await self.config.guild(guild).credits_thread()
        min_msgs = await self.config.guild(guild).thread_min_msgs()
        
        if thread_amt > 0 and thread:
            # Count messages
            # Note: Fetching history can be slow on massive threads, but suggestion threads are usually small.
            # Limiting to 500 to prevent API abuse/timeouts
            counter = Counter()
            try:
                async for message in thread.history(limit=500):
                    if not message.author.bot:
                        counter[message.author.id] += 1
                
                paid_chatters = 0
                for user_id, count in counter.items():
                    if count >= min_msgs:
                        member = guild.get_member(user_id)
                        if member:
                            try:
                                await bank.deposit_credits(member, thread_amt)
                                paid_chatters += 1
                            except: pass
                
                if paid_chatters > 0:
                    logs.append(f"{paid_chatters} Thread Participants: +{thread_amt} {currency} each")
            except:
                logs.append("Failed to process thread history for rewards.")

        return logs

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

    # --- Economy Configuration Commands ---
    @suggestionsset.group(name="credits")
    async def ss_credits(self, ctx):
        """Configure credit rewards for suggestions."""
        pass

    @ss_credits.command(name="create")
    async def ss_cred_create(self, ctx, amount: int):
        """Set credits given for submitting a suggestion."""
        await self.config.guild(ctx.guild).credits_create.set(amount)
        await ctx.send(f"Reward for **creating** a suggestion set to {amount}.")

    @ss_credits.command(name="approve")
    async def ss_cred_approve(self, ctx, amount: int):
        """Set credits given to the author when suggestion is approved."""
        await self.config.guild(ctx.guild).credits_approve.set(amount)
        await ctx.send(f"Reward for **approval** set to {amount}.")

    @ss_credits.command(name="vote")
    async def ss_cred_vote(self, ctx, amount: int):
        """Set credits given to users who voted (on approval)."""
        await self.config.guild(ctx.guild).credits_vote.set(amount)
        await ctx.send(f"Reward for **voting** (distributed on approval) set to {amount}.")

    @ss_credits.command(name="thread")
    async def ss_cred_thread(self, ctx, amount: int, min_messages: int = 5):
        """Set credits given to users who chatted in the thread (on approval)."""
        await self.config.guild(ctx.guild).credits_thread.set(amount)
        await self.config.guild(ctx.guild).thread_min_msgs.set(min_messages)
        await ctx.send(f"Reward for **thread participation** set to {amount} (Min messages: {min_messages}).")
    # --------------------------------------

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

**Economy Rewards**
Create:         {cfg['credits_create']}
Approve:        {cfg['credits_approve']}
Vote:           {cfg['credits_vote']}
Thread Chat:    {cfg['credits_thread']} (Min Msgs: {cfg['thread_min_msgs']})
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
            data['reason'] = message
            suggestions[suggestion_id] = data
            
            await self.update_suggestion_message(ctx.guild, data)

            # Update Thread
            thread = ctx.guild.get_thread(data['thread_id'])
            reward_logs = []
            
            if thread:
                if not thread.name.startswith("[APPROVED]"):
                    new_name = f"[APPROVED] {thread.name}"
                    await thread.edit(name=new_name, locked=True, archived=True)
                else:
                    await thread.edit(locked=True, archived=True)
                
                # Distribute Economy Rewards
                reward_logs = await self.distribute_approval_rewards(ctx.guild, data, thread)
                    
                embed = discord.Embed(title=f"Suggestion #{suggestion_id} Approved", description=message, color=discord.Color.green())
                await thread.send(embed=embed)
                await ctx.tick()
            else:
                await ctx.send("Thread not found, status updated in DB. (Rewards not distributed due to missing thread context)")

            # DM User
            try:
                member = ctx.guild.get_member(data['author_id'])
                if member:
                    chan_id = await self.config.guild(ctx.guild).channel_id()
                    jump_url = f"https://discord.com/channels/{ctx.guild.id}/{chan_id}/{data['message_id']}"
                    
                    # Add reward summary to DM
                    reward_text = ""
                    if reward_logs:
                        reward_text = "\n\n**Rewards Distributed:**\n" + "\n".join(reward_logs)

                    await member.send(
                        f"Your suggestion #{suggestion_id} has been **APPROVED**!\n"
                        f"**Reason:** {message}{reward_text}\n\n"
                        f"Link: {jump_url}"
                    )
            except:
                pass
            
            # Optional: Post reward logs to context channel for transparency
            if reward_logs:
                await ctx.send(box("\n".join(reward_logs), lang="yaml"))

    @suggestionsset.command(name="reject")
    async def ss_reject(self, ctx, suggestion_id: str, *, message: str):
        """Reject a suggestion."""
        async with self.config.guild(ctx.guild).suggestions() as suggestions:
            if suggestion_id not in suggestions:
                return await ctx.send("Suggestion ID not found.")
            
            data = suggestions[suggestion_id]
            data['status'] = 'rejected'
            data['reason'] = message
            suggestions[suggestion_id] = data
            
            await self.update_suggestion_message(ctx.guild, data)
            
            thread = ctx.guild.get_thread(data['thread_id'])
            if thread:
                if not thread.name.startswith("[REJECTED]"):
                    new_name = f"[REJECTED] {thread.name}"
                    await thread.edit(name=new_name, locked=True, archived=True)
                else:
                    await thread.edit(locked=True, archived=True)

                embed = discord.Embed(title=f"Suggestion #{suggestion_id} Rejected", description=message, color=discord.Color.red())
                await thread.send(embed=embed)
                await ctx.tick()
            else:
                await ctx.send("Thread not found, status updated in DB.")

            # DM User
            try:
                member = ctx.guild.get_member(data['author_id'])
                if member:
                    chan_id = await self.config.guild(ctx.guild).channel_id()
                    jump_url = f"https://discord.com/channels/{ctx.guild.id}/{chan_id}/{data['message_id']}"
                    await member.send(
                        f"Your suggestion #{suggestion_id} has been **REJECTED**.\n"
                        f"**Reason:** {message}\n\n"
                        f"Link: {jump_url}"
                    )
            except:
                pass

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
        
        if open_list:
            lines = []
            for s in open_list:
                ups = len(s['upvotes'])
                downs = len(s['downvotes'])
                
                chan_id = await self.config.guild(ctx.guild).channel_id()
                link = f"https://discord.com/channels/{ctx.guild.id}/{chan_id}/{s['message_id']}"
                
                line = (
                    f"[#{s['id']} {s['title']}]({link})\n"
                    f"👍 {ups} | 👎 {downs} | <t:{int(s['timestamp'])}:R>"
                )
                lines.append(line)
            open_str = "\n\n".join(lines)
        else:
            open_str = "None"

        embed.add_field(name=f"Open ({len(open_list)})", value=open_str, inline=False)
        
        app_str = "\n".join([f"#{x['id']} {x['title']}" for x in approved_list]) or "None"
        embed.add_field(name="Recently Approved", value=app_str, inline=False)
        
        rej_str = "\n".join([f"#{x['id']} {x['title']}" for x in rejected_list]) or "None"
        embed.add_field(name="Recently Rejected", value=rej_str, inline=False)
        
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
            
        cid = interaction.data.get("custom_id", "")
        
        if cid.startswith("suggestion:vote:"):
            try:
                parts = cid.split(":")
                vote_type = parts[2] # up or down
                suggestion_id = parts[3] # ID
                
                guild = interaction.guild
                
                req_level = await self.config.guild(guild).req_level_vote()
                if req_level > 0:
                    user_level = await self.get_user_level(interaction.user)
                    if user_level < req_level:
                        await interaction.response.send_message(
                            f"You need to be Level {req_level} to vote. (Current: {user_level})", 
                            ephemeral=True
                        )
                        return

                async with self.config.guild(guild).suggestions() as suggestions:
                    if suggestion_id not in suggestions:
                        await interaction.response.send_message("Suggestion not found.", ephemeral=True)
                        return
                        
                    data = suggestions[suggestion_id]
                    
                    if data['status'] != 'open':
                        await interaction.response.send_message("Voting is closed.", ephemeral=True)
                        return

                    uid = interaction.user.id
                    ups = set(data['upvotes'])
                    downs = set(data['downvotes'])
                    
                    msg_txt = "Vote recorded."
                    
                    if vote_type == "up":
                        if uid in ups:
                            ups.remove(uid)
                            msg_txt = "Upvote removed."
                        else:
                            ups.add(uid)
                            if uid in downs: downs.remove(uid)
                            msg_txt = "Upvoted!"
                    elif vote_type == "down":
                        if uid in downs:
                            downs.remove(uid)
                            msg_txt = "Downvote removed."
                        else:
                            downs.add(uid)
                            if uid in ups: ups.remove(uid)
                            msg_txt = "Downvoted."
                            
                    data['upvotes'] = list(ups)
                    data['downvotes'] = list(downs)
                    suggestions[suggestion_id] = data

                    await self.update_suggestion_message(guild, data)
                    await interaction.response.send_message(msg_txt, ephemeral=True)
            except Exception as e:
                pass