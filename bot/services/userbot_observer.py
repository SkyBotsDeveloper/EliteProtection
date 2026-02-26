import asyncio
import logging
import re
from time import monotonic
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings

from .auto_delete_service import get_auto_delete_service
from .protected_groups import (
    get_active_protected_group,
    is_group_protected,
    list_active_group_ids,
)

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError, InviteHashExpiredError, InviteHashInvalidError
    from telethon.errors import UserAlreadyParticipantError
    from telethon.sessions import StringSession
    from telethon.tl.functions.messages import ImportChatInviteRequest
    from telethon.tl.types import PeerChannel, PeerChat
except Exception:  # pragma: no cover - optional runtime dependency guard
    TelegramClient = None
    events = None
    FloodWaitError = None
    InviteHashExpiredError = None
    InviteHashInvalidError = None
    UserAlreadyParticipantError = None
    StringSession = None
    ImportChatInviteRequest = None
    PeerChannel = None
    PeerChat = None

INVITE_HASH_PATTERN = re.compile(
    r"(?:https?://t\.me/(?:\+|joinchat/)|tg://join\?invite=)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

_observer_client: Any | None = None
_observer_bot: Bot | None = None
_observer_lock = asyncio.Lock()
_sync_task: asyncio.Task[None] | None = None
_observer_user_id: int | None = None
_bot_user_id: int | None = None
_joined_groups: set[int] = set()
_retry_after_by_group: dict[int, float] = {}
_known_bot_user_ids: set[int] = set()
_sender_bot_cache: dict[int, tuple[bool, float]] = {}
_sender_bot_cache_ttl_seconds = 600.0


def _status_value(raw_status: object) -> str:
    value = getattr(raw_status, "value", raw_status)
    return str(value).lower()


def _to_bot_api_chat_id(peer_id: Any) -> int | None:
    if PeerChannel is not None and isinstance(peer_id, PeerChannel):
        return int(f"-100{peer_id.channel_id}")
    if PeerChat is not None and isinstance(peer_id, PeerChat):
        return -int(peer_id.chat_id)
    return None


def _extract_invite_hash(invite_link: str) -> str | None:
    match = INVITE_HASH_PATTERN.search(invite_link.strip())
    if match is None:
        return None
    return match.group(1)


async def _is_protected_group(chat_id: int) -> bool:
    protected = await is_group_protected(group_id=chat_id)
    if protected:
        return True
    return (await get_active_protected_group(group_id=chat_id)) is not None


def _cache_sender_is_bot(*, user_id: int, is_bot: bool) -> None:
    _sender_bot_cache[user_id] = (is_bot, monotonic())


def _lookup_cached_sender_is_bot(*, user_id: int) -> bool | None:
    cached = _sender_bot_cache.get(user_id)
    if cached is None:
        return None

    is_bot, cached_at = cached
    if (monotonic() - cached_at) > _sender_bot_cache_ttl_seconds:
        _sender_bot_cache.pop(user_id, None)
        return None
    return is_bot


def _extract_sender_user_id(message: Any) -> int | None:
    sender_id = getattr(message, "sender_id", None)
    if isinstance(sender_id, int):
        return sender_id

    from_id = getattr(message, "from_id", None)
    user_id = getattr(from_id, "user_id", None)
    if isinstance(user_id, int):
        return user_id

    return None


def _sender_object_is_bot(sender: Any) -> bool:
    return bool(getattr(sender, "bot", False) or getattr(sender, "is_bot", False))


def _remember_bot_sender(message: Any, sender: Any) -> None:
    if not _sender_object_is_bot(sender):
        return

    sender_id = _extract_sender_user_id(message)
    if sender_id is None:
        sender_id = getattr(sender, "id", None)

    if isinstance(sender_id, int):
        _known_bot_user_ids.add(sender_id)


def _message_has_sticker(message: Any) -> bool:
    if getattr(message, "sticker", None) is not None:
        return True

    document = getattr(message, "document", None)
    if document is None:
        return False

    mime_type = str(getattr(document, "mime_type", "")).lower()
    if mime_type in {"image/webp", "application/x-tgsticker", "video/webm"}:
        return True

    attributes = getattr(document, "attributes", None) or []
    for attribute in attributes:
        if "sticker" in attribute.__class__.__name__.lower():
            return True

    return False


def _message_has_via_bot(message: Any) -> bool:
    return (
        getattr(message, "via_bot_id", None) is not None
        or getattr(message, "via_business_bot_id", None) is not None
    )


def _forward_origin_is_bot_or_channel(message: Any) -> bool:
    fwd_from = getattr(message, "fwd_from", None)
    if fwd_from is None:
        return False

    from_id = getattr(fwd_from, "from_id", None)
    if from_id is None:
        return False

    if PeerChannel is not None and isinstance(from_id, PeerChannel):
        return True

    user_id = getattr(from_id, "user_id", None)
    return isinstance(user_id, int) and user_id in _known_bot_user_ids


async def _sender_is_bot(event: Any, *, source_message: Any | None = None) -> bool:
    message = source_message if source_message is not None else getattr(event, "message", None)
    if message is None:
        return False

    chat_id = _to_bot_api_chat_id(getattr(message, "peer_id", None))
    sender_id = _extract_sender_user_id(message)
    if sender_id is not None:
        if sender_id in _known_bot_user_ids:
            return True

        cached = _lookup_cached_sender_is_bot(user_id=sender_id)
        if cached is not None:
            if cached:
                _known_bot_user_ids.add(sender_id)
            return cached

    sender = getattr(message, "sender", None)
    if _sender_object_is_bot(sender):
        _remember_bot_sender(message, sender)
        if sender_id is not None:
            _cache_sender_is_bot(user_id=sender_id, is_bot=True)
        return True

    try:
        if source_message is None:
            sender = await event.get_sender()
        else:
            get_sender = getattr(message, "get_sender", None)
            sender = await get_sender() if callable(get_sender) else None
    except Exception:
        sender = None

    if not _sender_object_is_bot(sender):
        if sender_id is not None and chat_id is not None and chat_id < 0 and _observer_bot is not None:
            try:
                member = await _observer_bot.get_chat_member(chat_id=chat_id, user_id=sender_id)
                resolved_is_bot = bool(getattr(getattr(member, "user", None), "is_bot", False))
                _cache_sender_is_bot(user_id=sender_id, is_bot=resolved_is_bot)
                if resolved_is_bot:
                    _known_bot_user_ids.add(sender_id)
                return resolved_is_bot
            except TelegramAPIError:
                pass
            except Exception:
                logger.exception(
                    "Observer sender bot resolution failed",
                    extra={"chat_id": chat_id, "sender_user_id": sender_id},
                )
        return False

    _remember_bot_sender(message, sender)
    if sender_id is not None:
        _cache_sender_is_bot(user_id=sender_id, is_bot=True)
    return True


async def _is_reply_to_bot_or_sticker(event: Any, message: Any) -> bool:
    if not bool(getattr(message, "is_reply", False)):
        return False

    try:
        reply_message = await event.get_reply_message()
    except Exception:
        return False

    if reply_message is None:
        return False
    if _message_has_sticker(reply_message):
        return True
    if _message_has_via_bot(reply_message):
        return True
    if _forward_origin_is_bot_or_channel(reply_message):
        return True
    return await _sender_is_bot(event, source_message=reply_message)


async def _pick_schedule_kind(event: Any, *, message: Any) -> str | None:
    if _message_has_sticker(message):
        return "sticker"

    if _message_has_via_bot(message):
        return "bot_content"
    if _forward_origin_is_bot_or_channel(message):
        return "bot_content"
    if await _sender_is_bot(event, source_message=message):
        return "bot_content"

    return None


async def _bot_can_invite_members(*, chat_id: int) -> bool:
    bot = _observer_bot
    if bot is None or _bot_user_id is None:
        return False

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=_bot_user_id)
    except TelegramAPIError:
        logger.exception(
            "Failed to load bot member status for observer auto-invite",
            extra={"chat_id": chat_id},
        )
        return False

    status = _status_value(getattr(member, "status", ""))
    if status == "creator":
        return True
    if status == "administrator" and bool(getattr(member, "can_invite_users", False)):
        return True
    return False


