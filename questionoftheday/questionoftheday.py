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

# --- Pydantic Models (Defining here for self-contained structure) ---

class ScheduleRule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()).split('-')[0])
    start_month_day: str
    end_month_day: str
    action: Literal["skip_run", "use_list"]
    list_id_override: Optional[str] = None # Used when action is "use_list"

class QuestionList(BaseModel):
    id: str
    name: str
    exclusion_dates: List[str] = Field(default_factory=list)

class Schedule(BaseModel):
    id: str
    list_id: str
    channel_id: int
    frequency: str
    post_time: Optional[str] = None # HH:MM format (24h UTC)
    last_post_time: Optional[datetime] = None
    last_question_id: Optional[str] = None
    rules: List[ScheduleRule] = Field(default_factory=list)

class Question(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    list_id: str
    user_id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# --- Constants and Logging ---

log = getLogger("red.fluffy.questionoftheday")

DEFAULT_GLOBAL = {
    "lists": []
}

DEFAULT_GUILD = {
    "schedules": [],
    "questions": [] # All questions from all lists for a guild, keyed by list_id.
}

# --- Utility Functions (For chat formatting) ---

def format_list_name(list_id: str, list_name: str) -> str:
    """Formats the list name and ID for display."""
    return f"**{list_name}** (`{list_id}`)"

# --- Main Cog Class ---

class QuestionOfTheDay(commands.Cog):
    """
    Manages daily or scheduled question prompts for discussion in Discord channels.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=141873836378, force_registration=True)
        self.config.register_global(**DEFAULT_GLOBAL)
        self.config.register_guild(**DEFAULT_GUILD)
        
        # Internal cache for list names to avoid repeated config lookups
        self._list_name_cache: Dict[str, str] = {}
        self.qotd_loop.start()

    def cog_unload(self):
        self.qotd_loop.cancel()
    
    # --- Helper Methods ---

    async def get_list_by_id(self, list_id: str) -> Optional[QuestionList]:
        """Fetches a QuestionList object by its ID from global config."""
        lists_data = await self.config.lists()
        for data in lists_data:
            try:
                q_list = QuestionList.model_validate(data)
                if q_list.id == list_id:
                    return q_list
            except ValidationError as e:
                log.error(f"Invalid list data found in config: {data} -> {e}")
                continue
        return None

    async def get_all_questions_for_guild(self, guild_id: int) -> List[Question]:
        """Fetches all questions for a specific guild."""
        questions_data = await self.config.guild(discord.Object(id=guild_id)).questions()
        questions = []
        for data in questions_data:
            try:
                questions.append(Question.model_validate(data))
            except ValidationError as e:
                log.error(f"Invalid question data found in guild {guild_id} config: {data} -> {e}")
                continue
        return questions

    async def get_questions_for_list(self, guild_id: int, list_id: str) -> List[Question]:
        """Fetches questions belonging to a specific list for a guild."""
        all_questions = await self.get_all_questions_for_guild(guild_id)
        return [q for q in all_questions if q.list_id == list_id]

    async def update_guild_questions(self, guild_id: int, questions: List[Question]):
        """Saves the entire list of questions back to the guild config."""
        questions_data = [q.model_dump() for q in questions]
        await self.config.guild(discord.Object(id=guild_id)).questions.set(questions_data)


    # --- Tasks ---

    @tasks.loop(seconds=60)
    async def qotd_loop(self):
        # Implementation for the main posting loop
        pass

    @qotd_loop.before_loop
    async def before_qotd_loop(self):
        await self.bot.wait_until_red_ready()
    
    # --- Commands ---

    @commands.group()
    async def qotd(self, ctx: commands.Context):
        """Base command for Question of the Day settings."""
        pass

    @qotd.group(name="admin", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def qotd_admin(self, ctx: commands.Context):
        """Administrator settings for Question of the Day (e.g., scheduling)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
            
    # --- LIST MANAGEMENT GROUP ---

    @qotd.group(name="list", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def qotd_list(self, ctx: commands.Context):
        """Manage global question lists and their contents within this guild."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)


    @qotd_list.command(name="create")
    async def list_create(self, ctx: commands.Context, list_name: str):
        """
        Creates a new global question list.
        
        This list can be used by any guild.
        """
        list_id = str(uuid.uuid4()).split('-')[0]
        new_list = QuestionList(id=list_id, name=list_name)

        async with self.config.lists() as lists:
            lists.append(new_list.model_dump())

        await ctx.send(
            success(f"Question list created: {format_list_name(list_id, list_name)}")
        )

    # --- QUESTION MANAGEMENT COMMANDS (under 'list' group) ---

    @qotd_list.command(name="add")
    async def list_add_question(self, ctx: commands.Context, list_id: str, *, question_text: str):
        """
        Adds a question to a specific list for use in this guild.
        
        The list is identified by its ID.
        """
        if ctx.guild is None:
            return await ctx.send(error("This command must be run in a guild."))

        list_obj = await self.get_list_by_id(list_id)
        if not list_obj:
            return await ctx.send(warning(f"List with ID `{list_id}` not found. Use `{ctx.prefix}qotd list` to see available lists."))

        # Create the new question
        new_question = Question(
            text=question_text,
            list_id=list_id,
            user_id=ctx.author.id
        )

        # Add the question to the guild's questions list
        async with self.config.guild(ctx.guild).questions() as questions_data:
            questions_data.append(new_question.model_dump())

        await ctx.send(
            success(
                f"Question successfully added to {format_list_name(list_id, list_obj.name)}:\n"
                f"{box(question_text)}"
            )
        )

    @qotd_list.command(name="show")
    async def list_show_questions(self, ctx: commands.Context, list_id: str):
        """Lists all questions in a specific list for this guild."""
        if ctx.guild is None:
            return await ctx.send(error("This command must be run in a guild."))

        list_obj = await self.get_list_by_id(list_id)
        if not list_obj:
            return await ctx.send(warning(f"List with ID `{list_id}` not found."))

        questions = await self.get_questions_for_list(ctx.guild.id, list_id)

        if not questions:
            return await ctx.send(info(f"The list {format_list_name(list_id, list_obj.name)} has no questions in this guild."))

        output = [f"Questions in {format_list_name(list_id, list_obj.name)}:"]
        for q in questions:
            # Shorten question text for list view
            display_text = q.text[:80] + "..." if len(q.text) > 80 else q.text
            output.append(f"`{q.id.split('-')[0]}`: {display_text}")
        
        await ctx.send(box('\n'.join(output), lang="md"))


    @qotd_list.command(name="clear")
    async def list_clear(self, ctx: commands.Context, list_id: str):
        """
        Clears (deletes) **all** questions belonging to a specific list ID in this guild.

        **WARNING:** This action is irreversible.
        """
        if ctx.guild is None:
            return await ctx.send(error("This command must be run in a guild."))

        list_obj = await self.get_list_by_id(list_id)
        if not list_obj:
            return await ctx.send(warning(f"List with ID `{list_id}` not found."))
        
        list_name = list_obj.name

        # Confirmation step
        message = await ctx.send(
            warning(
                f"**Confirmation Required:** Are you absolutely sure you want to delete ALL questions for the list {format_list_name(list_id, list_name)} in this guild?\n"
                "This action is irreversible. React with 👍 to confirm within 30 seconds."
            )
        )
        await message.add_reaction("👍")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) == '👍' and reaction.message.id == message.id

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await message.clear_reactions()
            return await ctx.send(info("Question clearing cancelled due to timeout."))
        
        # Action confirmed, proceed with deletion
        await message.clear_reactions()
        
        questions = await self.get_all_questions_for_guild(ctx.guild.id)
        
        initial_count = len([q for q in questions if q.list_id == list_id])
        if initial_count == 0:
            return await ctx.send(info(f"The list {format_list_name(list_id, list_name)} is already empty. No action taken."))

        # Filter out questions belonging to the specified list_id
        updated_questions = [q for q in questions if q.list_id != list_id]
        
        # Save the updated list back to config
        await self.update_guild_questions(ctx.guild.id, updated_questions)
        
        deleted_count = initial_count
        
        await ctx.send(
            success(f"Successfully cleared **{deleted_count}** questions from the list {format_list_name(list_id, list_name)}.")
        )


    # --- EXPORT COMMAND (under 'list' group) ---

    @qotd_list.command(name="export")
    async def list_export(self, ctx: commands.Context, list_id: str):
        """
        Exports all questions from a specific list into a JSON file.
        
        The file includes all question metadata (ID, user, creation time) for robust importing later.
        """
        if ctx.guild is None:
            return await ctx.send(error("This command must be run in a guild."))

        list_obj = await self.get_list_by_id(list_id)
        if not list_obj:
            return await ctx.send(warning(f"List with ID `{list_id}` not found."))
        
        list_name = list_obj.name

        questions = await self.get_questions_for_list(ctx.guild.id, list_id)
        question_count = len(questions)
        
        if question_count == 0:
            return await ctx.send(f"The list **{list_name}** is empty.")

        # Prepare data for export, converting datetime objects to ISO format strings
        export_data = {
            "list_id": list_id,
            "list_name": list_name,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "questions": [q.model_dump(mode="json") for q in questions]
        }

        # Create the file name
        file_name = f"qotd_export_{list_id}_{datetime.now().strftime('%Y%m%d')}.json"
        
        # Get the cog's data directory path
        data_dir = cog_data_path(self)
        temp_dir = data_dir / "temp_exports"
        
        # Ensure the subdirectory exists
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        temp_path = temp_dir / file_name

        try:
            # Write the file to the cog's data directory (guaranteed writable)
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=4)
        except Exception as e:
            log.exception("Error writing export file to temporary location.")
            return await ctx.send(warning(f"Failed to create the export file in a writable directory: {e}"))

        try:
            await ctx.send(
                f"Exported **{question_count}** questions from **{list_name}**. The file now includes the unique question ID and all metadata for robust importing.",
                file=discord.File(temp_path)
            )
        except Exception as e:
            log.exception("Error sending export file.")
            await ctx.send(error("Failed to send the export file. Check the bot logs."))
        finally:
            # Clean up the temporary file
            try:
                temp_path.unlink()
            except OSError as e:
                log.warning(f"Could not delete temporary export file {temp_path}: {e}")