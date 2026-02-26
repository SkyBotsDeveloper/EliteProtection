from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PaymentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class PaymentRequest(BaseModel):
    payment_id: str
    user_id: int
    username: str | None = None
    full_name: str
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="ignore")
