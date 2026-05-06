"""Small logging helpers for noisy background services."""

from __future__ import annotations

from time import monotonic
from threading import Lock


class LogRateLimiter:
    """Allow a log key through at most once per interval."""

    def __init__(self) -> None:
        self._last_seen: dict[str, float] = {}
        self._lock = Lock()

    def should_log(self, key: str, interval_seconds: float) -> bool:
        now = monotonic()
        with self._lock:
            last_seen = self._last_seen.get(key)
            if last_seen is not None and now - last_seen < interval_seconds:
                return False
            self._last_seen[key] = now
            return True
