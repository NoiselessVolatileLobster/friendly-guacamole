import asyncio
import json # Ensure json is imported for the double-serialization fix
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Set, Union
from pathlib import Path

import discord
from discord.ext import tasks 
from redbot.core import commands, Config, app_commands 
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list, box, bold, warning
from red_commons.logging import getLogger
from pydantic import BaseModel, Field, ValidationError

from .config import QuestionList, QuestionData, Schedule, ScheduleRule

log = getLogger("red.qotd")

# --- Custom Views/Modals for Suggestion/Approval ---

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
        # Store suggestion in the "suggestions" list (or a default list for review)
        new_q = QuestionData(
            question=str(self.question_text),
            suggested_by=interaction.user.id,
            list_id="suggestions", # Temp list for approval
            added_on=datetime.now(timezone.utc),
            status="pending",
        )

        try:
            # This calls add_question_to_data, which now uses the guaranteed serialization method
            await self.cog.add_question_to_data(new_q)
        except Exception as e:
            # Catch the error here for immediate user feedback
            log.exception("Failed to add new question data.")
            # Note: This specific warning might still show if the serialization fails at a deeper level, 
            # but the code should now prevent it.
            return await interaction.response.send_message(f"An error occurred while saving: Object of type datetime is not JSON serializable. This usually means a Pydantic model was not correctly serialized. Error: {e}", ephemeral=True)


        embed = discord.Embed(
            title="✅ Question Submitted!",
            description=f"Your question has been added to the review queue.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SuggestionButton(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", list_names: List[str]):
        # Set a fixed custom_id for persistent views
        super().__init__(timeout=None)
        self.cog = cog
        self._list_names = list_names # Store privately

    @discord.ui.button(label="Suggest a Question", style=discord.ButtonStyle.primary, custom_id="qotd_suggest_button")
    async def suggest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Retrieve the most current list names here for the modal, rather than relying on the init value
        lists_data = await self.cog.config.lists()
        current_list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]

        # Open the modal
        await interaction.response.send_modal(SuggestionModal(self.cog, current_list_names))


class ApprovalView(discord.ui.View):
    def __init__(self, cog: "QuestionOfTheDay", question_data: QuestionData, question_id: str, lists: Dict[str, QuestionList]):
        # Timeout set higher than default interaction timeout, but lower than the default persistent view timeout
        super().__init__(timeout=300) 
        self.cog = cog
        self.question_data = question_data
        self.question_id = question_id
        self.lists = lists

        # Dropdown for selecting the list to approve into
        list_options = [
            discord.SelectOption(label=list_obj.name, value=list_id)
            for list_id, list_obj in lists.items() if list_id != "suggestions" # Don't allow approving into the suggestions list
        ]

        self.list_select = discord.ui.Select(
            placeholder="Select a Question List to Approve Into",
            options=list_options,
            min_values=1,
            max_values=1,
            custom_id="qotd_approval_list_select"
        )
        self.list_select.callback = self.approve_callback
        self.add_item(self.list_select)

    async def approval_check(self, interaction: discord.Interaction) -> bool:
        # Check for manage_guild permission
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This must be run in a guild.", ephemeral=True)
            return False

        if interaction.user.guild_permissions.manage_guild:
             return True

        await interaction.response.send_message("You do not have permission to approve questions (requires Manage Server).", ephemeral=True)
        return False

    async def approve_callback(self, interaction: discord.Interaction):
        if not await self.approval_check(interaction):
            return

        selected_list_id = self.list_select.values[0]

        # Update the question's list_id and status
        self.question_data.list_id = selected_list_id
        self.question_data.status = "not asked"
        # Refresh 'added on' date upon approval and ensure it's timezone aware
        self.question_data.added_on = datetime.now(timezone.utc) 

        await self.cog.update_question_data(self.question_id, self.question_data)
        await self.cog.remove_question_from_list("suggestions", self.question_id)

        # Update the approval message
        embed = interaction.message.embeds[0]
        embed.title = "✅ Question Approved!"
        embed.description = f"Approved by {interaction.user.display_name} into list: **{self.lists[selected_list_id].name}**"
        embed.color = discord.Color.green()

        # Remove the view
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="qotd_reject_button")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.approval_check(interaction):
            return

        # Delete the question
        await self.cog.delete_question_by_id(self.question_id)

        embed = interaction.message.embeds[0]
        embed.title = "❌ Question Rejected!"
        embed.description = f"Rejected by {interaction.user.display_name}. Question deleted."
        embed.color = discord.Color.red()

        await interaction.response.edit_message(embed=embed, view=None)


