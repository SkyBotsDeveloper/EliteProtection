import logging
from datetime import UTC

from aiogram import F, Router, html
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import get_settings
from bot.db.models import PaymentStatus, UserState
from bot.services import (
    RevokeProtectedGroupStatus,
    get_owner_stats,
    is_valid_payment_id,
    list_pending_payment_requests,
    parse_group_chat_id,
    revoke_protected_group,
    set_user_state,
    update_payment_status,
)
from bot.utils import (
    APPROVED_PAYMENT_DM_TEXT,
    DENIED_PAYMENT_DM_TEXT,
    GENERIC_HANDLER_ERROR_TEXT,
)

router = Router(name="owner_commands")
logger = logging.getLogger(__name__)


async def _safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text)
    except TelegramAPIError:
        logger.exception(
            "Failed to send owner command reply",
            extra={"owner_user_id": getattr(message.from_user, "id", None)},
        )


async def _ensure_owner(message: Message) -> bool:
    settings = get_settings()
    from_user = message.from_user
    if from_user and from_user.id == settings.owner_user_id:
        return True

    await _safe_reply(message, "Ye command sirf owner ke liye hai.")
    return False


async def _send_decision_dm(
    *,
    message: Message,
    target_user_id: int,
    text: str,
    payment_id: str,
    action: str,
) -> bool:
    try:
        await message.bot.send_message(chat_id=target_user_id, text=text)
        return True
    except TelegramAPIError:
        logger.exception(
            "Failed to send manual owner decision DM",
            extra={
                "target_user_id": target_user_id,
                "payment_id": payment_id,
                "action": action,
            },
        )
        return False


@router.message(Command("pending"), F.chat.type == ChatType.PRIVATE)
async def pending_command(message: Message) -> None:
    if not await _ensure_owner(message):
        return

    try:
        pending_requests = await list_pending_payment_requests(limit=25)
    except Exception:
        logger.exception("Owner /pending failed")
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if not pending_requests:
        await _safe_reply(message, "Abhi koi pending payment request nahi hai.")
        return

    lines = ["Pending payment requests (latest 25) yahan diye gaye hain:"]
    for request in pending_requests:
        username_text = f"@{html.quote(request.username)}" if request.username else "Nahi diya"
        full_name_text = html.quote(request.full_name)
        created_at_text = request.created_at.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")

        lines.append(
            (
                f"- Payment ID: <code>{request.payment_id}</code>\n"
                f"  User ID: <code>{request.user_id}</code>\n"
                f"  Username: {username_text}\n"
                f"  Pura Naam: {full_name_text}\n"
                "  Status: Pending\n"
                f"  Request Time: {created_at_text}"
            )
        )

    await _safe_reply(message, "\n\n".join(lines))


@router.message(Command("approve"), F.chat.type == ChatType.PRIVATE)
async def approve_command(message: Message, command: CommandObject) -> None:
    if not await _ensure_owner(message):
        return

    payment_id = (command.args or "").strip().lower()
    if not payment_id:
        await _safe_reply(message, "Ye format use karo: /approve <payment_id>")
        return

    if not is_valid_payment_id(payment_id):
        await _safe_reply(message, "Payment ID invalid hai. Sahi payment_id bhejo.")
        return

    try:
        payment = await update_payment_status(payment_id=payment_id, status=PaymentStatus.APPROVED)
    except Exception:
        logger.exception("Owner /approve status update failed", extra={"payment_id": payment_id})
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if payment is None:
        await _safe_reply(message, "Ye payment pending me nahi mila ya pehle process ho chuka hai.")
        return

    try:
        await set_user_state(user_id=payment.user_id, state=UserState.AWAITING_GROUP_ID)
    except Exception:
        logger.exception(
            "Owner /approve user state update failed",
            extra={"payment_id": payment.payment_id, "user_id": payment.user_id},
        )
        await _safe_reply(
            message,
            (
                "Payment approve ho gaya ✅, lekin user state update me issue aa gaya.\n"
                f"Payment ID: <code>{payment.payment_id}</code>"
            ),
        )
        return

    dm_sent = await _send_decision_dm(
        message=message,
        target_user_id=payment.user_id,
        text=APPROVED_PAYMENT_DM_TEXT,
        payment_id=payment.payment_id,
        action="approve",
    )

    if dm_sent:
        await _safe_reply(
            message,
            f"Payment approve ho gaya ✅\nPayment ID: <code>{payment.payment_id}</code>",
        )
    else:
        await _safe_reply(
            message,
            (
                "Payment approve ho gaya ✅, lekin user ko DM nahi gaya.\n"
                f"Payment ID: <code>{payment.payment_id}</code>"
            ),
        )


