import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone, time
from typing import Dict, List, Literal, Optional, Set, Union
from pathlib import Path

import discord
from discord.ext import tasks 
from redbot.core import commands, Config, app_commands, bank
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_list, box, bold, warning, error, info, success, pagify
from red_commons.logging import getLogger
from pydantic import BaseModel, Field, ValidationError

# --- Pydantic Models ---

class ListPriorityRule(BaseModel):
    priority: int
    start_md: str
    end_md: str

class QuestionList(BaseModel):
    id: str
    name: str
    # Priority rules determine if a list is "Active" on a specific date
    # and how important it is relative to other lists in the same schedule.
    priority_rules: List[ListPriorityRule] = Field(default_factory=list)

class Schedule(BaseModel):
    id: str
    channel_id: int
    frequency: str
    post_time: Optional[str] = None
    next_run_time: datetime
    # If set, this defines the anchor point for the schedule intervals (e.g., Every 2 weeks FROM this date)
    start_date: Optional[datetime] = None
    # Specific lists linked to this schedule.
    lists: List[str] = Field(default_factory=list)

class QuestionData(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    suggested_by: Optional[int] = None
    list_id: str
    status: Literal["pending", "not asked", "asked"] = "not asked"
    added_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_asked: Optional[datetime] = None

# -------------------------------------------------------------------

log = getLogger("red.qotd")

# --- Custom Views ---

class SuggestionModal(discord.ui.Modal, title="Submit a Question of the Day"):
    def __init__(self, cog: "QuestionOfTheDay", list_names: List[str]):
        super().__init__(timeout=300)
        self.cog = cog
        self.list_names = list_names

    question_text = discord.ui.TextInput(
        label="Question",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your question here...",
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        new_q = QuestionData(
            question=str(self.question_text),
            suggested_by=interaction.user.id,
            list_id="suggestions", 
            status="pending",
        )

        reward_msg = ""
        try:
            await self.cog.add_question_to_data(new_q)
            
            reward = await self.cog.config.suggestion_reward()
            if reward > 0:
                paid = await self.cog._attempt_deposit(interaction.user, reward)
                if paid:
                    currency = await bank.get_currency_name(interaction.guild)
                    reward_msg = f"\n\n💰 You received **{reward} {currency}** for your suggestion!"

        except Exception as e:
            log.exception("Failed to add new question data.")
            return await interaction.followup.send(f"Error saving question: {e}", ephemeral=True)

        embed = discord.Embed(
            title="✅ Question Submitted!",
            description=f"Your question has been added to the review queue.{reward_msg}",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class SuggestionButton(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", list_names: List[str]):
        super().__init__(timeout=None)
        self.cog = cog
        self._list_names = list_names 

    @discord.ui.button(label="Suggest a Question", style=discord.ButtonStyle.primary, custom_id="qotd_suggest_button")
    async def suggest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        lists_data = await self.cog.config.lists()
        current_list_names = [v['name'] for v in lists_data.values() if v['id'] not in ["suggestions", "unassigned"]]
        await interaction.response.send_modal(SuggestionModal(self.cog, current_list_names))


class ApprovalView(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", question_data: QuestionData, question_id: str, lists: Dict[str, QuestionList]):
        super().__init__(timeout=None) 
        self.cog = cog
        self.question_data = question_data
        self.question_id = question_id
        self.lists = lists

        list_options = [
            discord.SelectOption(label=list_obj.name, value=list_id)
            for list_id, list_obj in lists.items() if list_id not in ["suggestions", "unassigned"]
        ]

        if list_options:
            self.list_select = discord.ui.Select(
                placeholder="Select a Question List to Approve Into",
                options=list_options,
                min_values=1,
                max_values=1,
                custom_id=f"qotd_approval_list_select_{question_id}"
            )
            self.list_select.callback = self.approve_callback
            self.add_item(self.list_select)
        else:
             log.warning("No valid non-suggestion lists found for ApprovalView dropdown.")

    async def approval_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This must be run in a guild.", ephemeral=True)
            return False
        if await self.cog.bot.is_owner(interaction.user) or interaction.user.guild_permissions.manage_guild:
             return True
        await interaction.response.send_message("You do not have permission to approve questions.", ephemeral=True)
        return False

    async def approve_callback(self, interaction: discord.Interaction):
        if not await self.approval_check(interaction):
            return

        selected_list_id = self.list_select.values[0]
        self.question_data.list_id = selected_list_id
        self.question_data.status = "not asked"
        self.question_data.added_on = datetime.now(timezone.utc) 

        await self.cog.update_question_data(self.question_id, self.question_data)
        
        reward_text = ""
        reward_amt = await self.cog.config.approval_reward()
        if reward_amt > 0 and self.question_data.suggested_by:
            guild = interaction.guild
            member = guild.get_member(self.question_data.suggested_by)
            if not member:
                try:
                    member = await guild.fetch_member(self.question_data.suggested_by)
                except discord.NotFound:
                    member = None
            
            if member:
                paid = await self.cog._attempt_deposit(member, reward_amt)
                if paid:
                    currency = await bank.get_currency_name(guild)
                    reward_text = f"\n💰 **{reward_amt} {currency}** awarded to {member.mention}."

        embed = interaction.message.embeds[0]
        embed.title = "✅ Question Approved!"
        embed.description = f"Approved by {interaction.user.display_name} into list: **{self.lists[selected_list_id].name}**{reward_text}"
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Processed by: {interaction.user.name}")

        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="qotd_reject_button")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.approval_check(interaction):
            return
        await self.cog.delete_question_by_id(self.question_id)
        
        embed = interaction.message.embeds[0]
        embed.title = "❌ Question Rejected!"
        embed.description = f"Rejected by {interaction.user.display_name}. Question deleted."
        embed.color = discord.Color.red()
        embed.set_footer(text=f"Processed by: {interaction.user.name}")

        await interaction.response.edit_message(embed=embed, view=None)


# --- Cog Class ---

class QuestionOfTheDay(commands.Cog):
    """
    Manages and posts scheduled Questions of the Day (QOTD).
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6942069, force_registration=True)
        default_global = {
            "questions": {}, 
            "lists": {
                "general": QuestionList(id="general", name="General Questions").model_dump(),
                "suggestions": QuestionList(id="suggestions", name="Pending Suggestions").model_dump(),
                "unassigned": QuestionList(id="unassigned", name="Unassigned").model_dump(),
            },
            "schedules": {}, 
            "approval_channel": None,
            "suggestion_reward": 0,
            "approval_reward": 0,
        }
        self.config.register_global(**default_global)
        self.qotd_poster.start()
        self.bot.add_view(SuggestionButton(self, [])) 

    def cog_unload(self):
        self.qotd_poster.cancel()

    async def red_delete_data_for_user(self, *, requester: Literal["discord", "owner", "admin", "user"], user_id: int):
        async with self.config.questions() as questions:
            keys_to_delete = [
                qid for qid, qdata in questions.items()
                if qdata.get("suggested_by") == user_id
            ]
            for key in keys_to_delete:
                del questions[key]

    async def _attempt_deposit(self, member: discord.Member, amount: int) -> bool:
        if amount <= 0: return False
        try:
            await bank.deposit_credits(member, amount)
            return True
        except Exception as e:
            log.error(f"Failed to deposit {amount} credits to {member.id}: {e}")
            return False
            
    def _migrate_list_dict(self, l_dict: dict) -> dict:
        """Migrate old list structure to new priority rules."""
        if 'exclusion_dates' in l_dict:
            exclusions = l_dict.pop('exclusion_dates', [])
            if exclusions and not l_dict.get('priority_rules'):
                new_rules = []
                for date_str in exclusions:
                    new_rules.append({'priority': 0, 'start_md': date_str, 'end_md': date_str})
                l_dict['priority_rules'] = new_rules
        return l_dict

    @tasks.loop(minutes=1)
    async def qotd_poster(self):
        now_utc = datetime.now(timezone.utc)
        schedules_data = await self.config.schedules()
        
        for schedule_id, schedule_dict in schedules_data.items():
            schedule = None
            try:
                # 1. Date Fix
                next_run_time_data = schedule_dict.get('next_run_time')
                if isinstance(next_run_time_data, str):
                    dt_obj = datetime.fromisoformat(next_run_time_data)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['next_run_time'] = dt_obj
                
                # 1b. Start Date Fix
                start_date_data = schedule_dict.get('start_date')
                if isinstance(start_date_data, str):
                    dt_obj = datetime.fromisoformat(start_date_data)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['start_date'] = dt_obj

                # 2. Cleanup
                schedule_dict.pop('list_id', None) # Legacy cleanup
                
                # 3. Validation
                schedule = Schedule.model_validate(schedule_dict)

            except (ValidationError, ValueError) as e:
                log.error(f"Failed to validate schedule {schedule_id}: {e}")
                continue

            if schedule and now_utc >= schedule.next_run_time:
                try:
                    await self._post_scheduled_question(schedule_id, schedule)
                except Exception as e:
                    log.exception(f"Critical error during _post_scheduled_question for {schedule_id}: {e}")
                    await self._update_schedule_next_run(schedule_id, schedule, now_utc)

    def _is_date_in_range(self, check_date: datetime, start_md: str, end_md: str) -> bool:
        """Checks if a date falls within MM-DD range, handling year wrap."""
        try:
            current_md = check_date.strftime("%m-%d")
            if start_md <= end_md:
                return start_md <= current_md <= end_md
            # Handles wrap around (e.g. 12-25 to 01-05)
            return current_md >= start_md or current_md <= end_md
        except ValueError:
            return False

    async def _resolve_list_priority(self, list_obj: QuestionList, now_utc: datetime) -> int:
        """
        Determines the priority of a list for the given date.
        Returns:
            int: The priority number (1 is highest).
            0: Explicitly excluded.
            999: No specific rule found (Default/Low priority).
        """
        best_priority = 999 
        
        has_match = False
        for rule in list_obj.priority_rules:
            if self._is_date_in_range(now_utc, rule.start_md, rule.end_md):
                has_match = True
                if rule.priority == 0:
                    return 0 # Explicit exclusion overrides everything
                if rule.priority < best_priority:
                    best_priority = rule.priority
        
        # If no date rules matched, it defaults to 999 (available but low priority)
        return best_priority

    async def _post_scheduled_question(self, schedule_id: str, schedule: Schedule):
        now_utc = datetime.now(timezone.utc)
        
        # 0. Check if Schedule has linked lists
        if not schedule.lists:
            log.warning(f"Schedule '{schedule_id}' tried to run but has no linked lists.")
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return

        lists_data = await self.config.lists()
        questions_data = await self.config.questions()

        # 1. Filter Lists: Only look at lists LINKED to this schedule
        candidates = []
        
        for list_id in schedule.lists:
            if list_id not in lists_data: continue # Skip deleted lists
            
            l_data = lists_data[list_id]
            
            try:
                l_data = self._migrate_list_dict(l_data)
                l_obj = QuestionList.model_validate(l_data)
                
                prio = await self._resolve_list_priority(l_obj, now_utc)
                
                # Priority 0 means "Excluded for this date range"
                if prio > 0:
                    candidates.append((prio, l_obj))
                    
            except ValidationError:
                log.warning(f"Validation error for list {list_id}")
                continue

        # 2. Sort by Priority (Ascending: 1 is top, 999 is bottom)
        candidates.sort(key=lambda x: x[0])
        
        if not candidates:
            log.info(f"Schedule {schedule_id}: No available lists (all excluded by date rules).")
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return

        selected_q = None
        selected_qid = None
        selected_list_name = ""

        # 3. Iterate candidates to find a valid question
        # We look at top priority lists first. If they have no questions, we fall back to lower priority.
        for prio, target_list in candidates:
            
            eligible_q_data = {
                qid: qdata
                for qid, qdata in questions_data.items()
                if qdata.get("list_id") == target_list.id and qdata.get("status") in ("not asked", "asked")
            }
            
            if not eligible_q_data:
                continue 

            eligible_q_objs = []
            for qid, qdata in eligible_q_data.items():
                try:
                    qdata_copy = qdata.copy()
                    qdata_copy['added_on'] = datetime.fromisoformat(qdata['added_on'])
                    qdata_copy['last_asked'] = datetime.fromisoformat(qdata['last_asked']) if qdata['last_asked'] else None
                    qdata_copy['id'] = qid
                    eligible_q_objs.append(QuestionData.model_validate(qdata_copy))
                except (ValidationError, ValueError):
                    continue

            if not eligible_q_objs:
                continue

            not_asked = [q for q in eligible_q_objs if q.status == "not asked"]
            
            if not_asked:
                selected_q = random.choice(not_asked)
                selected_qid = selected_q.id
            else:
                # Recycle oldest if all asked
                def sort_key(q):
                    if q.last_asked is None: 
                        return timedelta(days=3650).total_seconds() 
                    if q.last_asked.tzinfo is None:
                        q.last_asked = q.last_asked.replace(tzinfo=timezone.utc)
                    return (now_utc - q.last_asked).total_seconds()
                
                eligible_q_objs.sort(key=sort_key, reverse=False)
                # Pick from top 5 oldest to add slight variety
                top_candidates = eligible_q_objs[:min(5, len(eligible_q_objs))]
                selected_q = random.choice(top_candidates)
                selected_qid = selected_q.id

            if selected_q:
                selected_list_name = target_list.name
                break # Found a question

        if not selected_q:
             log.info(f"Schedule {schedule_id}: Lists available {len(candidates)}, but no questions found in them.")
             await self._update_schedule_next_run(schedule_id, schedule, now_utc)
             return

        channel = self.bot.get_channel(schedule.channel_id)
        if not channel:
            log.warning(f"Schedule {schedule_id}: Channel {schedule.channel_id} not found.")
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return
            
        # --- EMBED UPDATE ---
        embed = discord.Embed(title="Question Of The Day", color=discord.Color.blue())
        embed.description = selected_q.question
        
        footer_text = f"List: {selected_list_name}"
        if selected_q.suggested_by:
            user = self.bot.get_user(selected_q.suggested_by)
            if user:
                footer_text += f" | Suggested by {user.display_name}"
            else:
                footer_text += f" | Suggested by ID: {selected_q.suggested_by}"
        
        embed.set_footer(text=footer_text)
        # --------------------

        try:
            lists_data = await self.config.lists()
            list_names = [v['name'] for v in lists_data.values() if v['id'] not in ["suggestions", "unassigned"]]
            view = SuggestionButton(self, list_names) 
            
            await channel.send(embed=embed, view=view)
            
            selected_q.status = "asked"
            selected_q.last_asked = now_utc
            await self.update_question_data(selected_qid, selected_q)
        except discord.Forbidden:
            log.error(f"Missing permissions for channel {channel.id}.")
        except Exception as e:
            log.exception(f"Error posting QOTD: {e}")

        await self._update_schedule_next_run(schedule_id, schedule, now_utc)

    def _calculate_next_run_time(self, schedule: Schedule, last_run: datetime) -> datetime:
        now_utc = datetime.now(timezone.utc)
        
        # Parse Frequency Delta
        try:
            time_unit = schedule.frequency.split()
            if len(time_unit) != 2: raise ValueError
            amount = int(time_unit[0])
            unit = time_unit[1].lower().rstrip('s')
            if unit == 'minute': delta = timedelta(minutes=amount)
            elif unit == 'hour': delta = timedelta(hours=amount)
            elif unit == 'day': delta = timedelta(days=amount)
            elif unit == 'week': delta = timedelta(weeks=amount)
            else: raise ValueError
        except (ValueError, IndexError, TypeError):
            # Fallback for bad frequency
            return now_utc + timedelta(days=3650) 
            
        # 1. Start Date Logic (Anchored)
        if schedule.start_date:
            # If the start date is in the future, that is our next run.
            if schedule.start_date > now_utc:
                return schedule.start_date
            
            # If start date is in the past, calculate the next slot based on delta intervals
            elapsed_seconds = (now_utc - schedule.start_date).total_seconds()
            delta_seconds = delta.total_seconds()
            
            if delta_seconds <= 0: return now_utc + timedelta(days=365)

            periods_passed = int(elapsed_seconds // delta_seconds)
            next_run = schedule.start_date + (delta * (periods_passed + 1))
            
            return next_run

        # 2. Daily/Time Logic (Non-anchored date, but anchored time)
        if schedule.post_time:
            try:
                hour, minute = map(int, schedule.post_time.split(':'))
                target_time_today = datetime.combine(now_utc.date(), time(hour, minute), tzinfo=timezone.utc)
            except ValueError:
                return last_run + delta 

            next_run = target_time_today
            
            # Logic: If target is in past, move to next cycle
            # This handles "Daily at 6AM" when it is currently 7AM
            while next_run <= now_utc:
                 next_run += delta
                 
            return next_run
        
        # 3. Simple Interval Logic (No time anchor)
        else:
            return last_run + delta

    async def _update_schedule_next_run(self, schedule_id: str, schedule: Schedule, last_run: datetime):
        schedule.next_run_time = self._calculate_next_run_time(schedule, last_run)
        
        serialized_schedule = json.loads(schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule

    async def add_question_to_data(self, question: QuestionData):
        question_id = question.id 
        serialized_q = json.loads(question.model_dump_json())
        async with self.config.questions() as questions:
            questions[question_id] = serialized_q
        if question.list_id == "suggestions":
            await self._notify_new_suggestion(question_id, question)

    async def update_question_data(self, qid: str, question: QuestionData):
        serialized_q = json.loads(question.model_dump_json())
        async with self.config.questions() as questions:
            questions[qid] = serialized_q

    async def delete_question_by_id(self, qid: str):
        async with self.config.questions() as questions:
            if qid in questions:
                del questions[qid]
        
    async def _notify_new_suggestion(self, qid: str, question: QuestionData):
        approval_channel_id = await self.config.approval_channel()
        if not approval_channel_id: return
        channel = self.bot.get_channel(approval_channel_id)
        if not channel: return

        try:
            suggested_user = await self.bot.fetch_user(question.suggested_by) if question.suggested_by else None
        except discord.NotFound:
            suggested_user = None
        suggested_by_text = suggested_user.display_name if suggested_user else f"ID: {question.suggested_by}"
        
        lists_data = await self.config.lists()
        lists = {k: QuestionList.model_validate(v) for k,v in lists_data.items() if 'id' in v}

        embed = discord.Embed(
            title="✨ New Question Suggestion Pending Approval",
            description=box(question.question, lang="text"),
            color=discord.Color.orange()
        )
        embed.add_field(name="Suggested By", value=suggested_by_text)
        embed.set_footer(text=f"Question ID: {qid.split('-')[0]}")

        await channel.send(embed=embed, view=ApprovalView(self, question, qid, lists))

    @commands.Cog.listener()
    async def on_ready(self):
        lists_data = await self.config.lists()
        list_names = [v['name'] for v in lists_data.values() if v['id'] not in ["suggestions", "unassigned"]]
        self.bot.add_view(SuggestionButton(self, list_names))
        
    @commands.guild_only()
    @commands.admin()
    @commands.group(name="qotd", aliases=["qotdd", "qotdset"])
    async def qotd(self, ctx: commands.Context):
        """Base command for Question of the Day administration."""
        pass

    @qotd.command(name="configchannel")
    async def qotd_config_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where new question suggestions are posted for approval."""
        await self.config.approval_channel.set(channel.id)
        await ctx.send(f"Approval channel set to {channel.mention}.")

    @qotd.group(name="set")
    async def qotd_set(self, ctx: commands.Context):
        """Configure QOTD settings."""
        pass

    @qotd_set.command(name="view")
    async def qotd_set_view(self, ctx: commands.Context):
        """View all configurations, lists, and schedules."""
        
        embed = discord.Embed(title="Question of the Day Configuration", color=await ctx.embed_color())

        # 1. Global Config
        suggestion_reward = await self.config.suggestion_reward()
        approval_reward = await self.config.approval_reward()
        approval_channel_id = await self.config.approval_channel()
        approval_channel = self.bot.get_channel(approval_channel_id).mention if approval_channel_id else "Not Set"
        currency = await bank.get_currency_name(ctx.guild)

        global_settings = (
            f"**Suggestion Reward:** {suggestion_reward} {currency}\n"
            f"**Approval Reward:** {approval_reward} {currency}\n"
            f"**Approval Channel:** {approval_channel}"
        )
        embed.add_field(name="Global Settings", value=global_settings, inline=False)

        # 2. Lists
        lists_data = await self.config.lists()
        all_questions = await self.config.questions()
        
        list_text = ""
        if not lists_data:
            list_text = "No lists defined."
        else:
            for lid, ldata in lists_data.items():
                ldata = self._migrate_list_dict(ldata) 
                
                count = sum(1 for q in all_questions.values() if q.get('list_id') == lid)
                list_text += f"**{ldata['name']}** (`{lid}`): {count} questions\n"
                
                if 'priority_rules' in ldata and ldata['priority_rules']:
                    sorted_rules = sorted(ldata['priority_rules'], key=lambda x: x['start_md'])
                    for r in sorted_rules:
                        p_str = f"Priority {r['priority']}" if r['priority'] > 0 else "Excluded"
                        list_text += f"└ {r['start_md']} to {r['end_md']}: {p_str}\n"

        if len(list_text) > 1024:
            list_text = list_text[:1021] + "..."
        
        if not list_text: list_text = "No lists configured."

        embed.add_field(name="Question Lists & Priorities", value=list_text, inline=False)

        # 3. Schedules
        schedules_data = await self.config.schedules()
        schedule_text = ""
        
        if not schedules_data:
            schedule_text = "No schedules defined."
        else:
            for sid, sdata in schedules_data.items():
                chan_id = sdata.get('channel_id')
                chan = ctx.guild.get_channel(chan_id)
                chan_name = chan.mention if chan else f"Invalid Channel {chan_id}"
                
                run_time_str = sdata.get('next_run_time')
                next_run_str = "Unknown"
                if isinstance(run_time_str, str):
                    try:
                        rt = datetime.fromisoformat(run_time_str)
                        if rt.tzinfo is None: rt = rt.replace(tzinfo=timezone.utc)
                        next_run_str = discord.utils.format_dt(rt, "R")
                    except: next_run_str = "Invalid Date"

                linked_lists = sdata.get('lists', [])
                linked_str = humanize_list([f"`{l}`" for l in linked_lists]) if linked_lists else "⚠️ No lists linked"

                # Check for start date
                start_date_str = ""
                sd_raw = sdata.get('start_date')
                if isinstance(sd_raw, str):
                    try: 
                        sd_dt = datetime.fromisoformat(sd_raw)
                        start_date_str = f"\n└ Starts: {discord.utils.format_dt(sd_dt, 'D')}"
                    except: pass

                schedule_text += f"**{sid}** in {chan_name}\n"
                schedule_text += f"└ Freq: `{sdata.get('frequency')}` | Next: {next_run_str}"
                schedule_text += f"{start_date_str}\n"
                schedule_text += f"└ Linked Lists: {linked_str}\n"

        if len(schedule_text) > 1024:
            schedule_text = schedule_text[:1021] + "..."
            
        embed.add_field(name="Schedules", value=schedule_text, inline=False)

        await ctx.send(embed=embed)


    @qotd_set.command(name="suggestionreward")
    async def qotd_set_suggestionreward(self, ctx: commands.Context, amount: int):
        """Set the bank credit reward for submitting a suggestion."""
        if amount < 0:
            return await ctx.send(warning("Amount cannot be negative."))
        await self.config.suggestion_reward.set(amount)
        currency = await bank.get_currency_name(ctx.guild)
        await ctx.send(success(f"Suggestion reward set to **{amount} {currency}**."))

    @qotd_set.command(name="approvalreward")
    async def qotd_set_approvalreward(self, ctx: commands.Context, amount: int):
        """Set the bank credit reward when a suggestion is approved."""
        if amount < 0:
            return await ctx.send(warning("Amount cannot be negative."))
        await self.config.approval_reward.set(amount)
        currency = await bank.get_currency_name(ctx.guild)
        await ctx.send(success(f"Approval reward set to **{amount} {currency}**."))

    @qotd.group(name="question")
    async def qotd_question(self, ctx: commands.Context):
        """Manage individual questions."""
        pass

    @qotd_question.command(name="listuser")
    async def qotd_question_listuser(self, ctx: commands.Context, user: discord.User):
        """List all questions submitted by a specific user."""
        all_questions = await self.config.questions()
        user_questions = []

        for qid, qdata in all_questions.items():
            if qdata.get("suggested_by") == user.id:
                try:
                    if isinstance(qdata.get('added_on'), str):
                        qdata['added_on'] = datetime.fromisoformat(qdata['added_on'])
                    qdata['id'] = qid
                    q_obj = QuestionData.model_validate(qdata)
                    user_questions.append(q_obj)
                except (ValidationError, ValueError):
                    continue

        if not user_questions:
            return await ctx.send(info(f"No questions found for {user.name}."))

        user_questions.sort(key=lambda q: q.added_on, reverse=True)

        msg = f"**Questions by {user.name}**\n\n"
        for q in user_questions[:15]: 
            status_icon = "⏳" if q.status == "pending" else ("✅" if q.status == "not asked" else "📢")
            short_id = q.id.split('-')[0]
            msg += f"`{short_id}` {status_icon} **[{q.status.upper()}]** - {q.question[:50]}...\n"
        
        if len(user_questions) > 15:
            msg += f"\n...and {len(user_questions) - 15} more."

        await ctx.send(box(msg, lang="md"))

    @qotd_question.command(name="view")
    async def qotd_question_view(self, ctx: commands.Context, question_id: str):
        """View details of a specific question by ID."""
        all_questions = await self.config.questions()
        matched_qid = next((qid for qid in all_questions if qid.startswith(question_id)), None)
        
        if not matched_qid:
            return await ctx.send(warning(f"Question with ID `{question_id}` not found."))
            
        qdata = all_questions[matched_qid]
        try:
            if isinstance(qdata.get('added_on'), str):
                qdata['added_on'] = datetime.fromisoformat(qdata['added_on'])
            if isinstance(qdata.get('last_asked'), str):
                qdata['last_asked'] = datetime.fromisoformat(qdata['last_asked'])
            question = QuestionData.model_validate(qdata)
        except (ValidationError, ValueError):
            return await ctx.send(warning(f"Question data corrupt."))

        lists_data = await self.config.lists()
        list_name = "Unknown List"
        if question.list_id in lists_data:
            list_name = lists_data[question.list_id]['name']
        elif question.list_id == 'suggestions':
            list_name = "Pending Suggestions"

        suggested_by_str = "System/Unknown"
        if question.suggested_by:
            user = self.bot.get_user(question.suggested_by)
            if user: suggested_by_str = f"{user.mention} ({user.id})"
            else: suggested_by_str = f"ID: {question.suggested_by}"

        short_id = matched_qid.split('-')[0]
        embed = discord.Embed(title=f"Question Details (ID: {short_id})", color=discord.Color.blue())
        embed.description = box(question.question, lang="text")
        embed.add_field(name="List", value=f"{list_name} (`{question.list_id}`)", inline=True)
        embed.add_field(name="Status", value=question.status.title(), inline=True)
        embed.add_field(name="Suggested By", value=suggested_by_str, inline=False)
        
        if question.added_on.tzinfo is None:
             question.added_on = question.added_on.replace(tzinfo=timezone.utc)
        added_ts = discord.utils.format_dt(question.added_on, 'f')
        embed.add_field(name="Created On", value=added_ts, inline=False)
        
        await ctx.send(embed=embed)

    @qotd_question.command(name="remove")
    async def qotd_question_remove(self, ctx: commands.Context, question_id: str):
        """Permanently delete a specific question."""
        all_questions = await self.config.questions()
        matched_qid = next((qid for qid in all_questions if qid.startswith(question_id)), None)
        if not matched_qid: return await ctx.send(warning(f"Question not found."))
        await self.delete_question_by_id(matched_qid)
        await ctx.send(success(f"Question `{matched_qid.split('-')[0]}` deleted."))

    @qotd.group(name="suggest")
    async def qotd_suggest_admin(self, ctx: commands.Context):
        """Admin commands for managing pending question suggestions."""
        pass

    @qotd_suggest_admin.command(name="list")
    async def qotd_suggest_list(self, ctx: commands.Context):
        """Lists the oldest 5 pending question suggestions."""
        all_questions = await self.config.questions()
        lists_data = await self.config.lists()
        
        pending_questions_data = {qid: qdict for qid, qdict in all_questions.items() if qdict.get('list_id') == 'suggestions' and qdict.get('status') == 'pending'}
        pending_questions = {}
        for qid, qdict in pending_questions_data.items():
            try:
                qdict['added_on'] = datetime.fromisoformat(qdict['added_on']) if isinstance(qdict.get('added_on'), str) else qdict.get('added_on')
                qdict['last_asked'] = datetime.fromisoformat(qdict['last_asked']) if qdict.get('last_asked') else None
                qdict['id'] = qid
                pending_questions[qid] = QuestionData.model_validate(qdict)
            except (ValidationError, ValueError):
                continue
                
        if not pending_questions:
            return await ctx.send("There are currently no pending question suggestions.")

        limit = 5 
        suggestions_to_show = sorted(pending_questions.items(), key=lambda item: item[1].added_on)[:limit]
        remaining_count = len(pending_questions) - len(suggestions_to_show)

        try:
            lists = {k: QuestionList.model_validate(v) for k, v in lists_data.items()}
        except ValidationError:
            await ctx.send(warning("Failed to load question lists."))
            return

        await ctx.send(bold(f"Found {len(pending_questions)} pending suggestions. Displaying oldest {len(suggestions_to_show)}..."))
        
        for qid, q_obj in suggestions_to_show:
            suggested_by_id = q_obj.suggested_by
            suggested_by_str = f"User ID: {suggested_by_id}"
            if suggested_by_id:
                user = self.bot.get_user(suggested_by_id)
                if user: suggested_by_str = user.mention
            short_qid = qid.split('-')[0]
            embed = discord.Embed(title=f"❓ Pending Suggestion (ID: {short_qid})", description=box(q_obj.question, lang="text"), color=discord.Color.orange())
            embed.add_field(name="Suggested By", value=suggested_by_str, inline=True)
            embed.add_field(name="Date Submitted", value=discord.utils.format_dt(q_obj.added_on, 'R'), inline=True)
            await ctx.send(embed=embed, view=ApprovalView(self, q_obj, qid, lists))

        if remaining_count > 0:
            await ctx.send(f"\n{remaining_count} more suggestions pending.")

    @qotd_suggest_admin.command(name="approve")
    async def qotd_suggest_approve(self, ctx: commands.Context, short_qid: str, list_id: str):
        """Approves a pending question by ID."""
        all_questions = await self.config.questions()
        lists_data = await self.config.lists()
        full_qid = next((qid for qid in all_questions if qid.startswith(short_qid) and all_questions[qid].get('list_id') == 'suggestions'), None)

        if not full_qid:
            return await ctx.send(warning(f"No pending suggestion found with short ID `{short_qid}`."))
        if list_id not in lists_data or list_id in ['suggestions', 'unassigned']:
            return await ctx.send(warning(f"List ID `{list_id}` is invalid."))
            
        try:
            qdata = all_questions[full_qid]
            qdata['added_on'] = datetime.fromisoformat(qdata['added_on'])
            qdata['last_asked'] = datetime.fromisoformat(qdata['last_asked']) if qdata['last_asked'] else None
            qdata['id'] = full_qid
            question_data = QuestionData.model_validate(qdata)
        except (ValidationError, ValueError):
            return await ctx.send(warning(f"Question data for `{short_qid}` is corrupt."))
            
        question_data.list_id = list_id
        question_data.status = "not asked"
        question_data.added_on = datetime.now(timezone.utc) 
        await self.update_question_data(full_qid, question_data)
        
        reward = await self.config.approval_reward()
        if reward > 0 and question_data.suggested_by:
            guild = ctx.guild
            member = guild.get_member(question_data.suggested_by)
            if not member:
                 try: member = await guild.fetch_member(question_data.suggested_by)
                 except: member = None
            
            if member:
                if await self._attempt_deposit(member, reward):
                     currency = await bank.get_currency_name(ctx.guild)
                     await ctx.send(info(f"Awarded **{reward} {currency}** to {member.mention}."))

        list_name = lists_data[list_id]['name']
        await ctx.send(f"✅ Approved suggestion `{short_qid}` and moved it to the **{list_name}** list.")

    @qotd_suggest_admin.command(name="delete")
    async def qotd_suggest_delete(self, ctx: commands.Context, short_qid: str):
        """Deletes a pending question suggestion by ID."""
        all_questions = await self.config.questions()
        full_qid = next((qid for qid in all_questions if qid.startswith(short_qid) and all_questions[qid].get('list_id') == 'suggestions'), None)
        if not full_qid:
            return await ctx.send(warning(f"No pending suggestion found with short ID `{short_qid}`."))
        await self.delete_question_by_id(full_qid)
        await ctx.send(f"❌ Deleted pending suggestion with ID `{short_qid}`.")

    @qotd.group(name="list")
    async def qotd_list_management(self, ctx: commands.Context):
        """Manage Question Lists."""
        pass
    
    @qotd_list_management.command(name="add")
    async def qotd_list_add(self, ctx: commands.Context, list_name: str, list_id: Optional[str] = None):
        """Adds a new question list."""
        lists_data = await self.config.lists()
        if any(l.get('name', '').lower() == list_name.lower() for l in lists_data.values()):
             return await ctx.send(warning(f"A list named **{list_name}** already exists."))

        if list_id is None:
            new_id = str(uuid.uuid4()).split('-')[0]
        else:
            new_id = list_id.lower().replace(" ", "-")
            if not all(c.isalnum() or c == '-' for c in new_id):
                 return await ctx.send(warning("Provided list ID can only contain letters, numbers, and hyphens (`-`)."))
            if new_id in lists_data:
                return await ctx.send(warning(f"The list ID `{new_id}` is already in use."))
            
        new_list = QuestionList(id=new_id, name=list_name)
        async with self.config.lists() as lists:
            lists[new_id] = new_list.model_dump() 
            
        await ctx.send(f"Added new question list: **{list_name}** (ID: `{new_id}`).")

    @qotd_list_management.command(name="remove")
    async def qotd_list_remove(self, ctx: commands.Context, list_id: str):
        """Removes a list."""
        lists_data = await self.config.lists()
        
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
            
        system_lists = ["general", "suggestions", "unassigned"]
        if list_id in system_lists:
            return await ctx.send(warning(f"The system list `{list_id}` cannot be removed."))
            
        list_name = lists_data[list_id]['name']

        confirm_msg = await ctx.send(warning(f"Are you sure you want to remove list **{list_name}**? Questions will be moved to 'Unassigned'. Type `yes`."))

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await confirm_msg.edit(content="Removal cancelled.", embed=None)
            return

        questions_moved = 0
        async with self.config.questions() as questions:
            keys_to_move = [qid for qid, qdata in questions.items() if qdata.get('list_id') == list_id]
            for qid in keys_to_move:
                questions[qid]['list_id'] = "unassigned"
                questions[qid]['status'] = "not asked" 
                questions_moved += 1

        async with self.config.lists() as lists:
            del lists[list_id]
            
        # Also clean up schedules using this list
        async with self.config.schedules() as schedules:
             for s_id, s_data in schedules.items():
                 if 'lists' in s_data and list_id in s_data['lists']:
                     s_data['lists'].remove(list_id)

        await ctx.send(success(f"List **{list_name}** removed. **{questions_moved}** questions moved to **Unassigned**."))

    @qotd_list_management.command(name="view")
    async def qotd_list_view(self, ctx: commands.Context):
        """Displays all available question lists."""
        lists_data = await self.config.lists()
        all_questions = await self.config.questions()
        
        if not lists_data:
            return await ctx.send(info("No lists configured."))

        msg = ""
        for lid, ldata in lists_data.items():
            ldata = self._migrate_list_dict(ldata)
            count = sum(1 for q in all_questions.values() if q.get('list_id') == lid)
            
            msg += f"**{ldata['name']}** (`{lid}`)\n"
            msg += f"Questions: {count}\n"
            
            if 'priority_rules' in ldata and ldata['priority_rules']:
                sorted_rules = sorted(ldata['priority_rules'], key=lambda x: x['start_md'])
                for r in sorted_rules:
                    p_str = f"Priority {r['priority']}" if r['priority'] > 0 else "Excluded"
                    msg += f"└ {r['start_md']} to {r['end_md']}: {p_str}\n"
            msg += "\n"

        pages = list(pagify(msg, page_length=2000)) # Keep it safe for description or content
        for i, page in enumerate(pages):
            embed = discord.Embed(
                title="Question Lists",
                description=page,
                color=await ctx.embed_color()
            )
            if len(pages) > 1:
                embed.set_footer(text=f"Page {i+1}/{len(pages)}")
            await ctx.send(embed=embed)

    @qotd_list_management.command(name="clear")
    async def qotd_list_clear(self, ctx: commands.Context, list_id: str):
        """Clears all questions from a list (moves to Unassigned)."""
        lists_data = await self.config.lists()
        if list_id not in lists_data: return await ctx.send(warning(f"List ID `{list_id}` not found."))
        
        if list_id in ["suggestions", "unassigned", "general"]:
             return await ctx.send(warning(f"Cannot clear system list `{list_id}`."))

        confirm_msg = await ctx.send(warning(f"Move ALL questions from `{list_id}` to 'Unassigned'? Type `yes`."))
        def check(m): return m.author == ctx.author and m.content.lower() == "yes"
        try: await self.bot.wait_for("message", check=check, timeout=30.0)
        except: return await ctx.send("Cancelled.")

        questions_moved = 0
        async with self.config.questions() as questions:
            keys = [qid for qid, qdata in questions.items() if qdata.get('list_id') == list_id]
            for qid in keys:
                questions[qid]['list_id'] = "unassigned"
                questions[qid]['status'] = "not asked"
                questions_moved += 1
        await ctx.send(success(f"Moved **{questions_moved}** questions to Unassigned."))

    @qotd_list_management.command(name="priority")
    async def qotd_list_priority_set(self, ctx: commands.Context, list_id: str, priority: int, start_date: str, end_date: str):
        """
        Sets a priority rule for a list.
        
        Usage: [p]qotd list priority <list_id> <priority_num> <start_MM-DD> <end_MM-DD>
        
        - Priority 1 is highest.
        - Priority 0 means DO NOT POST (Exclusion).
        - If no rule matches a date, the list defaults to Priority 999.
        """
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
        
        # Helper to validate MM-DD
        def validate_date(d_str):
            if len(d_str) != 5 or d_str[2] != '-': return False
            try:
                datetime.strptime(f"2024-{d_str}", "%Y-%m-%d") # Use leap year
                return True
            except ValueError:
                return False

        if not validate_date(start_date) or not validate_date(end_date):
             return await ctx.send(warning(f"Invalid dates. Use `MM-DD` format (e.g. `12-25`)."))

        l_dict = self._migrate_list_dict(lists_data[list_id])
        l_obj = QuestionList.model_validate(l_dict)
        
        # Create new rule
        new_rule = ListPriorityRule(priority=priority, start_md=start_date, end_md=end_date)
        
        # Check for existing rule with exact same range
        existing_index = -1
        for i, r in enumerate(l_obj.priority_rules):
            if r.start_md == start_date and r.end_md == end_date:
                existing_index = i
                break
        
        action_word = "Added"
        if existing_index != -1:
            l_obj.priority_rules[existing_index] = new_rule
            action_word = "Updated"
        else:
            l_obj.priority_rules.append(new_rule)
        
        async with self.config.lists() as lists:
            lists[list_id] = l_obj.model_dump()
            
        p_text = f"Priority {priority}" if priority > 0 else "Exclusion (0)"
        await ctx.send(success(f"{action_word} rule for **{l_obj.name}**: {start_date} to {end_date} = **{p_text}**."))

    @qotd_list_management.command(name="resetpriorities")
    async def qotd_list_priority_reset(self, ctx: commands.Context, list_id: str):
        """Removes ALL priority/exclusion rules for a list."""
        lists_data = await self.config.lists()
        if list_id not in lists_data: return await ctx.send(warning("List not found."))
        
        async with self.config.lists() as lists:
            l_dict = self._migrate_list_dict(lists[list_id])
            l_dict['priority_rules'] = []
            lists[list_id] = l_dict
            
        await ctx.send(success(f"Reset all priorities for list `{list_id}`."))

    @qotd.group(name="schedule")
    async def qotd_schedule_management(self, ctx: commands.Context):
        """Manage QOTD schedules."""
        pass

    @qotd_schedule_management.command(name="add")
    async def qotd_schedule_add(self, ctx: commands.Context, name: str, channel: discord.TextChannel, frequency: str, post_time: Optional[str] = None, start_date: Optional[str] = None):
        """
        Adds a new schedule.
        
        Args:
            name: A unique name for this schedule (e.g. "Main").
            channel: The channel to post in.
            frequency: e.g., "1 day", "12 hours".
            post_time: Optional HH:MM UTC time.
            start_date: Optional start date in YYYY-MM-DD format.
        """
        schedules_data = await self.config.schedules()
        if name in schedules_data:
            return await ctx.send(warning(f"A schedule named **{name}** already exists."))

        schedule_id = name 
        now_utc = datetime.now(timezone.utc)
        
        # Parse frequency
        try:
            time_unit = frequency.split()
            if len(time_unit) != 2: raise ValueError
            amount = int(time_unit[0])
            unit = time_unit[1].lower().rstrip('s')
            if unit not in ('minute', 'hour', 'day', 'week'): raise ValueError
        except (ValueError, IndexError):
            return await ctx.send(warning("Invalid frequency format. Must be like '1 day' or '3 hours'."))
        
        # Determine anchor/start
        anchor_dt = None
        if start_date:
            try:
                # Basic YYYY-MM-DD parsing
                parsed_date = datetime.strptime(start_date, "%Y-%m-%d")
                
                # Combine with post_time if exists
                if post_time:
                    try:
                        ph, pm = map(int, post_time.split(':'))
                        parsed_date = parsed_date.replace(hour=ph, minute=pm)
                    except ValueError:
                        return await ctx.send(warning("Invalid post_time format. Use HH:MM."))
                
                # Set timezone to UTC
                anchor_dt = parsed_date.replace(tzinfo=timezone.utc)
                
            except ValueError:
                return await ctx.send(warning("Invalid start_date format. Use YYYY-MM-DD."))

        # Initial Run Calculation
        initial_next_run = now_utc
        if anchor_dt:
            # If start date is in future, that is the first run
            if anchor_dt > now_utc:
                initial_next_run = anchor_dt
            else:
                # If start date is in past, calculate next slot
                temp_sched = Schedule(id=schedule_id, channel_id=channel.id, frequency=frequency, post_time=post_time, next_run_time=now_utc, start_date=anchor_dt)
                initial_next_run = self._calculate_next_run_time(temp_sched, now_utc)
        else:
            # Standard logic without start_date anchor
            temp_sched = Schedule(id=schedule_id, channel_id=channel.id, frequency=frequency, post_time=post_time, next_run_time=now_utc)
            initial_next_run = self._calculate_next_run_time(temp_sched, now_utc - timedelta(minutes=1))

        # Create final schedule
        new_schedule = Schedule(
            id=schedule_id, 
            channel_id=channel.id, 
            frequency=frequency, 
            post_time=post_time, 
            next_run_time=initial_next_run,
            start_date=anchor_dt
        )
        
        serialized_schedule = json.loads(new_schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
        
        msg = f"Added new schedule **{name}** in {channel.mention}.\nNext run: {discord.utils.format_dt(initial_next_run, 'f')} ({discord.utils.format_dt(initial_next_run, 'R')})."
        if anchor_dt:
             msg += f"\nAnchored to start date: {start_date}."
             
        await ctx.send(msg + f"\n\n⚠️ **Important:** This schedule has no lists linked! Use `{ctx.prefix}qotd schedule link {name} <list_id>` to add some.")

    @qotd_schedule_management.command(name="remove")
    async def qotd_schedule_remove(self, ctx: commands.Context, schedule_id: str):
        """Removes a schedule."""
        schedules_data = await self.config.schedules()
        if schedule_id not in schedules_data: return await ctx.send(warning(f"Schedule ID `{schedule_id}` not found."))
        async with self.config.schedules() as schedules:
            del schedules[schedule_id]
        await ctx.send(f"Successfully removed schedule **`{schedule_id}`**.")

    @qotd_schedule_management.command(name="link")
    async def qotd_schedule_link(self, ctx: commands.Context, schedule_id: str, list_id: str):
        """
        Links a question list to a schedule.
        The schedule will pull questions from this list when it runs.
        """
        schedules_data = await self.config.schedules()
        lists_data = await self.config.lists()

        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule `{schedule_id}` not found."))
        if list_id not in lists_data:
            return await ctx.send(warning(f"List `{list_id}` not found."))
            
        async with self.config.schedules() as schedules:
            schedule_dict = schedules[schedule_id]
            
            # Ensure list exists in dict
            if 'lists' not in schedule_dict:
                schedule_dict['lists'] = []
            
            if list_id in schedule_dict['lists']:
                return await ctx.send(warning(f"List `{list_id}` is already linked to schedule `{schedule_id}`."))
                
            schedule_dict['lists'].append(list_id)
            schedules[schedule_id] = schedule_dict
            
        await ctx.send(success(f"Linked list **{lists_data[list_id]['name']}** to schedule **{schedule_id}**."))

    @qotd_schedule_management.command(name="unlink")
    async def qotd_schedule_unlink(self, ctx: commands.Context, schedule_id: str, list_id: str):
        """
        Unlinks a question list from a schedule.
        """
        schedules_data = await self.config.schedules()

        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule `{schedule_id}` not found."))
            
        async with self.config.schedules() as schedules:
            schedule_dict = schedules[schedule_id]
            
            if 'lists' not in schedule_dict or list_id not in schedule_dict['lists']:
                return await ctx.send(warning(f"List `{list_id}` is not linked to schedule `{schedule_id}`."))
                
            schedule_dict['lists'].remove(list_id)
            schedules[schedule_id] = schedule_dict
            
        await ctx.send(success(f"Unlinked list `{list_id}` from schedule **{schedule_id}**."))

    @qotd_schedule_management.command(name="list")
    async def qotd_schedule_list(self, ctx: commands.Context):
        """Lists all active schedules."""
        await self.qotd_schedule_view(ctx) 

    @qotd_schedule_management.command(name="view")
    async def qotd_schedule_view(self, ctx: commands.Context):
        """Displays all configured schedules."""
        await self.qotd_set_view(ctx)

    @qotd_schedule_management.command(name="upcoming")
    async def qotd_schedule_upcoming(self, ctx: commands.Context):
        """Shows the projected schedule for the next 7 days."""
        schedules_data = await self.config.schedules()
        lists_data = await self.config.lists()
        
        if not schedules_data:
            return await ctx.send("No schedules configured.")

        events = []
        now_utc = datetime.now(timezone.utc)
        end_time = now_utc + timedelta(days=7)
        
        for schedule_id, schedule_dict in schedules_data.items():
            try:
                if isinstance(schedule_dict.get('next_run_time'), str):
                    schedule_dict['next_run_time'] = datetime.fromisoformat(schedule_dict['next_run_time'])
                
                if schedule_dict['next_run_time'].tzinfo is None:
                    schedule_dict['next_run_time'] = schedule_dict['next_run_time'].replace(tzinfo=timezone.utc)

                # Validation
                schedule = Schedule.model_validate(schedule_dict)

            except (ValidationError, ValueError):
                continue
                
            if not schedule.lists: continue

            sim_time = schedule.next_run_time
            safety_count = 0 
            
            while sim_time <= end_time and safety_count < 1000:
                safety_count += 1
                
                if sim_time > now_utc:
                    # Logic to find which list would win
                    best_list = "None"
                    best_prio = 999
                    
                    # Simulation Logic: Check only linked lists
                    linked_lists = schedule.lists
                    
                    for l_id in linked_lists:
                        if l_id not in lists_data: continue
                        
                        try:
                            l_data = self._migrate_list_dict(lists_data[l_id])
                            l_obj = QuestionList.model_validate(l_data)
                            
                            has_match = False
                            prio = 999
                            for rule in l_obj.priority_rules:
                                current_md = sim_time.strftime("%m-%d")
                                if rule.start_md <= rule.end_md:
                                    if rule.start_md <= current_md <= rule.end_md: has_match = True
                                else:
                                    if current_md >= rule.start_md or current_md <= rule.end_md: has_match = True
                                
                                if has_match:
                                    if rule.priority == 0: prio = 0
                                    elif rule.priority < prio: prio = rule.priority
                                    break 
                            
                            if prio > 0:
                                if prio < best_prio:
                                    best_prio = prio
                                    best_list = l_obj.name
                                elif prio == best_prio and best_list == "None":
                                    best_list = l_obj.name
                        except: continue

                    if best_list != "None":
                        channel = self.bot.get_channel(schedule.channel_id)
                        channel_name = channel.mention if channel else f"<#{schedule.channel_id}>"
                        
                        events.append({
                            "time": sim_time,
                            "list": f"{best_list} (Prio {best_prio})",
                            "channel": channel_name,
                            "schedule_id": schedule_id
                        })
                
                next_sim = self._calculate_next_run_time(schedule, sim_time)
                if next_sim <= sim_time:
                    break
                sim_time = next_sim

        if not events:
            return await ctx.send("No posts scheduled for the next 7 days.")

        events.sort(key=lambda x: x['time'])
        
        embed = discord.Embed(title="📅 Upcoming QOTD Posts (Next 7 Days)", color=discord.Color.blue())
        
        description = ""
        for event in events:
            ts = discord.utils.format_dt(event['time'], "f")
            rel = discord.utils.format_dt(event['time'], "R")
            line = f"• {ts} ({rel})\n  **{event['list']}** in {event['channel']}\n"
            
            if len(description) + len(line) > 4000:
                description += "\n...and more."
                break
            description += line
            
        embed.description = description
        await ctx.send(embed=embed)

    @qotd.command(name="import")
    async def qotd_import(self, ctx: commands.Context, list_id: str):
        """Imports questions from JSON."""
        if not ctx.message.attachments: return await ctx.send(warning("Attach a JSON file."))
        lists_data = await self.config.lists()
        if list_id not in lists_data: return await ctx.send(warning("List ID not found."))
        file = ctx.message.attachments[0]
        if not file.filename.endswith('.json'): return await ctx.send(warning("Must be a JSON file."))
        try:
            file_data = await file.read()
            questions_list = json.loads(file_data.decode('utf-8'))
        except Exception as e: return await ctx.send(warning(f"Error parsing file: {e}"))
        
        if not isinstance(questions_list, list): return await ctx.send(warning("JSON must be a list."))

        imported = 0
        skipped = 0
        duplicates = 0
        IGNORED = ["2s5qal", "e8auv2"]
        
        async with self.config.questions() as global_questions:
            for item in questions_list:
                if not isinstance(item, dict): 
                    skipped += 1
                    continue
                imported_id = item.get("id")
                if imported_id and imported_id in global_questions:
                    duplicates += 1
                    continue
                
                q_text = item.get("question")
                if not q_text:
                    for k,v in item.items():
                        if k not in IGNORED and isinstance(v, str):
                            q_text = v
                            break
                if not q_text:
                    skipped += 1
                    continue

                final_id = imported_id if imported_id else str(uuid.uuid4())
                if final_id in global_questions: final_id = str(uuid.uuid4())

                suggested_by = item.get("suggested_by_id")
                if not isinstance(suggested_by, int): suggested_by = ctx.author.id
                
                added_on = datetime.now(timezone.utc)
                if isinstance(item.get("added_on"), str):
                    try: added_on = datetime.fromisoformat(item.get("added_on"))
                    except ValueError: pass
                
                new_q = QuestionData(id=final_id, question=q_text, suggested_by=suggested_by, list_id=list_id, status="not asked", added_on=added_on)
                global_questions[final_id] = json.loads(new_q.model_dump_json())
                imported += 1
        await ctx.send(f"Imported {imported}. Skipped {skipped}. Duplicates {duplicates}.")

    @qotd.command(name="export")
    async def qotd_export(self, ctx: commands.Context, list_id: str):
        """Exports questions to JSON."""
        lists_data = await self.config.lists()
        if list_id not in lists_data: return await ctx.send(warning("List ID not found."))
        all_q = await self.config.questions()
        list_name = lists_data.get(list_id, {}).get('name', 'Unknown')
        export_data = []
        export_data.append({"2s5qal": f"Export: {list_name}"})
        export_data.append({"e8auv2": f"List ID: {list_id}"})
        
        count = 0
        for qid, qdict in all_q.items():
            if qdict.get('list_id') == list_id:
                s_id = qdict.get('suggested_by')
                s_name = "System"
                if s_id:
                    u = self.bot.get_user(s_id)
                    if u: s_name = u.display_name
                    else: s_name = f"ID: {s_id}"
                
                export_data.append({
                    "id": qid,
                    "question": qdict.get('question'),
                    "suggested_by_id": s_id,
                    "suggested_by_name": s_name,
                    "added_on": qdict.get('added_on'),
                    "last_asked": qdict.get('last_asked')
                })
                count += 1
        
        if count == 0: return await ctx.send("List is empty.")
        
        filename = f"qotd_{list_id}.json"
        temp_dir = cog_data_path(self) / "exports"
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / filename
        
        try:
            with path.open("w", encoding="utf-8") as f: json.dump(export_data, f, indent=4)
            await ctx.send(f"Exported {count} questions.", file=discord.File(path))
        except Exception as e:
            await ctx.send(warning(f"Export failed: {e}"))
        finally:
            path.unlink(missing_ok=True)

    @commands.hybrid_command(name="suggestqotd")
    @commands.guild_only()
    async def suggest_qotd_command(self, ctx: commands.Context):
        """Suggest a question."""
        lists_data = await self.config.lists()
        list_names = [v['name'] for v in lists_data.values() if v['id'] not in ["suggestions", "unassigned"]]
        view = SuggestionButton(self, list_names)
        await ctx.send("Click below to suggest!", view=view)