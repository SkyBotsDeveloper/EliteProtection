import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.services import get_auto_delete_service, is_group_protected

router = Router(name="auto_delete")
GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
logger = logging.getLogger(__name__)


def _is_forwarded_from_bot(message: Message) -> bool:
    forward_from = getattr(message, "forward_from", None)
    if forward_from is not None and getattr(forward_from, "is_bot", False):
        return True

    forward_origin = getattr(message, "forward_origin", None)
    sender_user = getattr(forward_origin, "sender_user", None)
    if sender_user is not None and getattr(sender_user, "is_bot", False):
        return True

    sender_chat = getattr(forward_origin, "sender_chat", None)
    sender_chat_type = str(getattr(sender_chat, "type", "")).lower()
    if sender_chat is not None and sender_chat_type in {"bot", "channel"}:
        return True

    return False


def _is_sender_context_bot(message: Message) -> bool:
    sender_chat = getattr(message, "sender_chat", None)
    sender_chat_type = str(getattr(sender_chat, "type", "")).lower()
    if sender_chat is not None and sender_chat_type in {"bot", "channel"}:
        return True

    if getattr(message, "is_automatic_forward", False):
        return True

    sender_business_bot = getattr(message, "sender_business_bot", None)
    if sender_business_bot is not None and getattr(sender_business_bot, "is_bot", False):
        return True

    return False


def _is_reply_to_bot_content(message: Message) -> bool:
    reply_to_message = getattr(message, "reply_to_message", None)
    if reply_to_message is not None:
        if is_bot_generated_message(reply_to_message):
            return True
        if getattr(reply_to_message, "is_automatic_forward", False):
            return True

    external_reply = getattr(message, "external_reply", None)
    reply_origin = getattr(external_reply, "origin", None)

    sender_user = getattr(reply_origin, "sender_user", None)
    if sender_user is not None and getattr(sender_user, "is_bot", False):
        return True

    sender_chat = getattr(reply_origin, "sender_chat", None)
    sender_chat_type = str(getattr(sender_chat, "type", "")).lower()
    if sender_chat is not None and sender_chat_type in {"bot", "channel"}:
        return True

    return False


def is_bot_generated_message(message: Message) -> bool:
    from_user = message.from_user
    if from_user is not None and from_user.is_bot:
        return True

    if _is_sender_context_bot(message):
        return True

    if getattr(message, "via_bot", None) is not None:
        return True

    if _is_forwarded_from_bot(message):
        return True

    return False


def _pick_schedule_kind(message: Message) -> str | None:
    if message.sticker is not None:
        return "sticker"

    if is_bot_generated_message(message) or _is_reply_to_bot_content(message):
        return "bot_content"

    return None


async def _schedule_if_eligible(message: Message) -> None:
    schedule_kind = _pick_schedule_kind(message)
    if schedule_kind is None:
        return

    try:
        if not await is_group_protected(group_id=message.chat.id):
            return

        await get_auto_delete_service().schedule_message_delete(
            bot=message.bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            schedule_kind=schedule_kind,
        )
    except Exception:
        logger.exception(
            "Auto-delete handler failed",
            extra={
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "schedule_kind": schedule_kind,
            },
        )


@router.message(F.chat.type.in_(GROUP_CHAT_TYPES))
async def auto_delete_bot_messages(message: Message) -> None:
    await _schedule_if_eligible(message)


@router.edited_message(F.chat.type.in_(GROUP_CHAT_TYPES))
async def auto_delete_edited_bot_messages(message: Message) -> None:
    await _schedule_if_eligible(message)