# --- Cog Class ---

class QuestionOfTheDay(commands.Cog):
    """
    Manages and posts scheduled Questions of the Day (QOTD).
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Use Pydantic models for structured configuration
        self.config = Config.get_conf(self, identifier=6942069, force_registration=True)
        default_global = {
            "questions": {}, # key=UUID/ID, value=QuestionData dict
            "lists": {
                # Ensure defaults are fully serialized dicts, not Pydantic objects, for Config
                "general": QuestionList(id="general", name="General Questions").model_dump(),
                "suggestions": QuestionList(id="suggestions", name="Pending Suggestions").model_dump(),
            },
            "schedules": {}, # key=UUID/ID, value=Schedule dict
            "approval_channel": None, # Channel to post suggestions for approval
        }
        self.config.register_global(**default_global)

        self.qotd_poster.start()
        # Initial view registration (list_names is empty, will be updated on_ready)
        self.bot.add_view(SuggestionButton(self, [])) 

    def cog_unload(self):
        self.qotd_poster.cancel()
        self.bot.remove_view("qotd_suggest_button") 

    async def red_delete_data_for_user(self, *, requester: Literal["discord", "owner", "admin", "user"], user_id: int):
        """
        Deletes suggestion data for a user.
        """
        async with self.config.questions() as questions:
            keys_to_delete = [
                qid for qid, qdata in questions.items()
                if qdata.get("suggested_by") == user_id
            ]
            for key in keys_to_delete:
                del questions[key]

    @tasks.loop(minutes=1)
    async def qotd_poster(self):
        """Checks schedules and posts questions."""
        now_utc = datetime.now(timezone.utc)
        schedules_data = await self.config.schedules()

        for schedule_id, schedule_dict in schedules_data.items():
            try:
                # FIX: Check for string data and parse it back to a timezone-aware datetime object
                next_run_time_data = schedule_dict.get('next_run_time')
                if isinstance(next_run_time_data, str):
                    dt_obj = datetime.fromisoformat(next_run_time_data)
                    if dt_obj.tzinfo is None:
                        # If the string was saved without timezone info, force it to UTC
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['next_run_time'] = dt_obj

                schedule = Schedule.model_validate(schedule_dict)
            except ValidationError as e:
                log.error(f"Failed to validate schedule {schedule_id}: {e}")
                continue # Skip invalid schedule
            except ValueError as e:
                log.error(f"Failed to parse datetime string for schedule {schedule_id}: {e}")
                continue # Skip schedule if datetime parsing fails

            # Check if it's time to run based on frequency
            if now_utc >= schedule.next_run_time:
                # Catch any unexpected serialization errors during the posting process
                try:
                    await self._post_scheduled_question(schedule_id, schedule)
                except Exception as e:
                    log.exception(f"Critical error during _post_scheduled_question for {schedule_id}: {e}")
                    # If posting fails, still try to update the next run time to prevent spamming
                    await self._update_schedule_next_run(schedule_id, schedule) 

    # --- New Helper Methods for Rules ---

    def _is_date_active(self, start_md: str, end_md: str, check_date: datetime) -> bool:
        """Checks if the current MM-DD falls within the start/end MM-DD range, ignoring year."""
        try:
            current_md = check_date.strftime("%m-%d")
            
            # Simple lexicographical comparison works for non-crossing years (e.g., 03-01 to 04-30)
            if start_md <= end_md:
                return start_md <= current_md <= end_md
            
            # Handles rules that cross year boundary (e.g., 12-01 to 01-31)
            return current_md >= start_md or current_md <= end_md
            
        except ValueError:
            log.warning(f"Invalid date format in rule: {start_md} or {end_md}")
            return False


    async def _get_active_list_id(self, schedule: Schedule, now_utc: datetime) -> Optional[str]:
        """Checks schedule rules and returns the list_id to use, or None if the run should be skipped."""
        
        # 1. Check for schedule rules (priority or skip)
        for rule in schedule.rules:
            # Validate rule dates before using them
            try:
                if self._is_date_active(rule.start_month_day, rule.end_month_day, now_utc):
                    if rule.action == "skip_run":
                        return None  # Skip the execution entirely
                    
                    if rule.action == "use_list" and rule.list_id_override:
                        # Priority override: Use this list instead of the default
                        return rule.list_id_override
            except Exception:
                log.error(f"Error processing schedule rule {rule.id} for schedule {schedule.id}.")
                continue
                    
        # 2. If no override, use the schedule's default list
        return schedule.list_id


    async def _post_scheduled_question(self, schedule_id: str, schedule: Schedule):
        """Internal function to handle question selection and posting."""
        now_utc = datetime.now(timezone.utc)
        
        # 1. Determine the list_id to use, and check if the schedule should be skipped
        target_list_id = await self._get_active_list_id(schedule, now_utc)
        if target_list_id is None:
            log.info(f"Schedule {schedule_id} is set to skip posting on {now_utc.strftime('%m-%d')} due to an active rule.")
            await self._update_schedule_next_run(schedule_id, schedule)
            return

        # 2. Check for list-specific date exclusions (e.g., "don't ask List X on Feb 14")
        lists_data = await self.config.lists()
        try:
            target_list = QuestionList.model_validate(lists_data[target_list_id])
        except KeyError:
             log.warning(f"Target list {target_list_id} for schedule {schedule_id} not found.")
             await self._update_schedule_next_run(schedule_id, schedule)
             return
        except ValidationError:
            log.error(f"Target list {target_list_id} validation failed.")
            await self._update_schedule_next_run(schedule_id, schedule)
            return
            
        current_md = now_utc.strftime("%m-%d")
        if current_md in target_list.exclusion_dates:
            log.info(f"Skipping schedule {schedule_id}: Target list '{target_list.name}' is excluded on {current_md}.")
            await self._update_schedule_next_run(schedule_id, schedule)
            return

        # 3. Select the question (Selection logic remains the same)
        questions_data = await self.config.questions()
        
        # Filter questions for the target list and status 'not asked' or 'asked'
        eligible_q_data = {
            qid: qdata
            for qid, qdata in questions_data.items()
            if qdata.get("list_id") == target_list_id and qdata.get("status") in ("not asked", "asked")
        }
        
        eligible_q = {}
        for qid, qdata in eligible_q_data.items():
            try:
                # Use strict validation here to prevent using corrupt data
                eligible_q[qid] = QuestionData.model_validate(qdata)
            except ValidationError:
                log.warning(f"Skipping invalid QuestionData for QID {qid} in list {target_list_id}.")
                continue

        if not eligible_q:
            list_name = target_list.name
            log.info(f"No eligible questions found for list {list_name} in schedule {schedule_id}.")
            await self._update_schedule_next_run(schedule_id, schedule)
            return

        # Prioritize 'not asked'
        not_asked = [q for q in eligible_q.values() if q.status == "not asked"]
        
        if not_asked:
            selected_q = random.choice(not_asked)
            # Find the QID matching the selected question data
            # Use tuple comparison (question text and list ID) for safer match in case of duplicate text
            selected_qid = next(
                qid for qid, q in eligible_q.items() 
                if q.question == selected_q.question and q.list_id == selected_q.list_id
            )
        else:
            # Fallback to 'asked', deprioritize recent ones
            asked = list(eligible_q.items())
            
            # Sort by least recently asked (to introduce some randomness in older questions)
            def sort_key(item):
                qid, q = item
                # Ensure q.last_asked is a datetime object (or handle None gracefully)
                if q.last_asked is None: 
                    return timedelta(days=3650).total_seconds() 
                # Ensure comparison is between two timezone-aware datetimes
                if q.last_asked.tzinfo is None:
                    q.last_asked = q.last_asked.replace(tzinfo=timezone.utc)
                return (now_utc - q.last_asked).total_seconds()
            
            asked.sort(key=sort_key, reverse=False)
            
            # Select from the top 5 least recently asked
            top_5 = asked[:min(5, len(asked))]
            selected_qid, selected_q = random.choice(top_5)


        # 4. Post the question and update data
        channel = self.bot.get_channel(schedule.channel_id)
        if not channel:
            log.warning(f"Channel {schedule.channel_id} for schedule {schedule_id} not found.")
            await self._update_schedule_next_run(schedule_id, schedule)
            return
            
        embed = discord.Embed(
            title=f"❓ Question of the Day: {selected_q.question}",
            color=discord.Color.blue()
        )
        
        if selected_q.suggested_by:
            user = self.bot.get_user(selected_q.suggested_by)
            suggested_by_text = user.display_name if user else f"User ID: {selected_q.suggested_by}"
            embed.set_footer(text=f"Suggested by {suggested_by_text}")

        try:
            await channel.send(embed=embed)
            log.info(f"Posted QOTD {selected_qid} in {channel.name}.")
            
            # Update the question data
            selected_q.status = "asked"
            selected_q.last_asked = now_utc
            await self.update_question_data(selected_qid, selected_q)
            
        except discord.Forbidden:
            log.error(f"Missing permissions to post in channel {channel.name} ({channel.id}).")
        except Exception as e:
            log.exception(f"Error posting QOTD for schedule {schedule_id}: {e}")

        # 5. Update the schedule for the next run
        await self._update_schedule_next_run(schedule_id, schedule)

    async def _update_schedule_next_run(self, schedule_id: str, schedule: Schedule):
        """Calculates and saves the next run time for a schedule."""
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
            
            schedule.next_run_time = now_utc + delta
        except (ValueError, IndexError, TypeError):
            log.error(f"Invalid frequency format '{schedule.frequency}' for schedule {schedule_id}. Disabling schedule.")
            schedule.next_run_time = now_utc + timedelta(days=3650) # Far future date
            
        # FIX: Double serialize to guarantee only JSON primitives (strings for datetime) are saved
        serialized_schedule = json.loads(schedule.model_dump_json())
            
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule

    # --- Utility Functions for Data Persistence ---

    async def add_question_to_data(self, question: QuestionData):
        """Adds a new question to the global questions dict with a UUID."""
        question_id = str(uuid.uuid4())
        
        # CRITICAL FIX: Double serialization to guarantee no datetime objects remain.
        serialized_q = json.loads(question.model_dump_json())
        
        async with self.config.questions() as questions:
            questions[question_id] = serialized_q
        
        # Handle new suggestion notification if it was added to the 'suggestions' list
        if question.list_id == "suggestions":
            await self._notify_new_suggestion(question_id, question)

    async def update_question_data(self, qid: str, question: QuestionData):
        """Updates an existing question."""
        # CRITICAL FIX: Double serialization to guarantee no datetime objects remain.
        serialized_q = json.loads(question.model_dump_json())
        
        async with self.config.questions() as questions:
            questions[qid] = serialized_q

    async def delete_question_by_id(self, qid: str):
        """Deletes a question by its ID."""
        async with self.config.questions() as questions:
            if qid in questions:
                del questions[qid]
                
    async def remove_question_from_list(self, list_id: str, qid: str):
        """Placeholder for logic to remove QID from a specific list index, currently handled by list_id in question data."""
        pass 
        
    async def _notify_new_suggestion(self, qid: str, question: QuestionData):
        """Posts a notification to the configured approval channel."""
        approval_channel_id = await self.config.approval_channel()
        if not approval_channel_id:
            return

        channel = self.bot.get_channel(approval_channel_id)
        if not channel:
            log.warning(f"Approval channel ID {approval_channel_id} not found.")
            return

        # Fetch the user directly for better error handling/display
        try:
            suggested_user = await self.bot.fetch_user(question.suggested_by) if question.suggested_by else None
        except discord.NotFound:
            suggested_user = None

        suggested_by_text = suggested_user.display_name if suggested_user else f"ID: {question.suggested_by}"
        
        lists_data = await self.config.lists()
        try:
            lists = {k: QuestionList.model_validate(v) for k,v in lists_data.items()}
        except ValidationError:
            log.error("Failed to validate list data during suggestion notification.")
            lists = {} # Fallback

        embed = discord.Embed(
            title="✨ New Question Suggestion Pending Approval",
            description=box(question.question, lang="text"),
            color=discord.Color.orange()
        )
        embed.add_field(name="Suggested By", value=suggested_by_text)
        embed.set_footer(text=f"Question ID: {qid}")

        # Send the message with the ApprovalView
        try:
            await channel.send(
                embed=embed, 
                view=ApprovalView(self, question, qid, lists)
            )
        except discord.Forbidden:
            log.error(f"Missing permissions to post in approval channel {channel.name}.")
        except Exception as e:
            log.exception(f"Error notifying new suggestion: {e}")

    # --- Listeners ---
    
    @commands.Cog.listener()
    async def on_ready(self):
        # Retrieve the most current list names for the SuggestionButton view
        lists_data = await self.config.lists()
        list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]
        
        # Remove and re-add the persistent view to ensure its context is fresh
        self.bot.remove_view("qotd_suggest_button")
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

    @qotd.group(name="list")
    async def qotd_list_management(self, ctx: commands.Context):
        """Manage Question Lists (groups of questions)."""
        pass
    
    @qotd_list_management.command(name="add")
    async def qotd_list_add(self, ctx: commands.Context, list_name: str):
        """Adds a new question list."""
        list_id = str(uuid.uuid4()).split('-')[0] # Short ID
        new_list = QuestionList(id=list_id, name=list_name)
        async with self.config.lists() as lists:
            if any(l.get('name', '').lower() == list_name.lower() for l in lists.values()):
                 return await ctx.send(warning(f"A list named **{list_name}** already exists."))
            
            # QuestionList does not contain datetime, but we ensure serialization just in case
            lists[list_id] = new_list.model_dump() 
            
        await ctx.send(f"Added new question list: **{list_name}** (ID: `{list_id}`).")

    @qotd_list_management.command(name="view")
    async def qotd_list_view(self, ctx: commands.Context):
        """Displays all available question lists."""
        lists_data = await self.config.lists()
        if not lists_data:
            return await ctx.send("No question lists defined.")
            
        msg = "**Available Question Lists:**\n"
        for list_id, list_dict in lists_data.items():
            try:
                list_obj = QuestionList.model_validate(list_dict)
            except ValidationError:
                continue

            # Count questions in this list
            all_questions = await self.config.questions()
            count = sum(1 for q in all_questions.values() if q.get('list_id') == list_id)
            
            exclusion_str = f"Excluded Dates: {', '.join(list_obj.exclusion_dates)}" if list_obj.exclusion_dates else ""

            msg += f"• **{list_obj.name}** (`{list_id}`) - {count} questions\n"
            if exclusion_str:
                msg += f"  - {exclusion_str}\n"
            
        await ctx.send(box(msg))

    @qotd_list_management.group(name="rule")
    async def qotd_list_rule(self, ctx: commands.Context):
        """Manage list-specific exclusion rules (dates the list should not be used)."""
        pass
        
    @qotd_list_rule.command(name="addexclusion")
    async def qotd_list_rule_add(self, ctx: commands.Context, list_id: str, month_day: str):
        """
        Adds a single day (MM-DD) when this list should NOT be used by any schedule.
        
        Example: `[p]qotd list rule addexclusion mylist 02-14`
        """
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))
            
        if not month_day.strip().replace('-', '').isdigit() or len(month_day) != 5 or month_day[2] != '-':
            return await ctx.send(warning("Date format must be `MM-DD`, e.g., `02-14` or `12-25`."))

        try:
            list_obj = QuestionList.model_validate(lists_data[list_id])
        except ValidationError:
            return await ctx.send(warning(f"List data for `{list_id}` is invalid."))

        if month_day in list_obj.exclusion_dates:
            return await ctx.send(warning(f"Date **{month_day}** is already an exclusion for **{list_obj.name}**."))
            
        list_obj.exclusion_dates.append(month_day)
        
        async with self.config.lists() as lists:
            lists[list_id] = list_obj.model_dump()

        await ctx.send(f"Added exclusion date **{month_day}** to list **{list_obj.name}**. Questions from this list will be skipped on this date.")

    @qotd_list_rule.command(name="removeexclusion")
    async def qotd_list_rule_remove(self, ctx: commands.Context, list_id: str, month_day: str):
        """Removes a single exclusion day (MM-DD) from a list."""
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found."))

        try:
            list_obj = QuestionList.model_validate(lists_data[list_id])
        except ValidationError:
            return await ctx.send(warning(f"List data for `{list_id}` is invalid."))

        if month_day not in list_obj.exclusion_dates:
            return await ctx.send(warning(f"Date **{month_day}** is not an exclusion for **{list_obj.name}**."))
            
        list_obj.exclusion_dates.remove(month_day)
        
        async with self.config.lists() as lists:
            lists[list_id] = list_obj.model_dump()

        await ctx.send(f"Removed exclusion date **{month_day}** from list **{list_obj.name}**.")


    @qotd.group(name="schedule")
    async def qotd_schedule_management(self, ctx: commands.Context):
        """Manage QOTD schedules."""
        pass

    @qotd_schedule_management.command(name="add")
    async def qotd_schedule_add(self, ctx: commands.Context, list_id: str, channel: discord.TextChannel, *, frequency: str):
        """
        Adds a new schedule.
        
        `<list_id>`: The ID of the question list to pull from.
        `<channel>`: The channel to post the question in.
        `<frequency>`: How often to post (e.g., '1 day', '12 hours', '30 minutes').
        """
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found. Use `[p]qotd list view` to see IDs."))

        schedule_id = str(uuid.uuid4()).split('-')[0]
        
        # Validate frequency format (same logic as in _update_schedule_next_run)
        try:
            time_unit = frequency.split()
            if len(time_unit) != 2: raise ValueError
            amount = int(time_unit[0])
            unit = time_unit[1].lower().rstrip('s')
            if unit not in ('minute', 'hour', 'day', 'week'): raise ValueError
        except (ValueError, IndexError):
            # This is the exact error message the user saw.
            return await ctx.send(warning("Invalid frequency format. Must be like '1 day', '3 hours', or '30 minutes'. **Ensure the unit is separated by one space.**"))

        new_schedule = Schedule(
            id=schedule_id, 
            list_id=list_id, 
            channel_id=channel.id, 
            frequency=frequency,
            next_run_time=datetime.now(timezone.utc) # Start immediately
        )
        
        # FIX: Double serialize to guarantee only JSON primitives (strings for datetime) are saved
        serialized_schedule = json.loads(new_schedule.model_dump_json())
        
        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
            
        await ctx.send(f"Added new schedule: posting from list **{lists_data[list_id]['name']}** to {channel.mention} every **{frequency}** (ID: `{schedule_id}`).")

    @qotd_schedule_management.command(name="view")
    async def qotd_schedule_view(self, ctx: commands.Context):
        """Displays all configured schedules."""
        schedules_data = await self.config.schedules()
        lists_data = await self.config.lists()
        
        if not schedules_data:
            return await ctx.send("No schedules currently configured.")
            
        msg = "**Configured Schedules:**\n"
        for schedule_id, schedule_dict in schedules_data.items():
            try:
                # FIX: Check for string data and parse it back to a timezone-aware datetime object
                next_run_time_data = schedule_dict.get('next_run_time')
                if isinstance(next_run_time_data, str):
                    dt_obj = datetime.fromisoformat(next_run_time_data)
                    if dt_obj.tzinfo is None:
                        # If the string was saved without timezone info, force it to UTC
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    schedule_dict['next_run_time'] = dt_obj

                schedule = Schedule.model_validate(schedule_dict)
            except (ValidationError, ValueError):
                msg += f"• **ID `{schedule_id}`:** (Invalid/Corrupt Data)\n"
                continue
                
            list_name = lists_data.get(schedule.list_id, {}).get('name', 'UNKNOWN LIST')
            channel = self.bot.get_channel(schedule.channel_id)
            channel_name = channel.mention if channel else f"UNKNOWN CHANNEL ID ({schedule.channel_id})"
            
            # Ensure next_run_time is timezone-aware for formatting
            run_time = schedule.next_run_time
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone.utc)
            
            next_run_str = discord.utils.format_dt(run_time, "R")
            
            rules_summary = ""
            if schedule.rules:
                rules_summary = "\n"
                for i, rule in enumerate(schedule.rules):
                    target = f"list `{rule.list_id_override}`" if rule.list_id_override else ""
                    action_verb = "use list" if rule.action == "use_list" else "skip run"
                    rules_summary += f"  - Rule {i+1} (`{rule.id}`): **{action_verb}** {target} from {rule.start_month_day} to {rule.end_month_day}\n"

            msg += (
                f"• **ID `{schedule_id}`:**\n"
                f"  - List: **{list_name}**\n"
                f"  - Channel: {channel_name}\n"
                f"  - Frequency: **{schedule.frequency}**\n"
                f"  - Next Run: {next_run_str}"
            )
            if rules_summary:
                msg += rules_summary
            msg += "\n"
            
        await ctx.send(box(msg))

    # --- New Schedule Rule Management Group ---

    @qotd_schedule_management.group(name="rule")
    async def qotd_schedule_rule(self, ctx: commands.Context):
        """Manage date-based rules for a specific schedule."""
        pass

    @qotd_schedule_rule.command(name="addpriority")
    async def qotd_schedule_rule_add_priority(self, ctx: commands.Context, schedule_id: str, list_id: str, start_date: str, end_date: str):
        """
        Adds a rule to prioritize a different list during a date range (MM-DD).

        Example: `[p]qotd schedule rule addpriority schid christmaslist 12-01 12-31`
        """
        schedules_data = await self.config.schedules()
        lists_data = await self.config.lists()
        
        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule ID `{schedule_id}` not found."))
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` (priority target) not found."))
            
        try:
            ScheduleRule(id="temp", start_month_day=start_date, end_month_day=end_date, action="use_list", list_id_override=list_id)
        except ValidationError:
            return await ctx.send(warning("Invalid date format. Dates must be in `MM-DD` format (e.g., `01-01`)."))

        rule_id = str(uuid.uuid4()).split('-')[0]
        new_rule = ScheduleRule(
            id=rule_id, 
            start_month_day=start_date, 
            end_month_day=end_date, 
            action="use_list", 
            list_id_override=list_id
        )

        schedule = Schedule.model_validate(schedules_data[schedule_id])
        schedule.rules.append(new_rule)
        
        # FIX: Double serialize to guarantee only JSON primitives (strings for datetime) are saved
        serialized_schedule = json.loads(schedule.model_dump_json())

        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
            
        await ctx.send(f"Added priority rule (ID: `{rule_id}`) to schedule `{schedule_id}`: will use **{lists_data[list_id]['name']}** from **{start_date}** to **{end_date}**.")


    @qotd_schedule_rule.command(name="addskip")
    async def qotd_schedule_rule_add_skip(self, ctx: commands.Context, schedule_id: str, start_date: str, end_date: str):
        """
        Adds a rule to skip posting entirely during a date range (MM-DD).

        Example: `[p]qotd schedule rule addskip schid 12-25 12-25`
        """
        schedules_data = await self.config.schedules()
        
        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule ID `{schedule_id}` not found."))
            
        try:
            ScheduleRule(id="temp", start_month_day=start_date, end_month_day=end_date, action="skip_run")
        except ValidationError:
            return await ctx.send(warning("Invalid date format. Dates must be in `MM-DD` format (e.g., `01-01`)."))

        rule_id = str(uuid.uuid4()).split('-')[0]
        new_rule = ScheduleRule(
            id=rule_id, 
            start_month_day=start_date, 
            end_month_day=end_date, 
            action="skip_run"
        )

        schedule = Schedule.model_validate(schedules_data[schedule_id])
        schedule.rules.append(new_rule)
        
        # FIX: Double serialize to guarantee only JSON primitives (strings for datetime) are saved
        serialized_schedule = json.loads(schedule.model_dump_json())

        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
            
        await ctx.send(f"Added skip rule (ID: `{rule_id}`) to schedule `{schedule_id}`: will **skip posting** from **{start_date}** to **{end_date}**.")


    @qotd_schedule_rule.command(name="removerule")
    async def qotd_schedule_rule_remove(self, ctx: commands.Context, schedule_id: str, rule_id: str):
        """Removes a rule from a schedule by its unique rule ID."""
        schedules_data = await self.config.schedules()
        
        if schedule_id not in schedules_data:
            return await ctx.send(warning(f"Schedule ID `{schedule_id}` not found."))

        schedule = Schedule.model_validate(schedules_data[schedule_id])
        
        initial_length = len(schedule.rules)
        schedule.rules = [rule for rule in schedule.rules if rule.id != rule_id]

        if len(schedule.rules) == initial_length:
             return await ctx.send(warning(f"Rule ID `{rule_id}` not found in schedule `{schedule_id}`."))
             
        # FIX: Double serialize to guarantee only JSON primitives (strings for datetime) are saved
        serialized_schedule = json.loads(schedule.model_dump_json())

        async with self.config.schedules() as schedules:
            schedules[schedule_id] = serialized_schedule
            
        await ctx.send(f"Successfully removed rule **`{rule_id}`** from schedule **`{schedule_id}`**.")

    @qotd.command(name="import")
    async def qotd_import(self, ctx: commands.Context, list_id: str):
        """
        Imports questions from an attached JSON file into a specific question list.
        
        The file should be attached to the command message.
        """
        if not ctx.message.attachments:
            return await ctx.send(warning("Please attach a JSON file containing the questions."))

        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found. Use `[p]qotd list view` to see IDs."))

        file = ctx.message.attachments[0]
        if not file.filename.endswith('.json'):
            return await ctx.send(warning("The attached file must be a JSON file."))

        try:
            file_data = await file.read()
            questions_list = json.loads(file_data.decode('utf-8'))
        except Exception as e:
            log.exception("Error reading or parsing import file.")
            return await ctx.send(warning(f"Failed to read or parse the JSON file. Error: {e}"))
            
        if not isinstance(questions_list, list):
             return await ctx.send(warning("The JSON file must contain a list of question objects."))

        imported_count = 0
        skipped_count = 0
        
        # Keys to ignore based on user request
        IGNORED_KEYS = ["2s5qal", "e8auv2"]
        
        async with self.config.questions() as global_questions:
            for item in questions_list:
                
                if not isinstance(item, dict):
                    skipped_count += 1
                    continue
                
                question_text = None
                
                # Check for "question" key
                if "question" in item and isinstance(item["question"], str):
                    question_text = item["question"]
                else:
                    # Fallback for old/unstructured format (like the one user described)
                    for key, value in item.items():
                        if key not in IGNORED_KEYS and isinstance(value, str):
                            question_text = value
                            break # Use the first valid key/value found
                            
                if not question_text:
                    skipped_count += 1
                    continue
                
                # Create a new QuestionData object
                new_q = QuestionData(
                    question=question_text,
                    suggested_by=ctx.author.id,
                    list_id=list_id,
                    status="not asked",
                    added_on=datetime.now(timezone.utc),
                )
                
                # CRITICAL: Double serialize to guarantee no datetime objects remain.
                serialized_q = json.loads(new_q.model_dump_json())
                
                global_questions[str(uuid.uuid4())] = serialized_q
                imported_count += 1

        await ctx.send(f"Import complete. Imported **{imported_count}** questions into list **{lists_data[list_id]['name']}**. Skipped {skipped_count} items (including file header identifiers).")

    @qotd.command(name="export")
    async def qotd_export(self, ctx: commands.Context, list_id: str):
        """Exports all questions from a specific list into a JSON file."""
        
        lists_data = await self.config.lists()
        if list_id not in lists_data:
            return await ctx.send(warning(f"List ID `{list_id}` not found. Use `[p]qotd list view` to see IDs."))
            
        all_questions = await self.config.questions()
        list_name = lists_data.get(list_id, {}).get('name', 'Unknown List')
        
        export_data = []
        
        # Include placeholders for the source format identifiers the user requested
        export_data.append({"2s5qal": f"Export from QOTD list: {list_name}"})
        export_data.append({"e8auv2": f"Questions below for list ID: {list_id}"})
        
        question_count = 0
        for qid, qdict in all_questions.items():
            if qdict.get('list_id') == list_id:
                # Export as a simple object containing just the question text for easy re-import/use
                export_data.append({"question": qdict.get('question', 'Error: Missing question text')})
                question_count += 1

        if question_count == 0:
            return await ctx.send(f"The list **{list_name}** is empty.")

        # Create the file
        file_name = f"qotd_export_{list_id}_{datetime.now().strftime('%Y%m%d')}.json"
        
        try:
            with Path(file_name).open("w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=4)
        except Exception as e:
            log.exception("Error writing export file.")
            return await ctx.send(warning(f"Failed to create the export file: {e}"))

        try:
            await ctx.send(
                f"Exported **{question_count}** questions from **{list_name}**.",
                file=discord.File(file_name)
            )
        except Exception as e:
            log.exception("Error sending export file.")
            await ctx.send(warning(f"Failed to send the export file: {e}"))
        finally:
            Path(file_name).unlink(missing_ok=True) # Clean up the file after sending

    # --- User Command for Suggestion ---
    
    @commands.hybrid_command(name="suggestqotd")
    @commands.guild_only()
    async def suggest_qotd_command(self, ctx: commands.Context):
        """Opens a form to suggest a Question of the Day."""
        # Get list names for the SuggestionModal
        lists_data = await self.config.lists()
        list_names = [v['name'] for v in lists_data.values() if v['id'] != "suggestions"]

        # Use a persistent view with a button that triggers the modal
        view = SuggestionButton(self, list_names)
        
        await ctx.send(
            "Click the button below to submit your question for review!", 
            view=view
        )