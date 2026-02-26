from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CHECK_SETUP_CALLBACK = "setup:check"


def check_setup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Check Setup",
                    callback_data=CHECK_SETUP_CALLBACK,
                )
            ]
        ]
    )
