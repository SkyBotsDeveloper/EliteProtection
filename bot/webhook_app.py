import logging
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from bot.config import Settings, get_settings
from bot.runtime import (
    build_runtime_context,
    configure_runtime,
    shutdown_infra,
    startup_infra,
)

logger = logging.getLogger(__name__)

_SETTINGS = get_settings()
WEBHOOK_PATH = _SETTINGS.normalized_webhook_path


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    configure_runtime(settings)

    webhook_url = settings.webhook_url
    if webhook_url is None:
        raise RuntimeError("WEBHOOK_MODE=true ke liye WEBHOOK_BASE_URL set karna zaroori hai.")

    runtime = build_runtime_context(settings)

    try:
        await startup_infra(settings, bot=runtime.bot)
        await runtime.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret_token_value,
            allowed_updates=runtime.dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
    except Exception:
        logger.exception("Webhook startup failed")
        await shutdown_infra(bot=runtime.bot)
        raise

    app.state.bot = runtime.bot
    app.state.dp = runtime.dispatcher
    app.state.settings = settings

    logger.info(
        "Webhook startup completed",
        extra={
            "webhook_url": webhook_url,
            "webhook_path": settings.normalized_webhook_path,
        },
    )

    try:
        yield
    finally:
        try:
            await runtime.bot.delete_webhook(drop_pending_updates=False)
        except TelegramAPIError:
            logger.exception("Webhook delete failed during shutdown")

        await shutdown_infra(bot=runtime.bot)
        logger.info("Webhook shutdown completed")


app = FastAPI(title="EliteXprotectorBot Webhook", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    secret_token = settings.webhook_secret_token_value

    if secret_token and x_telegram_bot_api_secret_token != secret_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp

    try:
        update = Update.model_validate(await request.json())
    except Exception:
        logger.warning("Webhook payload invalid", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid update payload") from None

    try:
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Webhook update processing failed")
        raise HTTPException(status_code=500, detail="Update processing failed") from None

    return {"ok": True}

