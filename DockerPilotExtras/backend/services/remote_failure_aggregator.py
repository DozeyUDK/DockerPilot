"""In-process aggregation for repeated remote-operation failures."""

from __future__ import annotations

import threading
import time
from typing import Callable

from ..security import redact_sensitive_text, safe_error_message


class RemoteFailureAggregator:
    """Emit the first remote failure, periodic summaries, and one recovery event."""

    def __init__(
        self,
        logger,
        *,
        cooldown_seconds: float = 60.0,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.logger = logger
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.max_entries = max(1, int(max_entries))
        self.clock = clock
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _server_id(server_config) -> str:
        if isinstance(server_config, dict):
            value = server_config.get("id") or server_config.get("hostname") or "remote"
        else:
            value = "remote"
        return redact_sensitive_text(str(value))[:128]

    @staticmethod
    def _error_fields(error) -> tuple[str, str]:
        if isinstance(error, BaseException):
            return type(error).__name__, safe_error_message(error)
        return "RemoteError", redact_sensitive_text(error)[:500]

    def record_failure(self, server_config, operation: str, error) -> None:
        server_id = self._server_id(server_config)
        operation = redact_sensitive_text(str(operation))[:80]
        error_type, error_text = self._error_fields(error)
        now = self.clock()
        key = (server_id, operation)
        report = None

        with self._lock:
            state = self._active.get(key)
            if state is None:
                state = {
                    "first_seen": now,
                    "last_seen": now,
                    "last_reported": now,
                    "total_occurrences": 1,
                    "pending_occurrences": 0,
                    "error_type": error_type,
                    "error": error_text,
                }
                self._active[key] = state
                report = {
                    "message": "Remote operation failed",
                    "occurrences": 1,
                    "downtime_seconds": 0.0,
                }
            else:
                state["last_seen"] = now
                state["total_occurrences"] += 1
                state["pending_occurrences"] += 1
                state["error_type"] = error_type
                state["error"] = error_text
                if now - state["last_reported"] >= self.cooldown_seconds:
                    report = {
                        "message": "Remote operation remains unavailable; failures summarized",
                        "occurrences": state["pending_occurrences"],
                        "downtime_seconds": round(now - state["first_seen"], 2),
                    }
                    state["pending_occurrences"] = 0
                    state["last_reported"] = now

            if len(self._active) > self.max_entries:
                oldest_key = min(
                    self._active,
                    key=lambda item: self._active[item]["last_seen"],
                )
                if oldest_key != key:
                    self._active.pop(oldest_key, None)

        if report:
            self.logger.warning(
                report["message"],
                extra={
                    "event": "remote_operation_failed",
                    "server_id": server_id,
                    "operation": operation,
                    "error_type": error_type,
                    "error": error_text,
                    "occurrences": report["occurrences"],
                    "downtime_seconds": report["downtime_seconds"],
                },
            )

    def record_success(self, server_config, operation: str) -> None:
        server_id = self._server_id(server_config)
        operation = redact_sensitive_text(str(operation))[:80]
        key = (server_id, operation)
        now = self.clock()

        with self._lock:
            state = self._active.pop(key, None)

        if state is None:
            return

        self.logger.info(
            "Remote operation recovered",
            extra={
                "event": "remote_operation_restored",
                "server_id": server_id,
                "operation": operation,
                "error_type": state["error_type"],
                "occurrences": state["total_occurrences"],
                "downtime_seconds": round(now - state["first_seen"], 2),
            },
        )
