import logging

from aiogram.enums import ChatType
from aiogram.types import Message

from .auto_delete_service import get_auto_delete_service
from .protected_groups import get_active_protected_group, is_group_protected

logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
DELETE_DELAY_SECONDS = 45


async def schedule_sent_message_if_needed(
    message: Message,
    *,
    schedule_kind: str | None = None,
) -> bool:
    if message.chat.type not in GROUP_CHAT_TYPES:
        return False

    kind = schedule_kind
    if kind is None:
        kind = "sticker" if message.sticker is not None else "bot_content"

    try:
        protected = await is_group_protected(group_id=message.chat.id)
        if not protected:
            protected = (await get_active_protected_group(group_id=message.chat.id)) is not None
        if not protected:
            return False

        return await get_auto_delete_service().schedule_message_delete(
            bot=message.bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            delay_seconds=DELETE_DELAY_SECONDS,
            schedule_kind=kind,
        )
    except Exception:
        logger.exception(
            "Failed to schedule outbound bot message",
            extra={
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "schedule_kind": kind,
            },
        )
        return False
