import asyncio
import logging

from bot.db import get_database
from bot.db.models import SubscriptionStatus

PROTECTED_GROUPS_COLLECTION = "protected_groups"

logger = logging.getLogger(__name__)


class GroupCache:
    def __init__(self, *, refresh_interval_seconds: int = 30) -> None:
        if refresh_interval_seconds < 5:
            raise ValueError("refresh_interval_seconds must be >= 5")

        self._refresh_interval_seconds = refresh_interval_seconds
        self._group_ids: set[int] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._ready = False
        self._started = False

    def configure(self, *, refresh_interval_seconds: int) -> None:
        if refresh_interval_seconds < 5:
            raise ValueError("refresh_interval_seconds must be >= 5")

        self._refresh_interval_seconds = refresh_interval_seconds

    async def start(self) -> None:
        if self._started:
            return

        await self.refresh()
        self._started = True
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        self._started = False

        if self._task is None:
            return

        task = self._task
        self._task = None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def refresh(self) -> None:
        collection = get_database()[PROTECTED_GROUPS_COLLECTION]
        cursor = collection.find(
            {"subscription_status": SubscriptionStatus.ACTIVE.value},
            {"_id": 0, "group_id": 1},
        )

        active_group_ids: set[int] = set()
        async for document in cursor:
            group_id = document.get("group_id")
            if isinstance(group_id, int):
                active_group_ids.add(group_id)

        async with self._lock:
            self._group_ids.clear()
            self._group_ids.update(active_group_ids)
            self._ready = True

        logger.info(
            "Protected group cache refreshed",
            extra={"active_groups": len(active_group_ids)},
        )

    async def is_protected(self, *, group_id: int) -> bool:
        if not self._ready:
            await self.refresh()

        async with self._lock:
            return group_id in self._group_ids

    async def count(self) -> int:
        if not self._ready:
            await self.refresh()

        async with self._lock:
            return len(self._group_ids)

    async def mark_group_active(self, *, group_id: int) -> None:
        async with self._lock:
            self._group_ids.add(group_id)
            self._ready = True

    async def mark_group_inactive(self, *, group_id: int) -> None:
        async with self._lock:
            self._group_ids.discard(group_id)
            self._ready = True

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._refresh_interval_seconds)
                await self.refresh()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Failed to refresh protected group cache")


_group_cache = GroupCache()


def configure_group_cache(*, refresh_interval_seconds: int) -> None:
    _group_cache.configure(refresh_interval_seconds=refresh_interval_seconds)


async def start_group_cache() -> None:
    await _group_cache.start()


async def stop_group_cache() -> None:
    await _group_cache.stop()


async def refresh_group_cache() -> None:
    await _group_cache.refresh()


async def is_group_protected_cached(*, group_id: int) -> bool:
    return await _group_cache.is_protected(group_id=group_id)


async def count_group_cache() -> int:
    return await _group_cache.count()


async def mark_group_active_cached(*, group_id: int) -> None:
    await _group_cache.mark_group_active(group_id=group_id)


async def mark_group_inactive_cached(*, group_id: int) -> None:
    await _group_cache.mark_group_inactive(group_id=group_id)
