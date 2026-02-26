import asyncio
import re
from dataclasses import dataclass
from enum import Enum

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from bot.db import get_database
from bot.db.models import ProtectedGroup, SubscriptionStatus
from .group_cache import (
    configure_group_cache,
    count_group_cache,
    is_group_protected_cached,
    mark_group_active_cached,
    mark_group_inactive_cached,
    start_group_cache,
    stop_group_cache,
)

PROTECTED_GROUPS_COLLECTION = "protected_groups"
GROUP_CHAT_ID_PATTERN = re.compile(r"^-(?:100\d{5,}|\d{5,})$")


class BindProtectedGroupStatus(str, Enum):
    CREATED = "created"
    GROUP_ALREADY_BOUND = "group_already_bound"


@dataclass(slots=True)
class BindProtectedGroupResult:
    status: BindProtectedGroupStatus


class RevokeProtectedGroupStatus(str, Enum):
    REVOKED = "revoked"
    NOT_FOUND = "not_found"
    ALREADY_REVOKED = "already_revoked"


@dataclass(slots=True)
class RevokeProtectedGroupResult:
    status: RevokeProtectedGroupStatus


_indexes_ready = False
_indexes_lock = asyncio.Lock()


def parse_group_chat_id(raw_text: str) -> int | None:
    cleaned = raw_text.strip()
    if not GROUP_CHAT_ID_PATTERN.fullmatch(cleaned):
        return None
    return int(cleaned)


def configure_protected_group_cache(*, refresh_interval_seconds: int) -> None:
    configure_group_cache(refresh_interval_seconds=refresh_interval_seconds)


async def ensure_protected_group_indexes() -> None:
    global _indexes_ready

    if _indexes_ready:
        return

    async with _indexes_lock:
        if _indexes_ready:
            return

        collection = get_database()[PROTECTED_GROUPS_COLLECTION]
        await collection.create_index(
            [("group_id", ASCENDING)],
            unique=True,
            name="uq_protected_groups_group_id",
        )
        await collection.create_index(
            [("owner_user_id", ASCENDING), ("subscription_status", ASCENDING)],
            name="idx_protected_groups_owner_status",
        )
        _indexes_ready = True


async def start_protected_group_cache() -> None:
    await start_group_cache()


async def stop_protected_group_cache() -> None:
    await stop_group_cache()


async def bind_protected_group(
    *,
    owner_user_id: int,
    group_id: int,
) -> BindProtectedGroupResult:
    await ensure_protected_group_indexes()

    collection = get_database()[PROTECTED_GROUPS_COLLECTION]
    document = ProtectedGroup(group_id=group_id, owner_user_id=owner_user_id)

    try:
        await collection.insert_one(document.model_dump(mode="python"))
    except DuplicateKeyError:
        return BindProtectedGroupResult(status=BindProtectedGroupStatus.GROUP_ALREADY_BOUND)

    await mark_group_active_cached(group_id=group_id)
    return BindProtectedGroupResult(status=BindProtectedGroupStatus.CREATED)


async def revoke_protected_group(*, group_id: int) -> RevokeProtectedGroupResult:
    await ensure_protected_group_indexes()

    collection = get_database()[PROTECTED_GROUPS_COLLECTION]
    update_result = await collection.update_one(
        {
            "group_id": group_id,
            "subscription_status": SubscriptionStatus.ACTIVE.value,
        },
        {"$set": {"subscription_status": SubscriptionStatus.REVOKED.value}},
    )
    if update_result.modified_count > 0:
        await mark_group_inactive_cached(group_id=group_id)
        return RevokeProtectedGroupResult(status=RevokeProtectedGroupStatus.REVOKED)

    existing = await collection.find_one({"group_id": group_id}, {"_id": 0, "group_id": 1})
    if existing is None:
        return RevokeProtectedGroupResult(status=RevokeProtectedGroupStatus.NOT_FOUND)

    await mark_group_inactive_cached(group_id=group_id)
    return RevokeProtectedGroupResult(status=RevokeProtectedGroupStatus.ALREADY_REVOKED)


async def get_active_protected_group(*, group_id: int) -> ProtectedGroup | None:
    await ensure_protected_group_indexes()

    collection = get_database()[PROTECTED_GROUPS_COLLECTION]
    document = await collection.find_one(
        {
            "group_id": group_id,
            "subscription_status": SubscriptionStatus.ACTIVE.value,
        },
        {"_id": 0},
    )
    if document is None:
        await mark_group_inactive_cached(group_id=group_id)
        return None

    await mark_group_active_cached(group_id=group_id)
    return ProtectedGroup.model_validate(document)


async def is_group_protected(*, group_id: int) -> bool:
    return await is_group_protected_cached(group_id=group_id)


async def count_active_protected_groups() -> int:
    return await count_group_cache()


async def list_active_groups_by_owner(*, owner_user_id: int, limit: int = 10) -> list[ProtectedGroup]:
    await ensure_protected_group_indexes()

    capped_limit = max(1, min(limit, 100))
    collection = get_database()[PROTECTED_GROUPS_COLLECTION]
    cursor = (
        collection.find(
            {
                "owner_user_id": owner_user_id,
                "subscription_status": SubscriptionStatus.ACTIVE.value,
            },
            {"_id": 0},
        )
        .sort("activated_at", ASCENDING)
        .limit(capped_limit)
    )

    groups: list[ProtectedGroup] = []
    async for document in cursor:
        groups.append(ProtectedGroup.model_validate(document))

    return groups