async def _attempt_auto_join_group(chat_id: int) -> None:
    client = _observer_client
    bot = _observer_bot
    if client is None or bot is None:
        return
    if ImportChatInviteRequest is None:
        return

    if not await _bot_can_invite_members(chat_id=chat_id):
        logger.info(
            "Observer auto-invite skipped: bot missing invite-members permission",
            extra={"chat_id": chat_id},
        )
        _retry_after_by_group[chat_id] = monotonic() + 120.0
        return

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=False,
            name="observer-auto-join",
        )
    except TelegramAPIError:
        logger.exception(
            "Failed to create invite link for observer auto-join",
            extra={"chat_id": chat_id},
        )
        _retry_after_by_group[chat_id] = monotonic() + 120.0
        return

    invite_hash = _extract_invite_hash(invite.invite_link)
    if not invite_hash:
        logger.warning(
            "Invite link hash parse failed for observer auto-join",
            extra={"chat_id": chat_id},
        )
        _retry_after_by_group[chat_id] = monotonic() + 120.0
        return

    try:
        await client(ImportChatInviteRequest(invite_hash))
        _joined_groups.add(chat_id)
        _retry_after_by_group.pop(chat_id, None)
        logger.info(
            "Observer account auto-joined protected group",
            extra={"chat_id": chat_id, "observer_user_id": _observer_user_id},
        )
    except Exception as exc:
        if UserAlreadyParticipantError is not None and isinstance(exc, UserAlreadyParticipantError):
            _joined_groups.add(chat_id)
            _retry_after_by_group.pop(chat_id, None)
            return
        if InviteHashInvalidError is not None and isinstance(exc, InviteHashInvalidError):
            logger.warning(
                "Observer auto-join failed: invite hash invalid",
                extra={"chat_id": chat_id},
            )
            _retry_after_by_group[chat_id] = monotonic() + 180.0
            return
        if InviteHashExpiredError is not None and isinstance(exc, InviteHashExpiredError):
            logger.warning(
                "Observer auto-join failed: invite hash expired",
                extra={"chat_id": chat_id},
            )
            _retry_after_by_group[chat_id] = monotonic() + 180.0
            return
        if FloodWaitError is not None and isinstance(exc, FloodWaitError):
            wait_seconds = float(getattr(exc, "seconds", 30) or 30)
            _retry_after_by_group[chat_id] = monotonic() + max(10.0, wait_seconds)
            logger.warning(
                "Observer auto-join floodwait",
                extra={"chat_id": chat_id, "wait_seconds": wait_seconds},
            )
            return

        logger.exception(
            "Observer auto-join failed",
            extra={"chat_id": chat_id},
        )
        _retry_after_by_group[chat_id] = monotonic() + 180.0


