import asyncio
import logging

from pydantic import ValidationError

from bot.config import Settings, get_settings
from bot.runtime import (
    build_runtime_context,
    configure_runtime,
    shutdown_infra,
    startup_infra,
)

logger = logging.getLogger(__name__)


async def run_polling_bot(settings: Settings) -> None:
    configure_runtime(settings)
    runtime = build_runtime_context(settings)

    try:
        await startup_infra(settings, bot=runtime.bot)
        logger.info("Bot polling startup completed")
        await runtime.dispatcher.start_polling(
            runtime.bot,
            allowed_updates=runtime.dispatcher.resolve_used_update_types(),
        )
    finally:
        await shutdown_infra(bot=runtime.bot)
        logger.info("Bot polling shutdown completed")


def run_webhook_server(settings: Settings) -> None:
    import uvicorn

    if settings.webhook_url is None:
        raise SystemExit(
            "WEBHOOK mode ke liye WEBHOOK_BASE_URL set karo, tabhi webhook URL banega."
        )

    uvicorn.run(
        "bot.webhook_app:app",
        host=settings.web_server_host,
        port=settings.web_server_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise SystemExit(f"Configuration issue mila: {exc}") from exc

    try:
        if settings.resolved_run_mode == "webhook":
            run_webhook_server(settings)
        else:
            asyncio.run(run_polling_bot(settings))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually")


if __name__ == "__main__":
    main()
