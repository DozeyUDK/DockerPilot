"""Step-up TOTP with rate limit and single-use counter window."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional, Set, Tuple


class StepUpTotpGuard:
    """Rate-limit and replay-protect step-up TOTP codes (no secret persistence)."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 300,
        verify_fn: Callable[[str, str, int], bool],
        clock: Optional[Callable[[], float]] = None,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.verify_fn = verify_fn
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._attempts: Dict[str, Deque[float]] = defaultdict(deque)
        self._used: Dict[str, Set[str]] = defaultdict(set)
        self._used_order: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> None:
        attempts = self._attempts[key]
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        ordered = self._used_order[key]
        while ordered and now - ordered[0][0] > self.window_seconds:
            _, marker = ordered.popleft()
            self._used[key].discard(marker)

    def verify(self, *, actor_key: str, secret: str, code: str, window: int = 1) -> bool:
        now = self.clock()
        with self._lock:
            self._prune(actor_key, now)
            if len(self._attempts[actor_key]) >= self.max_attempts:
                return False
            self._attempts[actor_key].append(now)

            token = (code or "").strip()
            if not self.verify_fn(secret, token, window):
                return False

            counter = int(now // 30)
            markers = []
            for offset in range(-max(0, window), max(0, window) + 1):
                slot = counter + offset
                marker = hashlib.sha256(f"{actor_key}:{token}:{slot}".encode("utf-8")).hexdigest()
                markers.append(marker)
                if marker in self._used[actor_key]:
                    return False
            for marker in markers:
                self._used[actor_key].add(marker)
                self._used_order[actor_key].append((now, marker))
            return True
