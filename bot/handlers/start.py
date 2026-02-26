import logging
from datetime import UTC

from aiogram import F, Router, html
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db.models import UserState
from bot.keyboards import (
    CHECK_SETUP_CALLBACK,
    FLOW_CANCEL_CALLBACK,
    HELP_CALLBACK,
    HOW_IT_WORKS_CALLBACK,
    MY_SUBSCRIPTION_CALLBACK,
    PAYMENT_CANCEL_CALLBACK,
    PAYMENT_DONE_CALLBACK,
    SUBSCRIPTION_BUY_CALLBACK,
    check_setup_keyboard,
    payment_action_keyboard,
    payment_review_keyboard,
    start_menu_keyboard,
)
from bot.services import (
    BindProtectedGroupStatus,
    CreatePendingPaymentStatus,
    bind_protected_group,
    clear_user_state,
    consume_user_state,
    create_pending_payment_request,
    get_pending_payment_request_by_user,
    get_user_state,
    is_done_click_allowed,
    list_active_groups_by_owner,
    parse_group_chat_id,
    set_user_state,
)
from bot.utils import GENERIC_HANDLER_ERROR_TEXT

router = Router(name="start")
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Madad guide (simple steps):\n"
    "1) /start dabao aur Subscription Kharido choose karo\n"
    "2) Payment ke baad Done ✅ dabao\n"
    "3) Admin approval ke baad group chat ID bhejo\n"
    "4) Bot ko group me add karke admin permissions do\n"
    "5) Protected group me sab stickers bhi 35 second baad auto-delete honge"
)

HOW_IT_WORKS_TEXT = (
    "Kaise kaam karta hai:\n"
    "1) Group subscribed aur active hona chahiye\n"
    "2) Setup complete hone ke baad protection apply hota hai\n"
    "3) Group me /check aur /status se setup verify kar sakte ho\n"
    "Yaad rahe: 1 subscription = 1 group"
)


async def _safe_message_answer(message: Message, text: str, **kwargs) -> None:
    try:
        await message.answer(text, **kwargs)
    except TelegramAPIError:
        logger.exception(
            "Failed to send DM message",
            extra={"user_id": getattr(message.from_user, "id", None)},
        )


