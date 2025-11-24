from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# --- Pydantic Models for Structured Data ---

class ScheduleRule(BaseModel):
    """
    Represents a time-based rule that overrides a schedule's list selection or prevents execution.
    Dates must be in MM-DD format, and the rule is inclusive of the start/end dates.
    """
    id: str # Unique ID for the rule
    start_month_day: str = Field(pattern=r"^\d{2}-\d{2}$", description="MM-DD format, inclusive")
    end_month_day: str = Field(pattern=r"^\d{2}-\d{2}$", description="MM-DD format, inclusive")
    
    # Action defines the consequence if the current date falls within the range
    action: Literal["use_list", "skip_run"]
    
    # The list ID to use if action is 'use_list'. Must be present for 'use_list'.
    list_id_override: Optional[str] = None 


class QuestionList(BaseModel):
    """Represents a collection/list of QOTD questions."""
    id: str
    name: str
    # List of MM-DD strings where this list should NOT be used (e.g., ["02-14", "12-25"])
    exclusion_dates: List[str] = Field(default_factory=list, description="List of 'MM-DD' dates where questions from this list must be skipped.")


class QuestionData(BaseModel):
    """Represents a single Question of the Day."""
    question: str
    suggested_by: Optional[int] = None  # Discord user ID
    list_id: str                   # Which list it belongs to (e.g., 'general', 'suggestions')
    status: Literal["asked", "not asked", "pending"] = "pending" # Status for filtering
    added_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_asked: Optional[datetime] = None
    
    # Ensure added_on/last_asked are timezone-aware (UTC) when loaded
    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
        }
        json_decoders = {
            datetime: lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        }

class Schedule(BaseModel):
    """Represents a posting schedule."""
    id: str
    list_id: str            # The default list to pull questions from
    channel_id: int         # The channel to post in
    frequency: str          # e.g., "1 day", "12 hours"
    next_run_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rules: List[ScheduleRule] = Field(default_factory=list) # New field for date rules
    
    # Ensure next_run_time is timezone-aware (UTC) when loaded
    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
        }
        json_decoders = {
            datetime: lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        }