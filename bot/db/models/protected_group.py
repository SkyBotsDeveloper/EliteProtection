from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ProtectedGroup(BaseModel):
    group_id: int
    owner_user_id: int
    subscription_status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    activated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="ignore")
