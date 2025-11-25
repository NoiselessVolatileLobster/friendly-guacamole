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
from redbot.core import commands, Config, app_commands 
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_list, box, bold, warning, error, info, success
from red_commons.logging import getLogger
from pydantic import BaseModel, Field, ValidationError

# --- Pydantic Models (Defining here for self-contained structure as requested) ---

class ScheduleRule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()).split('-')[0])
    start_month_day: str
    end_month_day: str
    action: Literal["skip_run", "use_list"]
    list_id_override: Optional[str] = None 

class QuestionList(BaseModel):
    id: str
    name: str
    exclusion_dates: List[str] = Field(default_factory=list)

class Schedule(BaseModel):
    id: str
    list_id: str
    channel_id: int
    frequency: str
    post_time: Optional[str] = None 
    next_run_time: datetime
    rules: List[ScheduleRule] = Field(default_factory=list)

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
        new_q = QuestionData(
            question=str(self.question_text),
            suggested_by=interaction.user.id,
            list_id="suggestions", 
            status="pending",
        )

        try:
            await self.cog.add_question_to_data(new_q)
        except Exception as e:
            log.exception("Failed to add new question data.")
            return await interaction.response.send_message(f"Error saving question: {e}", ephemeral=True)

        embed = discord.Embed(
            title="✅ Question Submitted!",
            description=f"Your question has been added to the review queue.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SuggestionButton(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", list_names: List[str]):
        super().__init__(timeout=None)
        self.cog = cog
        self._list_names = list_names 

    @discord.ui.button(label="Suggest a Question", style=discord.ButtonStyle.primary, custom_id="qotd_suggest_button")
    async def suggest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        lists_data = await self.cog.config.lists()
        current_list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]
        await interaction.response.send_modal(SuggestionModal(self.cog, current_list_names))


