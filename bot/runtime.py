import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import Settings
from bot.db import close_mongo, connect_to_mongo
from bot.handlers import register_routers
from bot.services import (
    configure_auto_delete_service,
    configure_protected_group_cache,
    ensure_payment_indexes,
    ensure_protected_group_indexes,
    get_auto_delete_service,
    start_auto_delete_service,
    start_protected_group_cache,
    stop_protected_group_cache,
)
from bot.utils import configure_logging

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeContext:
    bot: Bot
    dispatcher: Dispatcher


def configure_runtime(settings: Settings) -> None:
    configure_logging(settings.log_level)
    configure_auto_delete_service(
        delete_delay_seconds=settings.bot_message_delete_delay_seconds,
        tick_interval_seconds=settings.auto_delete_tick_interval_ms / 1000,
        max_batch_size=settings.auto_delete_chunk_size,
        max_retry_attempts=settings.auto_delete_retry_attempts,
        retry_base_delay_seconds=settings.auto_delete_retry_base_seconds,
        retry_max_delay_seconds=settings.auto_delete_retry_max_seconds,
        worker_concurrency=settings.auto_delete_worker_concurrency,
        metrics_log_interval_seconds=settings.auto_delete_metrics_log_interval_seconds,
        persistence_enabled=settings.auto_delete_persistence_enabled,
        persistence_ttl_hours=settings.auto_delete_persistence_ttl_hours,
        restore_limit=settings.auto_delete_restore_limit,
    )
    configure_protected_group_cache(
        refresh_interval_seconds=settings.protected_group_cache_refresh_seconds,
    )


def build_runtime_context(settings: Settings) -> RuntimeContext:
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    register_routers(dispatcher)
    return RuntimeContext(bot=bot, dispatcher=dispatcher)


async def startup_infra(settings: Settings, *, bot: Bot) -> None:
    await connect_to_mongo(uri=settings.mongo_uri, db_name=settings.mongo_db_name)
    await ensure_payment_indexes()
    await ensure_protected_group_indexes()
    await start_protected_group_cache()
    await start_auto_delete_service(bot=bot)


async def shutdown_infra(*, bot: Bot) -> None:
    try:
        await get_auto_delete_service().shutdown()
    except Exception:
        logger.exception("Failed to shutdown auto-delete service")

    try:
        await stop_protected_group_cache()
    except Exception:
        logger.exception("Failed to stop protected-group cache")

    try:
        await close_mongo()
    except Exception:
        logger.exception("Failed to close MongoDB connection")

    try:
        await bot.session.close()
    except Exception:
        logger.exception("Failed to close bot session")
