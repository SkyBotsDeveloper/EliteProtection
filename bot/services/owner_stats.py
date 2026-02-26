from dataclasses import dataclass

from bot.db import get_database
from bot.services.payment_requests import count_pending_payment_requests
from bot.services.protected_groups import count_active_protected_groups


@dataclass(slots=True)
class OwnerStats:
    total_users: int
    active_groups: int
    pending_payments: int


async def get_owner_stats() -> OwnerStats:
    database = get_database()

    payments_users = await database["payments"].distinct("user_id")
    group_owners = await database["protected_groups"].distinct("owner_user_id")
    state_users = await database["user_states"].distinct("user_id")

    user_ids: set[int] = set()
    for raw_user_id in [*payments_users, *group_owners, *state_users]:
        if isinstance(raw_user_id, int):
            user_ids.add(raw_user_id)

    active_groups = await count_active_protected_groups()
    pending_payments = await count_pending_payment_requests()

    return OwnerStats(
        total_users=len(user_ids),
        active_groups=active_groups,
        pending_payments=pending_payments,
    )
