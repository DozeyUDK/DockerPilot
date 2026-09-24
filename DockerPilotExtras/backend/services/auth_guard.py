"""Authentication abuse controls and CSRF helpers for DockerPilot Extras."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Small in-memory failed-attempt limiter suitable for a single Extras process."""

    def __init__(self, *, max_failures: int = 5, window_seconds: int = 60, clock=None):
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = max(1, int(window_seconds))
        self._clock = clock or time.time
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def check(self, key: str) -> tuple[bool, int]:
        now = float(self._clock())
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.max_failures:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - events[0])))
            return False, retry_after

    def register_failure(self, key: str) -> tuple[bool, int]:
        now = float(self._clock())
        with self._lock:
            events = self._prune(key, now)
            events.append(now)
            if len(events) < self.max_failures:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - events[0])))
            return False, retry_after

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


def csrf_token_matches(*, header_token: str | None, session_token: str | None) -> bool:
    """Constant-time validation of a session-bound CSRF token."""
    header = (header_token or "").strip()
    stored = (session_token or "").strip()
    return bool(header) and bool(stored) and hmac.compare_digest(header, stored)
