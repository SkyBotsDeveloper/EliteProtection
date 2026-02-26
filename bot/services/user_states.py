import asyncio
from datetime import UTC, datetime

from pymongo import ASCENDING

from bot.db import get_database
from bot.db.models import UserState, UserStateRecord

USER_STATES_COLLECTION = "user_states"

_indexes_ready = False
_indexes_lock = asyncio.Lock()


async def ensure_user_state_indexes() -> None:
    global _indexes_ready

    if _indexes_ready:
        return

    async with _indexes_lock:
        if _indexes_ready:
            return

        collection = get_database()[USER_STATES_COLLECTION]
        await collection.create_index(
            [("user_id", ASCENDING)],
            unique=True,
            name="uq_user_states_user_id",
        )
        _indexes_ready = True


async def set_user_state(*, user_id: int, state: UserState) -> None:
    await ensure_user_state_indexes()

    collection = get_database()[USER_STATES_COLLECTION]
    await collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "state": state.value,
                "updated_at": datetime.now(UTC),
            }
        },
        upsert=True,
    )


async def get_user_state(*, user_id: int) -> UserStateRecord | None:
    await ensure_user_state_indexes()

    collection = get_database()[USER_STATES_COLLECTION]
    record = await collection.find_one({"user_id": user_id}, {"_id": 0})
    if record is None:
        return None
    return UserStateRecord.model_validate(record)


async def consume_user_state(*, user_id: int, expected_state: UserState) -> bool:
    await ensure_user_state_indexes()

    collection = get_database()[USER_STATES_COLLECTION]
    deleted = await collection.find_one_and_delete(
        {"user_id": user_id, "state": expected_state.value}
    )
    return deleted is not None


async def clear_user_state(*, user_id: int) -> bool:
    await ensure_user_state_indexes()

    collection = get_database()[USER_STATES_COLLECTION]
    deleted = await collection.find_one_and_delete({"user_id": user_id})
    return deleted is not None
