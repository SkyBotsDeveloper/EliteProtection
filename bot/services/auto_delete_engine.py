import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from aiogram import Bot
from pymongo import ASCENDING, DeleteOne, UpdateOne
from pymongo.errors import PyMongoError

from bot.db import get_database

from .delete_worker import DeleteWorker, DeleteExecutionResult, ScheduledDeleteEntry

logger = logging.getLogger(__name__)

PENDING_DELETES_COLLECTION = "pending_deletes"


@dataclass(slots=True)
class PendingDeleteMutation:
    op: str
    chat_id: int
    message_id: int
    due_at_utc: datetime | None = None
    attempt: int = 0


@dataclass(slots=True)
class AutoDeleteMetrics:
    scheduled_count: int = 0
    bot_content_scheduled: int = 0
    sticker_scheduled: int = 0
    deleted_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    restored_count: int = 0
    drift_sum_seconds: float = 0.0
    drift_samples: int = 0

    @property
    def average_drift_seconds(self) -> float:
        if self.drift_samples <= 0:
            return 0.0
        return self.drift_sum_seconds / self.drift_samples


class AutoDeleteEngine:
    def __init__(
        self,
        *,
        delete_delay_seconds: int = 45,
        tick_interval_seconds: float = 0.25,
        max_batch_size: int = 100,
        max_retry_attempts: int = 5,
        retry_base_delay_seconds: float = 1.5,
        retry_max_delay_seconds: float = 45.0,
        worker_concurrency: int = 6,
        metrics_log_interval_seconds: int = 60,
        persistence_enabled: bool = False,
        persistence_ttl_hours: int = 24,
        restore_limit: int = 20000,
    ) -> None:
        if delete_delay_seconds < 1:
            raise ValueError("delete_delay_seconds must be >= 1")
        if tick_interval_seconds <= 0:
            raise ValueError("tick_interval_seconds must be > 0")
        if max_retry_attempts < 0:
            raise ValueError("max_retry_attempts must be >= 0")
        if worker_concurrency < 1:
            raise ValueError("worker_concurrency must be >= 1")

        self._delete_delay_seconds = delete_delay_seconds
        self._tick_interval_seconds = tick_interval_seconds
        self._max_retry_attempts = max_retry_attempts
        self._metrics_log_interval_seconds = metrics_log_interval_seconds

        buffer_window_seconds = delete_delay_seconds + retry_max_delay_seconds + 10
        self._bucket_count = max(
            512,
            int(math.ceil(buffer_window_seconds / tick_interval_seconds)) + 1,
        )

        self._delete_worker = DeleteWorker(
            max_batch_size=max_batch_size,
            max_retry_delay_seconds=retry_max_delay_seconds,
            base_retry_delay_seconds=retry_base_delay_seconds,
        )

        self._slots: list[dict[tuple[int, int], ScheduledDeleteEntry]] = [
            {} for _ in range(self._bucket_count)
        ]
        self._entries: dict[tuple[int, int], ScheduledDeleteEntry] = {}
        self._lock = asyncio.Lock()
        self._worker_semaphore = asyncio.Semaphore(worker_concurrency)

        self._metrics = AutoDeleteMetrics()

        self._bot: Bot | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._metrics_task: asyncio.Task[None] | None = None
        self._persistence_task: asyncio.Task[None] | None = None
        self._started = False
        self._shutting_down = False
        self._current_slot = 0

        self._persistence_enabled = persistence_enabled
        self._persistence_ttl_hours = persistence_ttl_hours
        self._restore_limit = restore_limit
        self._persistence_indexes_ready = False
        self._persistence_indexes_lock = asyncio.Lock()
        self._persistence_queue: asyncio.Queue[PendingDeleteMutation] = asyncio.Queue(maxsize=200_000)
        self._persistence_drop_count = 0

    @property
    def delete_delay_seconds(self) -> int:
        return self._delete_delay_seconds

    async def start(self, *, bot: Bot) -> None:
        if self._started:
            self._bot = bot
            return

        self._bot = bot
        self._shutting_down = False
        self._current_slot = self._slot_for_due(monotonic())

        if self._persistence_enabled:
            await self._ensure_persistence_indexes()
            await self._restore_pending_deletes()
            self._persistence_task = asyncio.create_task(self._persistence_loop())

        self._tick_task = asyncio.create_task(self._tick_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        self._started = True

        logger.info(
            "Auto-delete engine started",
            extra={
                "delete_delay_seconds": self._delete_delay_seconds,
                "tick_interval_seconds": self._tick_interval_seconds,
                "bucket_count": self._bucket_count,
                "persistence_enabled": self._persistence_enabled,
            },
        )

    async def shutdown(self) -> None:
        self._shutting_down = True

        tasks: list[asyncio.Task[None]] = []
        if self._tick_task is not None:
            tasks.append(self._tick_task)
        if self._metrics_task is not None:
            tasks.append(self._metrics_task)

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._tick_task = None
        self._metrics_task = None

        if self._persistence_task is not None:
            await self._drain_persistence_queue()
            self._persistence_task.cancel()
            await asyncio.gather(self._persistence_task, return_exceptions=True)
            self._persistence_task = None

        async with self._lock:
            self._entries.clear()
            for slot in self._slots:
                slot.clear()

        self._started = False
        logger.info("Auto-delete engine stopped")

    async def schedule_message_delete(
        self,
        *,
        bot: Bot,
        chat_id: int,
        message_id: int,
        delay_seconds: float | None = None,
        schedule_kind: str = "bot_content",
    ) -> bool:
        if self._shutting_down:
            return False

        if not self._started:
            await self.start(bot=bot)

        self._bot = bot

        requested_delay = self._delete_delay_seconds if delay_seconds is None else max(0.0, delay_seconds)

        scheduled = await self._schedule_entry(
            chat_id=chat_id,
            message_id=message_id,
            delay_seconds=requested_delay,
            attempt=0,
            persist=True,
            restored=False,
            schedule_kind=schedule_kind,
        )
        return scheduled

    async def get_metrics_snapshot(self) -> dict[str, float | int]:
        async with self._lock:
            pending_count = len(self._entries)
            metrics = AutoDeleteMetrics(
                scheduled_count=self._metrics.scheduled_count,
                bot_content_scheduled=self._metrics.bot_content_scheduled,
                sticker_scheduled=self._metrics.sticker_scheduled,
                deleted_count=self._metrics.deleted_count,
                failed_count=self._metrics.failed_count,
                duplicate_count=self._metrics.duplicate_count,
                restored_count=self._metrics.restored_count,
                drift_sum_seconds=self._metrics.drift_sum_seconds,
                drift_samples=self._metrics.drift_samples,
            )

        return {
            "scheduled_count": metrics.scheduled_count,
            "bot_content_scheduled": metrics.bot_content_scheduled,
            "sticker_scheduled": metrics.sticker_scheduled,
            "deleted_count": metrics.deleted_count,
            "failed_count": metrics.failed_count,
            "duplicate_count": metrics.duplicate_count,
            "restored_count": metrics.restored_count,
            "pending_count": pending_count,
            "avg_delay_drift_ms": round(metrics.average_drift_seconds * 1000, 2),
        }

    async def _schedule_entry(
        self,
        *,
        chat_id: int,
        message_id: int,
        delay_seconds: float,
        attempt: int,
        persist: bool,
        restored: bool,
        schedule_kind: str | None,
    ) -> bool:
        key = (chat_id, message_id)
        due_at = monotonic() + delay_seconds
        entry = ScheduledDeleteEntry(
            chat_id=chat_id,
            message_id=message_id,
            due_at=due_at,
            attempt=attempt,
        )

        async with self._lock:
            if key in self._entries:
                self._metrics.duplicate_count += 1
                return False

            slot_index = self._slot_for_due(due_at)
            self._slots[slot_index][key] = entry
            self._entries[key] = entry
            self._metrics.scheduled_count += 1
            if schedule_kind == "bot_content":
                self._metrics.bot_content_scheduled += 1
            elif schedule_kind == "sticker":
                self._metrics.sticker_scheduled += 1
            if restored:
                self._metrics.restored_count += 1

        if self._persistence_enabled and persist:
            self._enqueue_persistence_upsert(
                chat_id=chat_id,
                message_id=message_id,
                due_at_utc=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                attempt=attempt,
            )

        return True

    async def _tick_loop(self) -> None:
        next_tick_at = monotonic()

        while True:
            try:
                now = monotonic()
                sleep_seconds = next_tick_at - now
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

                now = monotonic()
                due_by_chat = await self._collect_due_entries(now=now)
                if due_by_chat:
                    await self._process_due_entries(due_by_chat=due_by_chat)

                next_tick_at += self._tick_interval_seconds
                drift = monotonic() - next_tick_at
                if drift > self._tick_interval_seconds * 3:
                    # Catch up without jumping ring-buffer slots. Jumping slots can
                    # leave due entries waiting for a full wrap-around.
                    next_tick_at = monotonic()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Auto-delete tick loop failed")

    async def _collect_due_entries(self, *, now: float) -> dict[int, list[ScheduledDeleteEntry]]:
        due_by_chat: dict[int, list[ScheduledDeleteEntry]] = {}

        async with self._lock:
            slot_entries = self._slots[self._current_slot]
            self._slots[self._current_slot] = {}
            self._current_slot = (self._current_slot + 1) % self._bucket_count

            due_cutoff = now + (self._tick_interval_seconds / 2)

            for key, entry in slot_entries.items():
                if entry.due_at <= due_cutoff:
                    due_by_chat.setdefault(entry.chat_id, []).append(entry)
                    continue

                future_slot = self._slot_for_due(entry.due_at)
                self._slots[future_slot][key] = entry

        return due_by_chat

    async def _process_due_entries(self, *, due_by_chat: dict[int, list[ScheduledDeleteEntry]]) -> None:
        bot = self._bot
        if bot is None:
            logger.warning("Auto-delete engine has no bot instance while processing due entries")
            return

        tasks: list[asyncio.Task[None]] = []
        for chat_id, entries in due_by_chat.items():
            task = asyncio.create_task(
                self._process_chat_due_entries(bot=bot, chat_id=chat_id, entries=entries)
            )
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_chat_due_entries(
        self,
        *,
        bot: Bot,
        chat_id: int,
        entries: list[ScheduledDeleteEntry],
    ) -> None:
        async with self._worker_semaphore:
            try:
                result = await self._delete_worker.delete_due_messages(
                    bot=bot,
                    chat_id=chat_id,
                    entries=entries,
                )
                await self._apply_delete_result(chat_id=chat_id, result=result)
            except Exception:
                logger.exception(
                    "Failed to process chat due entries",
                    extra={"chat_id": chat_id, "entry_count": len(entries)},
                )

    async def _apply_delete_result(
        self,
        *,
        chat_id: int,
        result: DeleteExecutionResult,
    ) -> None:
        now = monotonic()

        delete_ops: list[tuple[int, int]] = []
        upsert_ops: list[tuple[int, int, datetime, int]] = []

        async with self._lock:
            for entry in result.deleted:
                key = (entry.chat_id, entry.message_id)
                self._entries.pop(key, None)
                self._metrics.deleted_count += 1
                self._metrics.drift_sum_seconds += max(0.0, now - entry.due_at)
                self._metrics.drift_samples += 1
                delete_ops.append(key)

            for entry in result.failed:
                key = (entry.chat_id, entry.message_id)
                self._entries.pop(key, None)
                self._metrics.failed_count += 1
                delete_ops.append(key)

            for retry_item in result.retry:
                entry = retry_item.entry
                key = (entry.chat_id, entry.message_id)

                if key not in self._entries:
                    continue

                next_attempt = entry.attempt + 1
                if next_attempt > self._max_retry_attempts:
                    self._entries.pop(key, None)
                    self._metrics.failed_count += 1
                    delete_ops.append(key)
                    continue

                retry_due_at = monotonic() + retry_item.delay_seconds
                retry_entry = ScheduledDeleteEntry(
                    chat_id=entry.chat_id,
                    message_id=entry.message_id,
                    due_at=retry_due_at,
                    attempt=next_attempt,
                )

                self._entries[key] = retry_entry
                retry_slot = self._slot_for_due(retry_due_at)
                self._slots[retry_slot][key] = retry_entry

                upsert_ops.append(
                    (
                        retry_entry.chat_id,
                        retry_entry.message_id,
                        datetime.now(UTC) + timedelta(seconds=retry_item.delay_seconds),
                        next_attempt,
                    )
                )

        if self._persistence_enabled:
            for chat_key, message_key in delete_ops:
                self._enqueue_persistence_delete(chat_id=chat_key, message_id=message_key)

            for chat_key, message_key, due_at_utc, attempt in upsert_ops:
                self._enqueue_persistence_upsert(
                    chat_id=chat_key,
                    message_id=message_key,
                    due_at_utc=due_at_utc,
                    attempt=attempt,
                )

        if result.retry:
            logger.info(
                "Auto-delete entries scheduled for retry",
                extra={
                    "chat_id": chat_id,
                    "retry_count": len(result.retry),
                    "failed_count": len(result.failed),
                },
            )

    async def _metrics_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._metrics_log_interval_seconds)
                snapshot = await self.get_metrics_snapshot()
                logger.info("Auto-delete metrics", extra=snapshot)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Failed to log auto-delete metrics")

    def _slot_for_due(self, due_at: float) -> int:
        return int(due_at / self._tick_interval_seconds) % self._bucket_count

    async def _ensure_persistence_indexes(self) -> None:
        if self._persistence_indexes_ready:
            return

        async with self._persistence_indexes_lock:
            if self._persistence_indexes_ready:
                return

            collection = get_database()[PENDING_DELETES_COLLECTION]
            await collection.create_index(
                [("chat_id", ASCENDING), ("message_id", ASCENDING)],
                unique=True,
                name="uq_pending_deletes_chat_message",
            )
            await collection.create_index(
                [("due_at", ASCENDING)],
                name="idx_pending_deletes_due_at",
            )
            await collection.create_index(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="ttl_pending_deletes_expires_at",
            )
            self._persistence_indexes_ready = True

    async def _restore_pending_deletes(self) -> None:
        if self._restore_limit <= 0:
            return

        collection = get_database()[PENDING_DELETES_COLLECTION]
        now_utc = datetime.now(UTC)
        restored_count = 0

        cursor = (
            collection.find(
                {},
                {
                    "_id": 0,
                    "chat_id": 1,
                    "message_id": 1,
                    "due_at": 1,
                    "attempt": 1,
                },
            )
            .sort("due_at", ASCENDING)
            .limit(self._restore_limit)
        )

        async for document in cursor:
            chat_id = document.get("chat_id")
            message_id = document.get("message_id")
            due_at = document.get("due_at")
            attempt = document.get("attempt", 0)

            if not isinstance(chat_id, int) or not isinstance(message_id, int):
                continue
            if not isinstance(due_at, datetime):
                due_at = now_utc
            if not isinstance(attempt, int) or attempt < 0:
                attempt = 0

            delay_seconds = max(0.0, (due_at - now_utc).total_seconds())
            scheduled = await self._schedule_entry(
                chat_id=chat_id,
                message_id=message_id,
                delay_seconds=delay_seconds,
                attempt=attempt,
                persist=False,
                restored=True,
                schedule_kind=None,
            )
            if scheduled:
                restored_count += 1

        if restored_count:
            logger.info(
                "Restored pending deletes from Mongo",
                extra={"restored_count": restored_count},
            )

    async def _persistence_loop(self) -> None:
        while True:
            try:
                mutation = await self._persistence_queue.get()
            except asyncio.CancelledError:
                return

            batch = [mutation]
            while len(batch) < 500:
                try:
                    batch.append(self._persistence_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            try:
                await self._flush_persistence_batch(batch)
            except Exception:
                logger.exception(
                    "Failed to flush persistence mutations",
                    extra={"batch_size": len(batch)},
                )

    async def _drain_persistence_queue(self) -> None:
        if self._persistence_queue.empty():
            return

        while True:
            batch: list[PendingDeleteMutation] = []
            while len(batch) < 1000:
                try:
                    batch.append(self._persistence_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not batch:
                return

            try:
                await self._flush_persistence_batch(batch)
            except Exception:
                logger.exception(
                    "Failed to drain pending-delete persistence queue",
                    extra={"batch_size": len(batch)},
                )
                return

    async def _flush_persistence_batch(self, batch: list[PendingDeleteMutation]) -> None:
        operations: list[UpdateOne | DeleteOne] = []

        for mutation in batch:
            key_filter = {"chat_id": mutation.chat_id, "message_id": mutation.message_id}

            if mutation.op == "upsert":
                if mutation.due_at_utc is None:
                    continue

                expires_at = mutation.due_at_utc + timedelta(hours=self._persistence_ttl_hours)
                operations.append(
                    UpdateOne(
                        key_filter,
                        {
                            "$set": {
                                "chat_id": mutation.chat_id,
                                "message_id": mutation.message_id,
                                "due_at": mutation.due_at_utc,
                                "expires_at": expires_at,
                                "attempt": mutation.attempt,
                                "updated_at": datetime.now(UTC),
                            }
                        },
                        upsert=True,
                    )
                )
            elif mutation.op == "delete":
                operations.append(DeleteOne(key_filter))

        if not operations:
            return

        collection = get_database()[PENDING_DELETES_COLLECTION]
        try:
            await collection.bulk_write(operations, ordered=False)
        except PyMongoError:
            logger.exception("Mongo bulk_write failed for pending deletes")

    def _enqueue_persistence_upsert(
        self,
        *,
        chat_id: int,
        message_id: int,
        due_at_utc: datetime,
        attempt: int,
    ) -> None:
        mutation = PendingDeleteMutation(
            op="upsert",
            chat_id=chat_id,
            message_id=message_id,
            due_at_utc=due_at_utc,
            attempt=attempt,
        )
        self._enqueue_persistence_mutation(mutation)

    def _enqueue_persistence_delete(self, *, chat_id: int, message_id: int) -> None:
        mutation = PendingDeleteMutation(
            op="delete",
            chat_id=chat_id,
            message_id=message_id,
        )
        self._enqueue_persistence_mutation(mutation)

    def _enqueue_persistence_mutation(self, mutation: PendingDeleteMutation) -> None:
        try:
            self._persistence_queue.put_nowait(mutation)
        except asyncio.QueueFull:
            self._persistence_drop_count += 1
            if self._persistence_drop_count % 500 == 0:
                logger.warning(
                    "Pending-delete persistence queue full",
                    extra={"dropped_mutations": self._persistence_drop_count},
                )
