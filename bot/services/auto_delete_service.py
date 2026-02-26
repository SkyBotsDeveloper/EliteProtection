from aiogram import Bot

from .auto_delete_engine import AutoDeleteEngine

_auto_delete_service = AutoDeleteEngine()


def configure_auto_delete_service(
    *,
    delete_delay_seconds: int,
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
    global _auto_delete_service
    _auto_delete_service = AutoDeleteEngine(
        delete_delay_seconds=delete_delay_seconds,
        tick_interval_seconds=tick_interval_seconds,
        max_batch_size=max_batch_size,
        max_retry_attempts=max_retry_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        retry_max_delay_seconds=retry_max_delay_seconds,
        worker_concurrency=worker_concurrency,
        metrics_log_interval_seconds=metrics_log_interval_seconds,
        persistence_enabled=persistence_enabled,
        persistence_ttl_hours=persistence_ttl_hours,
        restore_limit=restore_limit,
    )


async def start_auto_delete_service(*, bot: Bot) -> None:
    await _auto_delete_service.start(bot=bot)


def get_auto_delete_service() -> AutoDeleteEngine:
    return _auto_delete_service
