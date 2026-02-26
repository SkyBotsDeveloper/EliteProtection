from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class UserState(str, Enum):
    AWAITING_GROUP_ID = "awaiting_group_id"


class UserStateRecord(BaseModel):
    user_id: int
    state: UserState
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="ignore")
