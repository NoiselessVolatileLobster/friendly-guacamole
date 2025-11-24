from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, ValidationError, field_validator
import re

# --- Data Models for Configuration ---

class QuestionData(BaseModel):
    """Represents a single question."""
    question: str
    list_id: str
    suggested_by: Optional[int] = None
    status: Literal["not asked", "asked", "pending"] = "not asked"
    added_on: datetime = Field(default_factory=lambda: datetime.now().astimezone(None))
    last_asked: Optional[datetime] = None

class QuestionList(BaseModel):
    """Represents a named collection of questions."""
    id: str
    name: str
    # List of dates (MM-DD) when this list should not be used by any schedule
    exclusion_dates: List[str] = Field(default_factory=list)

class ScheduleRule(BaseModel):
    """Represents a specific rule for a schedule during a date range."""
    id: str
    # Month and day format: MM-DD (e.g., 01-01 for Jan 1st)
    start_month_day: str
    end_month_day: str
    action: Literal["skip_run", "use_list"]
    # Required if action is "use_list"
    list_id_override: Optional[str] = None
    
    @field_validator('start_month_day', 'end_month_day')
    def validate_month_day(cls, v):
        if not re.match(r"^\d{2}-\d{2}$", v):
            raise ValueError("Date must be in MM-DD format.")
        try:
            datetime.strptime(v, "%m-%d")
        except ValueError:
            raise ValueError("Invalid month or day in MM-DD format.")
        return v

class Schedule(BaseModel):
    """Represents a timed posting schedule."""
    id: str
    list_id: str
    channel_id: int
    frequency: str
    next_run_time: datetime
    # New: Optional time of day (HH:MM) to post, relative to UTC
    post_time: Optional[str] = None
    rules: List[ScheduleRule] = Field(default_factory=list)
    
    @field_validator('post_time')
    def validate_post_time(cls, v):
        if v is None:
            return v
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("Post time must be in HH:MM format (24-hour clock).")
        try:
            # Check if HH is 00-23 and MM is 00-59
            hour, minute = map(int, v.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid hour or minute in HH:MM format.")
        except ValueError:
            raise ValueError("Post time must be in HH:MM format (24-hour clock).")
        return v