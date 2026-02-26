from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAYMENT_DONE_CALLBACK = "payment:done"
PAYMENT_CANCEL_CALLBACK = "payment:cancel"


def payment_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Done ✅", callback_data=PAYMENT_DONE_CALLBACK),
                InlineKeyboardButton(text="Cancel ❌", callback_data=PAYMENT_CANCEL_CALLBACK),
            ]
        ]
    )
