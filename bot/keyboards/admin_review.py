from enum import Enum

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class PaymentReviewAction(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class PaymentReviewCallback(CallbackData, prefix="payment_review"):
    action: PaymentReviewAction
    payment_id: str


def payment_review_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Approve ✅",
                    callback_data=PaymentReviewCallback(
                        action=PaymentReviewAction.APPROVE,
                        payment_id=payment_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Deny ❌",
                    callback_data=PaymentReviewCallback(
                        action=PaymentReviewAction.DENY,
                        payment_id=payment_id,
                    ).pack(),
                ),
            ]
        ]
    )
