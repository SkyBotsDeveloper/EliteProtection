import asyncio
import time


class DoneClickGuard:
    def __init__(self, cooldown_seconds: int = 12, stale_ttl_seconds: int = 3600) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._stale_ttl_seconds = stale_ttl_seconds
        self._last_done_click_at: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def allow(self, user_id: int) -> bool:
        now = time.monotonic()

        async with self._lock:
            last_click = self._last_done_click_at.get(user_id)
            if last_click is not None and now - last_click < self._cooldown_seconds:
                return False

            self._last_done_click_at[user_id] = now
            self._cleanup(now)
            return True

    def _cleanup(self, now: float) -> None:
        stale_before = now - self._stale_ttl_seconds
        stale_user_ids = [
            user_id
            for user_id, clicked_at in self._last_done_click_at.items()
            if clicked_at < stale_before
        ]

        for user_id in stale_user_ids:
            self._last_done_click_at.pop(user_id, None)


_done_click_guard = DoneClickGuard()


async def is_done_click_allowed(user_id: int) -> bool:
    return await _done_click_guard.allow(user_id)
