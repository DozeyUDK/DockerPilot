"""Short-lived, one-time privileged credential tokens for DockerPilot Extras."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime


class ElevationTokenManager:
    """Keep sudo credentials server-side and expose only one-time opaque tokens."""

    def __init__(
        self,
        *,
        default_ttl_seconds: int = 120,
        max_ttl_seconds: int = 600,
        max_per_session: int = 16,
        clock=None,
    ):
        self.default_ttl_seconds = max(1, int(default_ttl_seconds))
        self.max_ttl_seconds = max(30, int(max_ttl_seconds))
        self.max_per_session = max(1, int(max_per_session))
        self._clock = clock or time.time
        self._tokens: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _get_or_create_session_id(session) -> str:
        session_id = session.get("elevation_session_id")
        if not session_id:
            session_id = secrets.token_hex(16)
            session["elevation_session_id"] = session_id
            session.permanent = True
        return session_id

    def cleanup_expired(self) -> int:
        now_ts = float(self._clock())
        removed = 0
        with self._lock:
            expired = [
                key for key, entry in self._tokens.items()
                if float(entry.get("expires_at_ts", 0)) <= now_ts
            ]
            for key in expired:
                self._tokens.pop(key, None)
                removed += 1
        return removed

    def issue(self, session, *, sudo_password: str, scope: dict | None = None, ttl_seconds: int | None = None) -> dict:
        if not sudo_password:
            raise ValueError("sudo_password is required")

        self.cleanup_expired()
        session_id = self._get_or_create_session_id(session)
        requested_ttl = int(ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds)
        effective_ttl = max(30, min(requested_ttl, self.max_ttl_seconds))
        issued_at_ts = float(self._clock())
        expires_at_ts = issued_at_ts + effective_ttl
        token_plain = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token_plain.encode("utf-8")).hexdigest()
        scope_data = dict(scope) if isinstance(scope, dict) else {}

        entry = {
            "session_id": session_id,
            "sudo_password": sudo_password,
            "scope": scope_data,
            "issued_at_iso": datetime.fromtimestamp(issued_at_ts).isoformat(),
            "expires_at_iso": datetime.fromtimestamp(expires_at_ts).isoformat(),
            "expires_at_ts": expires_at_ts,
        }

        with self._lock:
            session_keys = [
                key for key, value in self._tokens.items()
                if value.get("session_id") == session_id
            ]
            if len(session_keys) >= self.max_per_session:
                session_keys.sort(key=lambda key: self._tokens.get(key, {}).get("expires_at_ts", 0))
                remove_count = len(session_keys) - self.max_per_session + 1
                for key in session_keys[:remove_count]:
                    self._tokens.pop(key, None)
            self._tokens[token_hash] = entry

        return {
            "token": token_plain,
            "expires_in": effective_ttl,
            "expires_at": entry["expires_at_iso"],
            "scope": scope_data,
        }

    def consume(
        self,
        session,
        token: str,
        *,
        expected_action: str | None = None,
        expected_scope: dict | None = None,
    ) -> tuple[bool, str, str | None]:
        if not token or not isinstance(token, str):
            return False, "Missing elevation token", None

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        current_session_id = session.get("elevation_session_id")
        now_ts = float(self._clock())

        with self._lock:
            entry = self._tokens.get(token_hash)
            if not entry:
                return False, "Invalid or expired elevation token", None
            if float(entry.get("expires_at_ts", 0)) <= now_ts:
                self._tokens.pop(token_hash, None)
                return False, "Elevation token expired", None
            if not current_session_id or entry.get("session_id") != current_session_id:
                return False, "Elevation token does not match active session", None

            scope = entry.get("scope") or {}
            if expected_action and scope.get("action") != expected_action:
                return False, "Elevation token scope mismatch (action)", None
            if isinstance(expected_scope, dict):
                for key, value in expected_scope.items():
                    if value is None:
                        continue
                    if scope.get(key) != value:
                        return False, f"Elevation token scope mismatch ({key})", None

            sudo_password = entry.get("sudo_password")
            self._tokens.pop(token_hash, None)

        return True, "ok", sudo_password

    def revoke_for_session(self, session) -> int:
        current_session_id = session.get("elevation_session_id")
        if not current_session_id:
            return 0
        removed = 0
        with self._lock:
            keys = [
                key for key, value in self._tokens.items()
                if value.get("session_id") == current_session_id
            ]
            for key in keys:
                self._tokens.pop(key, None)
                removed += 1
        return removed
