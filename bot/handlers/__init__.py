from aiogram import Dispatcher

from .auto_delete import router as auto_delete_router
from .group_setup import router as group_setup_router
from .owner_commands import router as owner_commands_router
from .payment_review import router as payment_review_router
from .start import router as start_router


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(start_router)
    dp.include_router(payment_review_router)
    dp.include_router(group_setup_router)
    dp.include_router(auto_delete_router)
    dp.include_router(owner_commands_router)