async def _sync_observer_memberships() -> None:
    group_ids = await list_active_group_ids(limit=5000)
    if not group_ids:
        _joined_groups.clear()
        _retry_after_by_group.clear()
        return

    active_groups = set(group_ids)
    _joined_groups.intersection_update(active_groups)
    stale_retry_keys = [chat_id for chat_id in _retry_after_by_group if chat_id not in active_groups]
    for chat_id in stale_retry_keys:
        _retry_after_by_group.pop(chat_id, None)

    now = monotonic()
    for chat_id in group_ids:
        if chat_id in _joined_groups:
            continue

        retry_after = _retry_after_by_group.get(chat_id, 0.0)
        if now < retry_after:
            continue

        await _attempt_auto_join_group(chat_id)


async def _auto_join_sync_loop(*, interval_seconds: int) -> None:
    while True:
        try:
            await _sync_observer_memberships()
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Observer auto-join sync loop failed")
            await asyncio.sleep(interval_seconds)


async def _on_new_message(event: Any) -> None:
    bot = _observer_bot
    if bot is None:
        return

    message = getattr(event, "message", None)
    if message is None:
        return

    if getattr(message, "out", False):
        return

    chat_id = _to_bot_api_chat_id(getattr(message, "peer_id", None))
    if chat_id is None or chat_id >= 0:
        return

    schedule_kind = await _pick_schedule_kind(event, message=message)
    if schedule_kind is None:
        return

    try:
        if not await _is_protected_group(chat_id):
            return

        _joined_groups.add(chat_id)
        _retry_after_by_group.pop(chat_id, None)

        await get_auto_delete_service().schedule_message_delete(
            bot=bot,
            chat_id=chat_id,
            message_id=message.id,
            schedule_kind=schedule_kind,
        )
    except Exception:
        logger.exception(
            "Userbot observer scheduling failed",
            extra={
                "chat_id": chat_id,
                "message_id": getattr(message, "id", None),
                "schedule_kind": schedule_kind,
            },
        )


