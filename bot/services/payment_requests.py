import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from bot.db import get_database
from bot.db.models import PaymentRequest, PaymentStatus

PAYMENTS_COLLECTION = "payments"
PAYMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class CreatePendingPaymentStatus(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"


@dataclass(slots=True)
class CreatePendingPaymentResult:
    status: CreatePendingPaymentStatus
    payment_id: str | None = None


_indexes_ready = False
_indexes_lock = asyncio.Lock()


def is_valid_payment_id(payment_id: str) -> bool:
    return bool(PAYMENT_ID_PATTERN.fullmatch(payment_id))


async def ensure_payment_indexes() -> None:
    global _indexes_ready

    if _indexes_ready:
        return

    async with _indexes_lock:
        if _indexes_ready:
            return

        collection = get_database()[PAYMENTS_COLLECTION]
        await collection.create_index(
            [("payment_id", ASCENDING)],
            unique=True,
            name="uq_payments_payment_id",
        )
        await collection.create_index(
            [("user_id", ASCENDING), ("status", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": PaymentStatus.PENDING.value},
            name="uq_payments_user_pending",
        )
        await collection.create_index(
            [("status", ASCENDING), ("created_at", ASCENDING)],
            name="idx_payments_status_created_at",
        )
        _indexes_ready = True


async def create_pending_payment_request(
    *,
    user_id: int,
    username: str | None,
    full_name: str,
) -> CreatePendingPaymentResult:
    await ensure_payment_indexes()

    collection = get_database()[PAYMENTS_COLLECTION]
    existing = await collection.find_one(
        {"user_id": user_id, "status": PaymentStatus.PENDING.value},
        {"_id": 1, "payment_id": 1},
    )
    if existing is not None:
        return CreatePendingPaymentResult(
            status=CreatePendingPaymentStatus.DUPLICATE,
            payment_id=existing.get("payment_id"),
        )

    payment = PaymentRequest(
        payment_id=uuid4().hex,
        user_id=user_id,
        username=username,
        full_name=full_name,
    )

    try:
        await collection.insert_one(payment.model_dump(mode="python"))
    except DuplicateKeyError:
        return CreatePendingPaymentResult(status=CreatePendingPaymentStatus.DUPLICATE)

    return CreatePendingPaymentResult(
        status=CreatePendingPaymentStatus.CREATED,
        payment_id=payment.payment_id,
    )


async def list_pending_payment_requests(*, limit: int = 20) -> list[PaymentRequest]:
    await ensure_payment_indexes()

    capped_limit = max(1, min(limit, 100))
    collection = get_database()[PAYMENTS_COLLECTION]
    cursor = collection.find(
        {"status": PaymentStatus.PENDING.value},
        {"_id": 0},
    ).sort("created_at", ASCENDING).limit(capped_limit)

    pending_requests: list[PaymentRequest] = []
    async for document in cursor:
        pending_requests.append(PaymentRequest.model_validate(document))

    return pending_requests


async def count_pending_payment_requests() -> int:
    await ensure_payment_indexes()

    collection = get_database()[PAYMENTS_COLLECTION]
    return int(await collection.count_documents({"status": PaymentStatus.PENDING.value}))


async def get_pending_payment_request_by_user(*, user_id: int) -> PaymentRequest | None:
    await ensure_payment_indexes()

    collection = get_database()[PAYMENTS_COLLECTION]
    document = await collection.find_one(
        {"user_id": user_id, "status": PaymentStatus.PENDING.value},
        {"_id": 0},
    )
    if document is None:
        return None

    return PaymentRequest.model_validate(document)


async def update_payment_status(
    *,
    payment_id: str,
    status: PaymentStatus,
) -> PaymentRequest | None:
    if status == PaymentStatus.PENDING:
        raise ValueError("Pending status update is not allowed here")

    normalized_payment_id = payment_id.strip().lower()
    if not is_valid_payment_id(normalized_payment_id):
        return None

    await ensure_payment_indexes()

    collection = get_database()[PAYMENTS_COLLECTION]
    updated_document = await collection.find_one_and_update(
        {
            "payment_id": normalized_payment_id,
            "status": PaymentStatus.PENDING.value,
        },
        {"$set": {"status": status.value}},
        return_document=ReturnDocument.AFTER,
    )

    if updated_document is None:
        return None

    return PaymentRequest.model_validate(updated_document)