class ApprovalView(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", question_data: QuestionData, question_id: str, lists: Dict[str, QuestionList]):
        super().__init__(timeout=300) 
        self.cog = cog
        self.question_data = question_data
        self.question_id = question_id
        self.lists = lists

        list_options = [
            discord.SelectOption(label=list_obj.name, value=list_id)
            for list_id, list_obj in lists.items() if list_id != "suggestions" 
        ]

        if list_options:
            self.list_select = discord.ui.Select(
                placeholder="Select a Question List to Approve Into",
                options=list_options,
                min_values=1,
                max_values=1,
                custom_id="qotd_approval_list_select"
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
        if interaction.user.guild_permissions.manage_guild:
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
        
        embed = interaction.message.embeds[0]
        embed.title = "✅ Question Approved!"
        embed.description = f"Approved by {interaction.user.display_name} into list: **{self.lists[selected_list_id].name}**"
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
            },
            "schedules": {}, 
            "approval_channel": None,
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

    # --- Loop ---

    @tasks.loop(minutes=1)
    async def qotd_poster(self):
        now_utc = datetime.now(timezone.utc)
        schedules_data = await self.config.schedules()

        for schedule_id, schedule_dict in schedules_data.items():
            schedule = None
            try:
                next_run_time_data = schedule_dict.get('next_run_time')
                if isinstance(next_run_time_data, str):
                    dt_obj = datetime.fromisoformat(next_run_time_data)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['next_run_time'] = dt_obj
                
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

    # --- Helper Methods ---

    def _is_date_active(self, start_md: str, end_md: str, check_date: datetime) -> bool:
        try:
            current_md = check_date.strftime("%m-%d")
            if start_md <= end_md:
                return start_md <= current_md <= end_md
            return current_md >= start_md or current_md <= end_md
        except ValueError:
            return False

    async def _get_active_list_id(self, schedule: Schedule, now_utc: datetime) -> Optional[str]:
        for rule in schedule.rules:
            try:
                if self._is_date_active(rule.start_month_day, rule.end_month_day, now_utc):
                    if rule.action == "skip_run":
                        return None
                    if rule.action == "use_list" and rule.list_id_override:
                        return rule.list_id_override
            except Exception:
                continue
        return schedule.list_id

    async def _post_scheduled_question(self, schedule_id: str, schedule: Schedule):
        now_utc = datetime.now(timezone.utc)
        target_list_id = await self._get_active_list_id(schedule, now_utc)
        
        if target_list_id is None:
            log.info(f"Schedule {schedule_id} skipped due to active rule.")
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return

        lists_data = await self.config.lists()
        try:
            target_list = QuestionList.model_validate(lists_data[target_list_id])
        except KeyError:
             log.warning(f"Target list {target_list_id} for schedule {schedule_id} not found.")
             await self._update_schedule_next_run(schedule_id, schedule, now_utc)
             return
        except ValidationError:
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return
            
        current_md = now_utc.strftime("%m-%d")
        if current_md in target_list.exclusion_dates:
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return

        questions_data = await self.config.questions()
        eligible_q_data = {
            qid: qdata
            for qid, qdata in questions_data.items()
            if qdata.get("list_id") == target_list_id and qdata.get("status") in ("not asked", "asked")
        }
        
        eligible_q = {}
        for qid, qdata in eligible_q_data.items():
            try:
                qdata['added_on'] = datetime.fromisoformat(qdata['added_on'])
                qdata['last_asked'] = datetime.fromisoformat(qdata['last_asked']) if qdata['last_asked'] else None
                qdata['id'] = qid
                eligible_q[qid] = QuestionData.model_validate(qdata)
            except (ValidationError, ValueError):
                continue

        if not eligible_q:
            log.info(f"No eligible questions for schedule {schedule_id}.")
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return

        not_asked = [q for q in eligible_q.values() if q.status == "not asked"]
        
        selected_qid = None
        if not_asked:
            selected_q = random.choice(not_asked)
            selected_qid = selected_q.id
        else:
            asked = list(eligible_q.items())
            def sort_key(item):
                qid, q = item
                if q.last_asked is None: 
                    return timedelta(days=3650).total_seconds() 
                if q.last_asked.tzinfo is None:
                    q.last_asked = q.last_asked.replace(tzinfo=timezone.utc)
                return (now_utc - q.last_asked).total_seconds()
            
            asked.sort(key=sort_key, reverse=False)
            top_5 = asked[:min(5, len(asked))]
            selected_qid, selected_q = random.choice(top_5)

        if not selected_qid:
             await self._update_schedule_next_run(schedule_id, schedule, now_utc)
             return

        channel = self.bot.get_channel(schedule.channel_id)
        if not channel:
            await self._update_schedule_next_run(schedule_id, schedule, now_utc)
            return
            
        embed = discord.Embed(title=f"❓ Question of the Day: {selected_q.question}", color=discord.Color.blue())
        if selected_q.suggested_by:
            user = self.bot.get_user(selected_q.suggested_by)
            suggested_by_text = user.display_name if user else f"User ID: {selected_q.suggested_by}"
            embed.set_footer(text=f"Suggested by {suggested_by_text}")

        try:
            await channel.send(embed=embed)
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
            return now_utc + timedelta(days=3650) 
            
        if schedule.post_time:
            try:
                hour, minute = map(int, schedule.post_time.split(':'))
                target_time_today = datetime.combine(now_utc.date(), time(hour, minute), tzinfo=timezone.utc)
            except ValueError:
                return last_run + delta 

            next_run = target_time_today
            while next_run <= last_run:
                next_run += delta
            if next_run <= now_utc and next_run == target_time_today:
                next_run += delta
            return next_run
        else:
            return last_run + delta

    async def _update_schedule_next_run(self, schedule_id: str, schedule: Schedule, last_run: datetime):
        schedule.next_run_time = self._calculate_next_run_time(schedule, last_run)
        serialized_schedule = json.loads(schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule

    # --- Data Ops ---

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
                
    async def remove_question_from_list(self, list_id: str, qid: str):
        pass 
        
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

    # --- Listeners ---
    
    @commands.Cog.listener()
    async def on_ready(self):
        lists_data = await self.config.lists()
        list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]
        self.bot.add_view(SuggestionButton(self, list_names))
        
    # --- Commands ---

    @commands.guild_only()
    @commands.admin()
    @commands.group(name="qotd", aliases=["qotdd"])
    async def qotd(self, ctx: commands.Context):
        """Base command for Question of the Day administration."""
        pass

    @qotd.command(name="configchannel")
    async def qotd_config_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where new question suggestions are posted for approval."""
        await self.config.approval_channel.set(channel.id)
        await ctx.send(f"Approval channel set to {channel.mention}.")
        
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
        if list_id not in lists_data or list_id == 'suggestions':
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

    # --- Question Management Group (NEW) ---

    @qotd.group(name="question")
    async def qotd_question_management(self, ctx: commands.Context):
        """Manage individual questions (View or Delete)."""
        pass

    @qotd_question_management.command(name="view")
    async def qotd_question_view(self, ctx: commands.Context, question_id: str):
        """
        View details of a specific question.
        
        You can use the full UUID or the short ID (first 8 characters).
        """
        all_questions = await self.config.questions()
        
        # Find match by short or long ID
        matched_qid = next((qid for qid in all_questions if qid.startswith(question_id)), None)
        
        if not matched_qid:
            return await ctx.send(warning(f"Question with ID `{question_id}` not found."))
            
        qdata = all_questions[matched_qid]
        
        try:
            # Deserialize datetime fields for display
            if isinstance(qdata.get('added_on'), str):
                qdata['added_on'] = datetime.fromisoformat(qdata['added_on'])
            if isinstance(qdata.get('last_asked'), str):
                qdata['last_asked'] = datetime.fromisoformat(qdata['last_asked'])
            
            question = QuestionData.model_validate(qdata)
        except (ValidationError, ValueError):
            return await ctx.send(warning(f"Question data for `{matched_qid}` is corrupt."))

        # Get list name
        lists_data = await self.config.lists()
        list_name = "Unknown List"
        if question.list_id in lists_data:
            list_name = lists_data[question.list_id]['name']
        elif question.list_id == 'suggestions':
            list_name = "Pending Suggestions"

        # Get suggester name
        suggested_by_str = "System/Unknown"
        if question.suggested_by:
            user = self.bot.get_user(question.suggested_by)
            if user:
                suggested_by_str = f"{user.mention} ({user.id})"
            else:
                suggested_by_str = f"ID: {question.suggested_by}"

        short_id = matched_qid.split('-')[0]
        embed = discord.Embed(title=f"Question Details (ID: {short_id})", color=discord.Color.blue())
        embed.description = box(question.question, lang="text")
        
        embed.add_field(name="List", value=f"{list_name} (`{question.list_id}`)", inline=True)
        embed.add_field(name="Status", value=question.status.title(), inline=True)
        embed.add_field(name="Suggested By", value=suggested_by_str, inline=False)
        
        # Dates
        if question.added_on.tzinfo is None:
             question.added_on = question.added_on.replace(tzinfo=timezone.utc)
        
        added_ts = discord.utils.format_dt(question.added_on, 'f') + f" ({discord.utils.format_dt(question.added_on, 'R')})"
        embed.add_field(name="Created On", value=added_ts, inline=False)
        
        if question.last_asked:
            if question.last_asked.tzinfo is None:
                question.last_asked = question.last_asked.replace(tzinfo=timezone.utc)
            asked_ts = discord.utils.format_dt(question.last_asked, 'f') + f" ({discord.utils.format_dt(question.last_asked, 'R')})"
            embed.add_field(name="Last Asked", value=asked_ts, inline=False)
        else:
            embed.add_field(name="Last Asked", value="Never", inline=False)
            
        await ctx.send(embed=embed)

    @qotd_question_management.command(name="remove")
    async def qotd_question_remove(self, ctx: commands.Context, question_id: str):
        """
        Permanently delete a specific question.
        
        You can use the full UUID or the short ID.
        """
        all_questions = await self.config.questions()
        
        # Find match by short or long ID
        matched_qid = next((qid for qid in all_questions if qid.startswith(question_id)), None)
        
        if not matched_qid:
            return await ctx.send(warning(f"Question with ID `{question_id}` not found."))
            
        await self.delete_question_by_id(matched_qid)
        await ctx.send(success(f"Question `{matched_qid.split('-')[0]}` has been permanently deleted."))

    @qotd.group(name="list")
    async def qotd_list_management(self, ctx: commands.Context):
        """Manage Question Lists."""
        pass
    
    @qotd_list_management.command(name="add")
    async def qotd_list_add(self, ctx: commands.Context, list_name: str):
        """Adds a new question list."""
        list_id = str(uuid.uuid4()).split('-')[0] 
        new_list = QuestionList(id=list_id, name=list_name)
        async with self.config.lists() as lists:
            if any(l.get('name', '').lower() == list_name.lower() for l in lists.values()):
                 return await ctx.send(warning(f"A list named **{list_name}** already exists."))
            lists[list_id] = new_list.model_dump() 
        await ctx.send(f"Added new question list: **{list_name}** (ID: `{list_id}`).")

    @qotd_list_management.command(name="view")
    async def qotd_list_view(self, ctx: commands.Context):
        """Displays all available question lists."""
        lists_data = await self.config.lists()
        all_questions = await self.config.questions()
        if not lists_data:
            return await ctx.send("No question lists defined.")
        embed = discord.Embed(title="📋 Configured Question Lists", color=discord.Color.gold())
        for list_id, list_dict in lists_data.items():
            try:
                list_obj = QuestionList.model_validate(list_dict)
            except ValidationError:
                continue
            count = sum(1 for q in all_questions.values() if q.get('list_id') == list_id)
            list_info = f"**Questions:** {count}\n"
            if list_obj.exclusion_dates:
                dates = sorted(list_obj.exclusion_dates)
                date_str = humanize_list([f"`{d}`" for d in dates[:5]])
                if len(dates) > 5: date_str += f", and {len(dates) - 5} more..."
                list_info += f"**Exclusions:** {date_str}"
            else: list_info += "**Exclusions:** None"
            if list_id == "suggestions": list_info = f"**Status:** Pending Approval Queue\n**Questions:** {count}"
            embed.add_field(name=f"{'🗃️ ' if list_id != 'suggestions' else '📩 '} {list_obj.name} (`{list_id}`)", value=list_info, inline=False)
        await ctx.send(embed=embed)

    @qotd_list_management.command(name="clear")
    async def qotd_list_clear(self, ctx: commands.Context, list_id: str):
        """Clears all questions from a specific list."""
        lists_data = await self.config.lists()
        
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found. Use `[p]qotd list view` to see available lists."))
            
        list_name = lists_data[list_id]['name']
        
        # Ask for confirmation
        confirm_msg = await ctx.send(warning(f"⚠️ **WARNING** ⚠️\nAre you sure you want to delete **ALL** questions from the list **{list_name}** (`{list_id}`)?\nThis action cannot be undone. Type `yes` to confirm."))
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"
            
        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return await ctx.send("Deletion cancelled (timeout).")
            
        # Proceed with deletion
        async with self.config.questions() as questions:
            # Find all keys (question IDs) that belong to this list
            keys_to_delete = [qid for qid, qdata in questions.items() if qdata.get('list_id') == list_id]
            
            for qid in keys_to_delete:
                del questions[qid]
                
        await ctx.send(success(f"Successfully deleted **{len(keys_to_delete)}** questions from list **{list_name}**."))

    @qotd_list_management.group(name="rule")
    async def qotd_list_rule(self, ctx: commands.Context):
        """Manage list-specific exclusion rules."""
        pass
        
    @qotd_list_rule.command(name="addexclusion")
    async def qotd_list_rule_add(self, ctx: commands.Context, list_id: str, month_day: str):
        """Adds a single day (MM-DD) when this list should NOT be used."""
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
        if not month_day.strip().replace('-', '').isdigit() or len(month_day) != 5 or month_day[2] != '-':
            return await ctx.send(warning("Date format must be `MM-DD`."))
        try:
            list_obj = QuestionList.model_validate(lists_data[list_id])
        except ValidationError:
            return await ctx.send(warning(f"List data for `{list_id}` is invalid."))
        if month_day in list_obj.exclusion_dates:
            return await ctx.send(warning(f"Date **{month_day}** is already an exclusion."))
        list_obj.exclusion_dates.append(month_day)
        async with self.config.lists() as lists:
            lists[list_id] = list_obj.model_dump()
        await ctx.send(f"Added exclusion date **{month_day}** to list **{list_obj.name}**.")

    @qotd_list_rule.command(name="removeexclusion")
    async def qotd_list_rule_remove(self, ctx: commands.Context, list_id: str, month_day: str):
        """Removes a single exclusion day."""
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
        try:
            list_obj = QuestionList.model_validate(lists_data[list_id])
        except ValidationError:
            return await ctx.send(warning(f"List data for `{list_id}` is invalid."))
        if month_day not in list_obj.exclusion_dates:
            return await ctx.send(warning(f"Date **{month_day}** is not an exclusion."))
        list_obj.exclusion_dates.remove(month_day)
        async with self.config.lists() as lists:
            lists[list_id] = list_obj.model_dump()
        await ctx.send(f"Removed exclusion date **{month_day}** from list **{list_obj.name}**.")

    @qotd.group(name="schedule")
    async def qotd_schedule_management(self, ctx: commands.Context):
        """Manage QOTD schedules."""
        pass

    @qotd_schedule_management.command(name="add")
    async def qotd_schedule_add(self, ctx: commands.Context, list_id: str, channel: discord.TextChannel, frequency: str, post_time: Optional[str] = None):
        """Adds a new schedule."""
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
        schedule_id = str(uuid.uuid4()).split('-')[0]
        now_utc = datetime.now(timezone.utc)
        try:
            time_unit = frequency.split()
            if len(time_unit) != 2: raise ValueError
            amount = int(time_unit[0])
            unit = time_unit[1].lower().rstrip('s')
            if unit not in ('minute', 'hour', 'day', 'week'): raise ValueError
        except (ValueError, IndexError):
            return await ctx.send(warning("Invalid frequency format."))
        temp_schedule = Schedule(id=schedule_id, list_id=list_id, channel_id=channel.id, frequency=frequency, post_time=post_time, next_run_time=now_utc)
        next_run_time = self._calculate_next_run_time(temp_schedule, now_utc - timedelta(minutes=1)) 
        new_schedule = Schedule(id=schedule_id, list_id=list_id, channel_id=channel.id, frequency=frequency, post_time=post_time, next_run_time=next_run_time)
        serialized_schedule = json.loads(new_schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
        time_str = f"at **{post_time} UTC**" if post_time else ""
        await ctx.send(f"Added new schedule (ID: `{schedule_id}`). Next run: {discord.utils.format_dt(next_run_time, 'R')}.")
        
    @qotd_schedule_management.command(name="remove")
    async def qotd_schedule_remove(self, ctx: commands.Context, schedule_id: str):
        """Removes a schedule."""
        schedules_data = await self.config.schedules()
        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule ID `{schedule_id}` not found."))
        async with self.config.schedules() as schedules:
            del schedules[schedule_id]
        await ctx.send(f"Successfully removed schedule **`{schedule_id}`**.")

    @qotd_schedule_management.command(name="view")
    async def qotd_schedule_view(self, ctx: commands.Context):
        """Displays all configured schedules."""
        schedules_data = await self.config.schedules()
        lists_data = await self.config.lists()
        if not schedules_data: return await ctx.send("No schedules currently configured.")
        embed = discord.Embed(title="🗓️ Configured QOTD Schedules", color=discord.Color.blue())
        for schedule_id, schedule_dict in schedules_data.items():
            try:
                next_run_time_data = schedule_dict.get('next_run_time')
                if isinstance(next_run_time_data, str):
                    dt_obj = datetime.fromisoformat(next_run_time_data)
                    if dt_obj.tzinfo is None: dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['next_run_time'] = dt_obj
                schedule = Schedule.model_validate(schedule_dict)
            except (ValidationError, ValueError):
                embed.add_field(name=f"ID: `{schedule_id}`", value="Corrupt Data", inline=False)
                continue
            list_name = lists_data.get(schedule.list_id, {}).get('name', 'UNKNOWN LIST')
            channel = self.bot.get_channel(schedule.channel_id)
            channel_mention = channel.mention if channel else f"ID: {schedule.channel_id} (Missing)"
            run_time = schedule.next_run_time
            if run_time.tzinfo is None: run_time = run_time.replace(tzinfo=timezone.utc)
            next_run_str = discord.utils.format_dt(run_time, "R")
            time_info = f" at **{schedule.post_time} UTC**" if schedule.post_time else ""
            field_value = f"**List:** `{list_name}`\n**Channel:** {channel_mention}\n**Frequency:** `{schedule.frequency}`{time_info}\n**Next Run:** {next_run_str}"
            embed.add_field(name=f"Schedule ID: `{schedule_id}`", value=field_value, inline=False)
        await ctx.send(embed=embed)

    @qotd_schedule_management.group(name="rule")
    async def qotd_schedule_rule(self, ctx: commands.Context):
        """Manage date-based rules."""
        pass

    @qotd_schedule_rule.command(name="addpriority")
    async def qotd_schedule_rule_add_priority(self, ctx: commands.Context, schedule_id: str, list_id: str, start_date: str, end_date: str):
        """Adds a priority rule."""
        schedules_data = await self.config.schedules()
        if schedule_id not in schedules_data: return await ctx.send(warning("Schedule ID not found."))
        schedule_dict = schedules_data[schedule_id]
        schedule_dict['next_run_time'] = datetime.fromisoformat(schedule_dict['next_run_time'])
        schedule = Schedule.model_validate(schedule_dict)
        new_rule = ScheduleRule(start_month_day=start_date, end_month_day=end_date, action="use_list", list_id_override=list_id)
        schedule.rules.append(new_rule)
        serialized_schedule = json.loads(schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
        await ctx.send(f"Added priority rule.")

    @qotd_schedule_rule.command(name="addskip")
    async def qotd_schedule_rule_add_skip(self, ctx: commands.Context, schedule_id: str, start_date: str, end_date: str):
        """Adds a skip rule."""
        schedules_data = await self.config.schedules()
        if schedule_id not in schedules_data: return await ctx.send(warning("Schedule ID not found."))
        schedule_dict = schedules_data[schedule_id]
        schedule_dict['next_run_time'] = datetime.fromisoformat(schedule_dict['next_run_time'])
        schedule = Schedule.model_validate(schedule_dict)
        new_rule = ScheduleRule(start_month_day=start_date, end_month_day=end_date, action="skip_run")
        schedule.rules.append(new_rule)
        serialized_schedule = json.loads(schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
        await ctx.send(f"Added skip rule.")

    @qotd_schedule_rule.command(name="removerule")
    async def qotd_schedule_rule_remove(self, ctx: commands.Context, schedule_id: str, rule_id: str):
        """Removes a rule."""
        schedules_data = await self.config.schedules()
        if schedule_id not in schedules_data: return await ctx.send(warning("Schedule ID not found."))
        schedule_dict = schedules_data[schedule_id]
        schedule_dict['next_run_time'] = datetime.fromisoformat(schedule_dict['next_run_time'])
        schedule = Schedule.model_validate(schedule_dict)
        schedule.rules = [rule for rule in schedule.rules if rule.id != rule_id]
        serialized_schedule = json.loads(schedule.model_dump_json())
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
        await ctx.send(f"Removed rule.")

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
        list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]
        view = SuggestionButton(self, list_names)
        await ctx.send("Click below to suggest!", view=view)