async def _safe_callback_answer(
    callback: CallbackQuery,
    text: str = "",
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramAPIError:
        logger.exception(
            "Failed to answer callback",
            extra={"user_id": callback.from_user.id, "data": callback.data},
        )


async def _clear_callback_markup(callback: CallbackQuery) -> None:
    if callback.message is None:
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        logger.exception(
            "Failed to clear callback markup",
            extra={
                "chat_id": callback.message.chat.id,
                "message_id": callback.message.message_id,
                "data": callback.data,
            },
        )


async def _send_help_message(message: Message) -> None:
    await _safe_message_answer(message, HELP_TEXT)


async def _send_subscription_summary(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        active_groups = await list_active_groups_by_owner(owner_user_id=user.id, limit=5)
        pending_request = await get_pending_payment_request_by_user(user_id=user.id)
        state_record = await get_user_state(user_id=user.id)
    except Exception:
        logger.exception("Failed to build subscription summary", extra={"user_id": user.id})
        await _safe_message_answer(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    lines: list[str] = ["Aapki subscription summary:"]

    if active_groups:
        lines.append("Status: Active ✅")
        lines.append("Linked groups:")
        for group in active_groups:
            lines.append(f"- <code>{group.group_id}</code>")
    elif pending_request is not None:
        created_text = pending_request.created_at.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")
        lines.append("Status: Pending approval ⏳")
        lines.append(f"Payment ID: <code>{pending_request.payment_id}</code>")
        lines.append(f"Request Time: {created_text}")
    else:
        lines.append("Status: Abhi active ya pending subscription nahi mili.")
        lines.append("Start karne ke liye /start dabao aur Subscription Kharido choose karo.")

    if state_record is not None and state_record.state == UserState.AWAITING_GROUP_ID:
        lines.append("")
        lines.append("Next step pending hai: apna group chat ID DM me bhejo.")

    lines.append("")
    lines.append("Yaad rahe: 1 subscription = 1 group.")

    await _safe_message_answer(message, "\n".join(lines))


async def _cancel_current_flow(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        cleared = await clear_user_state(user_id=user.id)
    except Exception:
        logger.exception("Failed to clear user state", extra={"user_id": user.id})
        await _safe_message_answer(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if cleared:
        await _safe_message_answer(
            message,
            "Current flow cancel ho gaya ✅\nJab ready ho tab /start se phir continue kar sakte ho.",
        )
        return

    await _safe_message_answer(message, "Abhi koi active DM flow chal nahi raha hai.")


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_command(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    first_name = html.quote(user.first_name)
    await _safe_message_answer(
        message,
        (
            f"Namaste {first_name}! EliteXprotectorBot me aapka welcome hai.\n\n"
            "Ye bot aapke group ko spam aur unwanted activity se protect karne me help karta hai.\n"
            "Beginner ho to bhi tension mat lo, steps simple rahenge.\n\n"
            "Quick actions neeche diye gaye hain.\n"
            "Yaad rahe: 1 subscription = 1 group."
        ),
        reply_markup=start_menu_keyboard(),
    )


@router.message(Command("madad"), F.chat.type == ChatType.PRIVATE)
async def madad_command(message: Message) -> None:
    await _send_help_message(message)


@router.message(Command("meri_subscription"), F.chat.type == ChatType.PRIVATE)
async def meri_subscription_command(message: Message) -> None:
    await _send_subscription_summary(message)


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def cancel_flow_command(message: Message) -> None:
    await _cancel_current_flow(message)


@router.callback_query(F.data == SUBSCRIPTION_BUY_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def subscription_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback)
    if callback.message is None:
        return

    settings = get_settings()

    try:
        await callback.message.answer_photo(
            photo=settings.payment_qr_image_url,
            caption=(
                "Subscription ka price: ₹100\n"
                "QR scan karke payment complete karo.\n"
                "Payment complete hone ke baad Done ✅ dabao.\n"
                "Agar abhi continue nahi karna hai to Cancel ❌ dabao.\n"
                "Yaad rahe: 1 subscription = 1 group."
            ),
            reply_markup=payment_action_keyboard(),
        )
    except TelegramAPIError:
        logger.exception("Failed to send payment QR", extra={"user_id": callback.from_user.id})
        await _safe_message_answer(callback.message, GENERIC_HANDLER_ERROR_TEXT)


@router.callback_query(F.data == MY_SUBSCRIPTION_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def my_subscription_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback)
    if callback.message:
        await _send_subscription_summary(callback.message)


@router.callback_query(F.data == FLOW_CANCEL_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def flow_cancel_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback, "Current flow cancel kar diya.")
    if callback.message:
        await _cancel_current_flow(callback.message)


@router.callback_query(F.data == PAYMENT_DONE_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def payment_done_callback(callback: CallbackQuery) -> None:
    settings = get_settings()

    try:
        is_allowed = await is_done_click_allowed(callback.from_user.id)
    except Exception:
        logger.exception("Done-click guard check failed", extra={"user_id": callback.from_user.id})
        await _safe_callback_answer(callback, GENERIC_HANDLER_ERROR_TEXT, show_alert=True)
        return

    if not is_allowed:
        await _safe_callback_answer(callback, "Done ko baar-baar mat dabao, thoda wait karo.")
        return

    try:
        result = await create_pending_payment_request(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
    except Exception:
        logger.exception("Failed to create pending payment request", extra={"user_id": callback.from_user.id})
        await _safe_callback_answer(callback, GENERIC_HANDLER_ERROR_TEXT, show_alert=True)
        return

    if result.status == CreatePendingPaymentStatus.DUPLICATE:
        await _safe_callback_answer(callback, "Aapka pending request pehle se bana hua hai.")
        if callback.message:
            await _clear_callback_markup(callback)
            await _safe_message_answer(
                callback.message,
                "Aapka payment request pehle se pending hai, admin approve karega.",
            )
        return

    await _safe_callback_answer(callback, "Done receive ho gaya ✅")

    payment_id = result.payment_id
    if not payment_id:
        logger.error(
            "Created payment request without payment_id",
            extra={"user_id": callback.from_user.id},
        )
    else:
        username_text = (
            f"@{html.quote(callback.from_user.username)}" if callback.from_user.username else "Nahi diya"
        )
        full_name = html.quote(callback.from_user.full_name)

        try:
            await callback.bot.send_message(
                chat_id=settings.admin_review_chat_id,
                text=(
                    "Naya payment review request aaya hai.\n\n"
                    f"User ID: <code>{callback.from_user.id}</code>\n"
                    f"Username: {username_text}\n"
                    f"Pura Naam: {full_name}\n"
                    f"Payment ID: <code>{payment_id}</code>\n"
                    "Status: Pending"
                ),
                reply_markup=payment_review_keyboard(payment_id),
            )
        except TelegramAPIError:
            logger.exception(
                "Failed to send pending payment to admin review channel",
                extra={
                    "user_id": callback.from_user.id,
                    "payment_id": payment_id,
                    "admin_review_chat_id": settings.admin_review_chat_id,
                },
            )

    if callback.message:
        await _clear_callback_markup(callback)
        await _safe_message_answer(
            callback.message,
            "Payment request bhej diya gaya hai, admin approve karega.",
        )


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def bind_group_id_message(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        state_record = await get_user_state(user_id=user.id)
    except Exception:
        logger.exception("Failed to load user state", extra={"user_id": user.id})
        await _safe_message_answer(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if state_record is None or state_record.state != UserState.AWAITING_GROUP_ID:
        return

    group_id = parse_group_chat_id(message.text or "")
    if group_id is None:
        await _safe_message_answer(
            message,
            "Chat ID format sahi nahi hai. Numeric group chat ID bhejo, example: -1001234567890",
        )
        return

    try:
        state_consumed = await consume_user_state(
            user_id=user.id,
            expected_state=UserState.AWAITING_GROUP_ID,
        )
    except Exception:
        logger.exception("Failed to consume user state", extra={"user_id": user.id})
        await _safe_message_answer(message, GENERIC_HANDLER_ERROR_TEXT)
        return

    if not state_consumed:
        await _safe_message_answer(message, "Yeh group binding pehle hi process ho chuka hai.")
        return

    try:
        bind_result = await bind_protected_group(owner_user_id=user.id, group_id=group_id)
    except Exception:
        try:
            await set_user_state(user_id=user.id, state=UserState.AWAITING_GROUP_ID)
        except Exception:
            logger.exception("Failed to restore user state after bind error", extra={"user_id": user.id})

        logger.exception("Failed to bind protected group", extra={"user_id": user.id, "group_id": group_id})
        await _safe_message_answer(
            message,
            "Abhi group bind karte waqt issue aa gaya. Thodi der baad same chat ID phir bhejo.",
        )
        return

    if bind_result.status == BindProtectedGroupStatus.GROUP_ALREADY_BOUND:
        try:
            await set_user_state(user_id=user.id, state=UserState.AWAITING_GROUP_ID)
        except Exception:
            logger.exception("Failed to restore user state for already-bound group", extra={"user_id": user.id})

        await _safe_message_answer(
            message,
            "Yeh group pehle se linked hai. Koi aur valid group chat ID bhejo.",
        )
        return

    await _safe_message_answer(
        message,
        (
            "Group bind ho gaya ✅\n"
            "Yaad rahe: 1 approved subscription = 1 group.\n\n"
            "Ab setup ke liye ye steps follow karo:\n"
            "1) @EliteXprotectorBot ko group me add karo\n"
            "2) Bot ko admin banao\n"
            "3) Delete messages permission do\n"
            "4) BotFather me privacy mode off karna recommended hai\n\n"
            "Setup complete ho jaye to Check Setup dabao."
        ),
        reply_markup=check_setup_keyboard(),
    )


@router.callback_query(F.data == CHECK_SETUP_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def check_setup_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback, "Check Setup feature next step me add hoga.")
    if callback.message:
        await _safe_message_answer(
            callback.message,
            "Abhi placeholder mode hai. Pehle bot ko group me add karke admin setup complete karo.",
        )


@router.callback_query(F.data == PAYMENT_CANCEL_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def payment_cancel_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback, "Payment flow close kar diya gaya.")
    if callback.message:
        await _clear_callback_markup(callback)
        await _safe_message_answer(
            callback.message,
            "Theek hai, payment flow cancel ho gaya. Jab ready ho tab phir se Subscription Kharido dabao.",
        )


@router.callback_query(F.data == HELP_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def help_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback)
    if callback.message:
        await _send_help_message(callback.message)


@router.callback_query(F.data == HOW_IT_WORKS_CALLBACK, F.message.chat.type == ChatType.PRIVATE)
async def how_it_works_callback(callback: CallbackQuery) -> None:
    await _safe_callback_answer(callback)
    if callback.message:
        await _safe_message_answer(callback.message, HOW_IT_WORKS_TEXT)

