from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

SUBSCRIPTION_BUY_CALLBACK = "start:subscription_buy"
HELP_CALLBACK = "start:help"
HOW_IT_WORKS_CALLBACK = "start:how_it_works"
MY_SUBSCRIPTION_CALLBACK = "start:my_subscription"
FLOW_CANCEL_CALLBACK = "start:flow_cancel"


def start_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Subscription Kharido",
                    callback_data=SUBSCRIPTION_BUY_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Meri Subscription",
                    callback_data=MY_SUBSCRIPTION_CALLBACK,
                ),
                InlineKeyboardButton(text="Madad", callback_data=HELP_CALLBACK),
            ],
            [
                InlineKeyboardButton(
                    text="Kaise Kaam Karta Hai",
                    callback_data=HOW_IT_WORKS_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Flow Cancel Karo",
                    callback_data=FLOW_CANCEL_CALLBACK,
                )
            ],
        ]
    )