@router.message(Command("deny"), F.chat.type == ChatType.PRIVATE)
async def deny_command(message: Message, command: CommandObject) -> None:
    if not await _ensure_owner(message):
        return

    payment_id = (command.args or "").strip().lower()
    if not payment_id:
        await _safe_reply(message, "Ye format use karo: /deny <payment_id>")
        return

    if not is_valid_payment_id(payment_id):
        await _safe_reply(message, "Payment ID invalid hai. Sahi payment_id bhejo.")
        return

    try:
        payment = await update_payment_status(payment_id=payment_id, status=PaymentStatus.DENIED)
    except Exception:
        logger.exception("Owner /deny status update failed", extra={"payment_id": payment_id})
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if payment is None:
        await _safe_reply(message, "Ye payment pending me nahi mila ya pehle process ho chuka hai.")
        return

    dm_sent = await _send_decision_dm(
        message=message,
        target_user_id=payment.user_id,
        text=DENIED_PAYMENT_DM_TEXT,
        payment_id=payment.payment_id,
        action="deny",
    )

    if dm_sent:
        await _safe_reply(
            message,
            f"Payment deny ho gaya ❌\nPayment ID: <code>{payment.payment_id}</code>",
        )
    else:
        await _safe_reply(
            message,
            (
                "Payment deny ho gaya ❌, lekin user ko DM nahi gaya.\n"
                f"Payment ID: <code>{payment.payment_id}</code>"
            ),
        )


@router.message(Command("revoke"), F.chat.type == ChatType.PRIVATE)
async def revoke_command(message: Message, command: CommandObject) -> None:
    if not await _ensure_owner(message):
        return

    group_id_text = (command.args or "").strip()
    if not group_id_text:
        await _safe_reply(message, "Ye format use karo: /revoke <group_id>")
        return

    group_id = parse_group_chat_id(group_id_text)
    if group_id is None:
        await _safe_reply(message, "Group ID invalid hai. Example format: -1001234567890")
        return

    try:
        result = await revoke_protected_group(group_id=group_id)
    except Exception:
        logger.exception("Owner /revoke failed", extra={"group_id": group_id})
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if result.status == RevokeProtectedGroupStatus.REVOKED:
        await _safe_reply(message, f"Group protection disable ho gaya ✅\nGroup ID: <code>{group_id}</code>")
        return

    if result.status == RevokeProtectedGroupStatus.ALREADY_REVOKED:
        await _safe_reply(
            message,
            f"Is group ka protection pehle se disable hai.\nGroup ID: <code>{group_id}</code>",
        )
        return

    await _safe_reply(message, f"Group record nahi mila.\nGroup ID: <code>{group_id}</code>")


@router.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
async def stats_command(message: Message) -> None:
    if not await _ensure_owner(message):
        return

    try:
        stats = await get_owner_stats()
    except Exception:
        logger.exception("Owner /stats failed")
        await _safe_reply(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    await _safe_reply(
        message,
        (
            "Owner stats ka summary:\n"
            f"- Total users: <code>{stats.total_users}</code>\n"
            f"- Active groups: <code>{stats.active_groups}</code>\n"
            f"- Pending payments: <code>{stats.pending_payments}</code>"
        ),
    )

