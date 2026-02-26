import logging
import re
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

logger = logging.getLogger(__name__)

RETRY_AFTER_PATTERN = re.compile(r"retry\s*after\s*(\d+)", re.IGNORECASE)


@dataclass(slots=True)
class ScheduledDeleteEntry:
    chat_id: int
    message_id: int
    due_at: float
    attempt: int = 0


@dataclass(slots=True)
class RetryDeleteEntry:
    entry: ScheduledDeleteEntry
    delay_seconds: float


@dataclass(slots=True)
class DeleteExecutionResult:
    deleted: list[ScheduledDeleteEntry] = field(default_factory=list)
    failed: list[ScheduledDeleteEntry] = field(default_factory=list)
    retry: list[RetryDeleteEntry] = field(default_factory=list)


class DeleteWorker:
    def __init__(
        self,
        *,
        max_batch_size: int = 100,
        max_retry_delay_seconds: float = 45.0,
        base_retry_delay_seconds: float = 1.5,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be >= 1")

        self._max_batch_size = min(max_batch_size, 100)
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._base_retry_delay_seconds = base_retry_delay_seconds
        self._batch_delete_supported: bool | None = None

    async def delete_due_messages(
        self,
        *,
        bot: Bot,
        chat_id: int,
        entries: list[ScheduledDeleteEntry],
    ) -> DeleteExecutionResult:
        result = DeleteExecutionResult()
        if not entries:
            return result

        for chunk in self._chunk_entries(entries):
            chunk_result = await self._delete_chunk(bot=bot, chat_id=chat_id, entries=chunk)
            result.deleted.extend(chunk_result.deleted)
            result.failed.extend(chunk_result.failed)
            result.retry.extend(chunk_result.retry)

        return result

    def _chunk_entries(self, entries: list[ScheduledDeleteEntry]) -> list[list[ScheduledDeleteEntry]]:
        chunks: list[list[ScheduledDeleteEntry]] = []
        for index in range(0, len(entries), self._max_batch_size):
            chunks.append(entries[index : index + self._max_batch_size])
        return chunks

    async def _delete_chunk(
        self,
        *,
        bot: Bot,
        chat_id: int,
        entries: list[ScheduledDeleteEntry],
    ) -> DeleteExecutionResult:
        if self._batch_delete_supported is False:
            return await self._delete_chunk_sequential(bot=bot, chat_id=chat_id, entries=entries)

        delete_messages = getattr(bot, "delete_messages", None)
        if not callable(delete_messages):
            self._batch_delete_supported = False
            return await self._delete_chunk_sequential(bot=bot, chat_id=chat_id, entries=entries)

        message_ids = [entry.message_id for entry in entries]

        try:
            await delete_messages(chat_id=chat_id, message_ids=message_ids)
            self._batch_delete_supported = True
            return DeleteExecutionResult(deleted=entries)
        except (AttributeError, TypeError):
            self._batch_delete_supported = False
            return await self._delete_chunk_sequential(bot=bot, chat_id=chat_id, entries=entries)
        except TelegramBadRequest as exc:
            logger.info(
                "Batch delete fallback to sequential mode",
                extra={"chat_id": chat_id, "error": str(exc), "message_count": len(entries)},
            )
            return await self._delete_chunk_sequential(bot=bot, chat_id=chat_id, entries=entries)
        except TelegramForbiddenError as exc:
            logger.warning(
                "Batch delete failed: missing permission",
                extra={"chat_id": chat_id, "error": str(exc), "message_count": len(entries)},
            )
            return DeleteExecutionResult(failed=entries)
        except TelegramRetryAfter as exc:
            retry_delay = self._bounded_retry_delay(float(exc.retry_after or 1.0))
            retry_entries = [
                RetryDeleteEntry(entry=entry, delay_seconds=self._compute_backoff(entry, retry_delay))
                for entry in entries
            ]
            return DeleteExecutionResult(retry=retry_entries)
        except TelegramAPIError as exc:
            if not self._is_temporary_api_error(exc):
                logger.warning(
                    "Batch delete failed with non-retryable Telegram error",
                    extra={"chat_id": chat_id, "error": str(exc), "message_count": len(entries)},
                )
                return DeleteExecutionResult(failed=entries)

            retry_delay = self._retry_delay_from_error(exc)
            retry_entries = [
                RetryDeleteEntry(
                    entry=entry,
                    delay_seconds=self._compute_backoff(entry, retry_delay),
                )
                for entry in entries
            ]
            return DeleteExecutionResult(retry=retry_entries)
        except Exception:
            logger.exception(
                "Unexpected batch delete failure",
                extra={"chat_id": chat_id, "message_count": len(entries)},
            )
            retry_entries = [
                RetryDeleteEntry(entry=entry, delay_seconds=self._compute_backoff(entry, None))
                for entry in entries
            ]
            return DeleteExecutionResult(retry=retry_entries)

    async def _delete_chunk_sequential(
        self,
        *,
        bot: Bot,
        chat_id: int,
        entries: list[ScheduledDeleteEntry],
    ) -> DeleteExecutionResult:
        result = DeleteExecutionResult()

        for entry in entries:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=entry.message_id)
                result.deleted.append(entry)
            except TelegramBadRequest as exc:
                error_text = str(exc).lower()
                if self._is_already_deleted_or_not_deletable(error_text):
                    result.deleted.append(entry)
                    continue

                logger.warning(
                    "Single delete bad request",
                    extra={
                        "chat_id": chat_id,
                        "message_id": entry.message_id,
                        "error": str(exc),
                    },
                )
                result.failed.append(entry)
            except TelegramForbiddenError as exc:
                logger.warning(
                    "Single delete forbidden",
                    extra={
                        "chat_id": chat_id,
                        "message_id": entry.message_id,
                        "error": str(exc),
                    },
                )
                result.failed.append(entry)
            except TelegramRetryAfter as exc:
                retry_delay = self._compute_backoff(entry, float(exc.retry_after or 1.0))
                result.retry.append(RetryDeleteEntry(entry=entry, delay_seconds=retry_delay))
            except TelegramAPIError as exc:
                if not self._is_temporary_api_error(exc):
                    logger.warning(
                        "Single delete non-retryable Telegram error",
                        extra={
                            "chat_id": chat_id,
                            "message_id": entry.message_id,
                            "error": str(exc),
                        },
                    )
                    result.failed.append(entry)
                    continue

                retry_delay = self._compute_backoff(entry, self._retry_delay_from_error(exc))
                result.retry.append(RetryDeleteEntry(entry=entry, delay_seconds=retry_delay))
            except Exception:
                logger.exception(
                    "Unexpected single delete failure",
                    extra={"chat_id": chat_id, "message_id": entry.message_id},
                )
                result.retry.append(
                    RetryDeleteEntry(
                        entry=entry,
                        delay_seconds=self._compute_backoff(entry, None),
                    )
                )

        return result

    def _compute_backoff(self, entry: ScheduledDeleteEntry, suggested_delay: float | None) -> float:
        exponential_delay = self._base_retry_delay_seconds * (2 ** entry.attempt)
        if suggested_delay is not None:
            exponential_delay = max(exponential_delay, suggested_delay)
        return self._bounded_retry_delay(exponential_delay)

    def _bounded_retry_delay(self, value: float) -> float:
        return max(0.5, min(value, self._max_retry_delay_seconds))

    def _retry_delay_from_error(self, exc: TelegramAPIError) -> float | None:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return self._bounded_retry_delay(float(retry_after))

        error_text = str(exc)
        match = RETRY_AFTER_PATTERN.search(error_text)
        if not match:
            return None

        try:
            return self._bounded_retry_delay(float(match.group(1)))
        except ValueError:
            return None

    def _is_temporary_api_error(self, exc: TelegramAPIError) -> bool:
        if isinstance(exc, TelegramRetryAfter):
            return True

        class_name = exc.__class__.__name__.lower()
        if "network" in class_name or "timeout" in class_name or "server" in class_name:
            return True

        error_text = str(exc).lower()
        return (
            "timeout" in error_text
            or "timed out" in error_text
            or "temporarily unavailable" in error_text
            or "internal server error" in error_text
            or "bad gateway" in error_text
            or "retry after" in error_text
            or "too many requests" in error_text
            or "flood" in error_text
        )

    def _is_already_deleted_or_not_deletable(self, error_text: str) -> bool:
        return (
            "message to delete not found" in error_text
            or "message can't be deleted" in error_text
            or "message can not be deleted" in error_text
            or "message identifier is not specified" in error_text
        )
