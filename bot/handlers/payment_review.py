import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from bot.config import get_settings
from bot.db.models import PaymentStatus, UserState
from bot.keyboards import PaymentReviewAction, PaymentReviewCallback
from bot.services import is_valid_payment_id, set_user_state, update_payment_status
from bot.utils import (
    APPROVED_PAYMENT_DM_TEXT,
    DENIED_PAYMENT_DM_TEXT,
    GENERIC_HANDLER_ERROR_TEXT,
)

router = Router(name="payment_review")
logger = logging.getLogger(__name__)


def _status_text(action: PaymentReviewAction) -> str:
    if action == PaymentReviewAction.APPROVE:
        return "Approve ho gaya ✅"
    return "Deny ho gaya ❌"


def _owner_result_text(action: PaymentReviewAction, dm_sent: bool) -> str:
    if action == PaymentReviewAction.APPROVE:
        if dm_sent:
            return "Payment request approve kar diya gaya ✅."
        return "Payment request approve ho gaya ✅, lekin user ko DM nahi gaya."

    if dm_sent:
        return "Payment request deny kar diya gaya ❌."
    return "Payment request deny ho gaya ❌, lekin user ko DM nahi gaya."


async def _safe_callback_answer(
    callback: CallbackQuery,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramAPIError:
        logger.exception(
            "Failed to answer payment review callback",
            extra={"from_user_id": callback.from_user.id},
        )


@router.callback_query(PaymentReviewCallback.filter())
async def payment_review_callback(
    callback: CallbackQuery,
    callback_data: PaymentReviewCallback,
) -> None:
    settings = get_settings()

    if callback.from_user.id != settings.owner_user_id:
        await _safe_callback_answer(
            callback,
            f"Access mana hai. Sirf @{settings.owner_username} approve ya deny kar sakta hai.",
            show_alert=True,
        )
        return

    if callback.message is None or callback.message.chat.id != settings.admin_review_chat_id:
        await _safe_callback_answer(
            callback,
            "Yeh action sirf admin review channel me valid hai.",
            show_alert=True,
        )
        return

    normalized_payment_id = callback_data.payment_id.strip().lower()
    if not is_valid_payment_id(normalized_payment_id):
        await _safe_callback_answer(
            callback,
            "Callback data invalid hai, action cancel kar diya gaya.",
            show_alert=True,
        )
        return

    if callback_data.action == PaymentReviewAction.APPROVE:
        target_status = PaymentStatus.APPROVED
        dm_text = APPROVED_PAYMENT_DM_TEXT
    else:
        target_status = PaymentStatus.DENIED
        dm_text = DENIED_PAYMENT_DM_TEXT

    try:
        payment = await update_payment_status(
            payment_id=normalized_payment_id,
            status=target_status,
        )
    except Exception:
        logger.exception(
            "Failed to update payment status from admin review callback",
            extra={"payment_id": normalized_payment_id, "action": callback_data.action.value},
        )
        await _safe_callback_answer(callback, GENERIC_HANDLER_ERROR_TEXT, show_alert=True)
        return

    if payment is None:
        await _safe_callback_answer(
            callback,
            "Yeh request pending me nahi hai ya pehle process ho chuki hai.",
            show_alert=True,
        )
        return

    if target_status == PaymentStatus.APPROVED:
        try:
            await set_user_state(user_id=payment.user_id, state=UserState.AWAITING_GROUP_ID)
        except Exception:
            logger.exception(
                "Failed to set awaiting_group_id state after approval",
                extra={"payment_id": payment.payment_id, "user_id": payment.user_id},
            )
            await _safe_callback_answer(
                callback,
                "Payment approve hua, lekin user state update me issue aaya.",
                show_alert=True,
            )
            return

    dm_sent = True
    try:
        await callback.bot.send_message(chat_id=payment.user_id, text=dm_text)
    except TelegramAPIError:
        dm_sent = False
        logger.exception(
            "Failed to send payment decision DM",
            extra={
                "payment_id": payment.payment_id,
                "user_id": payment.user_id,
                "status": target_status.value,
            },
        )

    status_text = _status_text(callback_data.action)
    existing_text = callback.message.text or ""
    updated_lines = []
    replaced_status = False

    for line in existing_text.splitlines():
        if line.lower().startswith("status:"):
            updated_lines.append(f"Status: {status_text}")
            replaced_status = True
        else:
            updated_lines.append(line)

    if not replaced_status:
        updated_lines.append(f"Status: {status_text}")

    if not dm_sent:
        updated_lines.append("DM Status: User ko DM send nahi ho paya (blocked ya privacy issue).")

    try:
        await callback.message.edit_text("\n".join(updated_lines), reply_markup=None)
    except TelegramAPIError:
        logger.exception(
            "Failed to edit admin review message",
            extra={"payment_id": payment.payment_id, "chat_id": callback.message.chat.id},
        )

    await _safe_callback_answer(
        callback,
        _owner_result_text(callback_data.action, dm_sent),
        show_alert=not dm_sent,
    )
