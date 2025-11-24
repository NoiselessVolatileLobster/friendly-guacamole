from datetime import datetime
from typing import Dict, List, Literal, Optional, Set, Union
from pydantic import BaseModel, Field

# --- Pydantic Data Models for structured configuration ---

class ScheduleRule(BaseModel):
    """Defines a rule for a schedule based on a date range."""
    id: str
    start_month_day: str = Field(pattern=r"^\d{2}-\d{2}$", description="MM-DD format")
    end_month_day: str = Field(pattern=r"^\d{2}-\d{2}$", description="MM-DD format")
    # Action determines if we skip the run or use a different list
    action: Literal["skip_run", "use_list"] = "skip_run" 
    list_id_override: Optional[str] = None # Used only if action is "use_list"

class Schedule(BaseModel):
    """Defines a single QOTD posting schedule."""
    id: str
    list_id: str # The default list ID to use
    channel_id: int
    frequency: str # e.g., "1 day", "12 hours"
    next_run_time: datetime
    rules: List[ScheduleRule] = Field(default_factory=list)

class QuestionData(BaseModel):
    """Defines a single question and its metadata."""
    question: str
    suggested_by: Optional[int] = None
    list_id: str # Which list this question belongs to
    status: Literal["not asked", "asked", "pending"] = "not asked"
    added_on: datetime
    last_asked: Optional[datetime] = None

class QuestionList(BaseModel):
    """Defines a group of questions."""
    id: str
    name: str
    exclusion_dates: List[str] = Field(default_factory=list, description="List of dates (MM-DD) to skip this list.")