async def start_userbot_observer(*, settings: Settings, bot: Bot) -> None:
    global _observer_client, _observer_bot, _sync_task, _observer_user_id, _bot_user_id

    active_groups_count = 0
    try:
        active_groups_count = len(await list_active_group_ids(limit=10000))
    except Exception:
        logger.exception("Failed to load active group count before observer startup")

    if not settings.observer_effective_enabled:
        logger.warning(
            "Userbot observer disabled; other-bot messages are not visible without observer credentials",
            extra={
                "observer_enabled": settings.observer_enabled,
                "active_groups_count": active_groups_count,
            },
        )
        return

    if TelegramClient is None or events is None or StringSession is None:
        logger.warning("Userbot observer unavailable: telethon import failed")
        return

    missing_fields = settings.observer_missing_fields
    if missing_fields:
        logger.warning(
            "Userbot observer not started: missing required observer credentials",
            extra={
                "missing_fields": ",".join(missing_fields),
                "active_groups_count": active_groups_count,
            },
        )
        return

    api_id = settings.observer_api_id
    api_hash = settings.observer_api_hash_value
    session_string = settings.observer_session_string_value
    if api_id is None or api_hash is None or session_string is None:
        # Defensive guard; missing fields should already be handled above.
        return

    async with _observer_lock:
        _observer_bot = bot
        if _bot_user_id is None:
            try:
                bot_me = await bot.get_me()
                _bot_user_id = bot_me.id
            except TelegramAPIError:
                logger.exception("Failed to fetch bot profile for observer module")

        if _observer_client is not None:
            return

        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            logger.error(
                "Userbot observer not authorized. Generate OBSERVER_SESSION_STRING with a logged-in account.",
            )
            return

        client.add_event_handler(_on_new_message, events.NewMessage(incoming=True))
        client.add_event_handler(_on_new_message, events.MessageEdited(incoming=True))

        _observer_client = client
        observer_user = await client.get_me()
        _observer_user_id = getattr(observer_user, "id", None)

        logger.info(
            "Userbot observer started",
            extra={
                "observer_user_id": _observer_user_id,
                "observer_username": getattr(observer_user, "username", None),
            },
        )

        await _sync_observer_memberships()
        _sync_task = asyncio.create_task(
            _auto_join_sync_loop(interval_seconds=settings.observer_sync_interval_seconds)
        )


async def stop_userbot_observer() -> None:
    global _observer_client, _observer_bot, _sync_task, _observer_user_id

    async with _observer_lock:
        client = _observer_client
        sync_task = _sync_task
        _observer_client = None
        _observer_bot = None
        _sync_task = None
        _observer_user_id = None
        _joined_groups.clear()
        _retry_after_by_group.clear()
        _known_bot_user_ids.clear()
        _sender_bot_cache.clear()

    if sync_task is not None:
        sync_task.cancel()
        await asyncio.gather(sync_task, return_exceptions=True)

    if client is None:
        return

    try:
        await client.disconnect()
        logger.info("Userbot observer stopped")
    except Exception:
        logger.exception("Failed to stop userbot observer")
