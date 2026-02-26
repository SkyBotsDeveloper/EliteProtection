import logging
from datetime import UTC

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from bot.services import get_active_protected_group
from bot.utils import GENERIC_HANDLER_ERROR_TEXT

router = Router(name="group_setup")
logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
BOT_ACTIVE_STATUSES = {"member", "administrator"}
BOT_INACTIVE_STATUSES = {"left", "kicked"}


def _status_value(raw_status: object) -> str:
    value = getattr(raw_status, "value", raw_status)
    return str(value).lower()


def _is_bot_added_event(update: ChatMemberUpdated) -> bool:
    old_status = _status_value(update.old_chat_member.status)
    new_status = _status_value(update.new_chat_member.status)
    return old_status in BOT_INACTIVE_STATUSES and new_status in BOT_ACTIVE_STATUSES


def _delete_permission_status(chat_member_status: str, can_delete_messages: bool | None) -> str:
    if chat_member_status in {"creator", "administrator"}:
        if chat_member_status == "creator":
            return "Haan ✅"
        if can_delete_messages:
            return "Haan ✅"
        return "Nahi ❌"
    return "Nahi ❌"


def _read_messages_status(can_read_all_group_messages: bool | None) -> str:
    if can_read_all_group_messages:
        return "Full access ✅ (privacy mode off)"
    return "Limited access ⚠️ (privacy mode on ho sakta hai)"


async def _safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text)
    except TelegramAPIError:
        logger.exception(
            "Failed to send group setup reply",
            extra={"chat_id": message.chat.id, "user_id": getattr(message.from_user, "id", None)},
        )


@router.message(Command("check"), F.chat.type.in_(GROUP_CHAT_TYPES))
async def check_group_setup(message: Message) -> None:
    chat = message.chat

    try:
        protected_group = await get_active_protected_group(group_id=chat.id)
        me = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(chat_id=chat.id, user_id=me.id)
    except TelegramAPIError:
        logger.exception("Telegram API failed during /check", extra={"chat_id": chat.id})
        await _safe_reply(
            message,
            "Setup check abhi complete nahi ho paya. Bot permissions aur network dubara check karke /check phir chalayo.",
        )
        return
    except Exception:
        logger.exception("Unexpected error during /check", extra={"chat_id": chat.id})
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    subscription_text = "Active ✅" if protected_group else "Active nahi ❌"
    delete_perm_text = _delete_permission_status(
        chat_member_status=_status_value(bot_member.status),
        can_delete_messages=getattr(bot_member, "can_delete_messages", None),
    )
    read_messages_text = _read_messages_status(getattr(me, "can_read_all_group_messages", None))

    await _safe_reply(
        message,
        (
            "Setup ka status summary:\n"
            f"1) Subscription/Protection: {subscription_text}\n"
            f"2) Delete messages permission: {delete_perm_text}\n"
            f"3) Group messages read access (padhne ki permission): {read_messages_text}\n\n"
            "Note: Agar privacy mode on ho to bot sirf limited messages read kar pata hai."
        ),
    )


@router.message(Command("status"), F.chat.type.in_(GROUP_CHAT_TYPES))
async def group_status(message: Message) -> None:
    chat = message.chat

    try:
        protected_group = await get_active_protected_group(group_id=chat.id)
    except Exception:
        logger.exception("Failed to load protection status", extra={"chat_id": chat.id})
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if protected_group is None:
        await _safe_reply(message, "Protection abhi active nahi hai. Ye group subscribed nahi hai.")
        return

    activated_at_utc = protected_group.activated_at.astimezone(UTC)
    activated_at_text = activated_at_utc.strftime("%d %b %Y %H:%M UTC")

    await _safe_reply(
        message,
        (
            "Protection active hai ✅\n"
            f"Owner User ID: <code>{protected_group.owner_user_id}</code>\n"
            f"Activation Time: {activated_at_text}"
        ),
    )


@router.my_chat_member(F.chat.type.in_(GROUP_CHAT_TYPES))
async def on_bot_added_to_group(event: ChatMemberUpdated) -> None:
    if not _is_bot_added_event(event):
        return

    try:
        protected_group = await get_active_protected_group(group_id=event.chat.id)
    except Exception:
        logger.exception(
            "Failed to check subscription on bot added event",
            extra={"chat_id": event.chat.id},
        )
        return

    if protected_group is not None:
        return

    try:
        await event.bot.send_message(
            chat_id=event.chat.id,
            text=(
                "Namaste! Ye group subscribed nahi hai, isliye protection abhi active nahi hoga.\n"
                "Subscription ke liye owner ko bot DM me /start karke process complete karna hoga."
            ),
        )
    except TelegramAPIError:
        logger.exception(
            "Failed to send non-subscribed group notice",
            extra={"chat_id": event.chat.id},
        )

