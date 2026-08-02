"""Approval state machine for Secure Deploy (no apply)."""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from dockerpilot.secure_deploy.schemas import SchemaValidationError, load_schema
from dockerpilot.secure_deploy.schemas import _validate

from .errors import ForbiddenError, SecureDeployError, ValidationFailedError
from .store import FileSecureDeployStore, new_id

APPROVAL_VERSION = 1
APPROVAL_TTL_MINUTES = 10

ALLOWED_TRANSITIONS = {
    ("pending", "approved"),
    ("pending", "expired"),
    ("pending", "revoked"),
    ("approved", "consumed"),
    ("approved", "expired"),
    ("approved", "revoked"),
}


def _utcnow(clock: Optional[Callable[[], datetime]] = None) -> datetime:
    if clock:
        return clock()
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def session_id_hash(raw_session_material: str) -> str:
    return hashlib.sha256(raw_session_material.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(
        self,
        store: FileSecureDeployStore,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        ttl_minutes: int = APPROVAL_TTL_MINUTES,
    ):
        self.store = store
        self.clock = clock
        self.ttl_minutes = min(ttl_minutes, APPROVAL_TTL_MINUTES)
        self._locks: Dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._meta:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def approve_plan(
        self,
        stored_plan: Dict[str, Any],
        *,
        actor: str,
        session_hash: str,
    ) -> Dict[str, Any]:
        plan = stored_plan.get("plan") or stored_plan
        plan_id = plan["plan_id"]
        plan_sha = plan.get("plan_sha256")
        if not plan_sha:
            raise ValidationFailedError("plan missing plan_sha256")
        if stored_plan.get("status") == "invalid":
            raise ValidationFailedError("cannot approve invalid plan")

        now = _utcnow(self.clock)
        plan_expires = _parse(plan["expires_at"])
        if now >= plan_expires:
            raise ValidationFailedError("plan expired", code="plan_expired")

        with self._lock_for(plan_id):
            # Reject second active approval for same plan hash.
            existing = self.store.find_active_approval(plan_id, plan_sha, now_ts=now.timestamp())
            if existing and existing.get("status") == "approved":
                raise SecureDeployError("double_approval", "plan already has an active approval", 409)

            approval_id = new_id("appr")
            expires = now + timedelta(minutes=self.ttl_minutes)
            if expires > plan_expires:
                expires = plan_expires
            record = {
                "approval_version": APPROVAL_VERSION,
                "approval_id": approval_id,
                "plan_id": plan_id,
                "plan_sha256": plan_sha,
                "actor": actor,
                "nonce": secrets.token_urlsafe(16),
                "issued_at": _iso(now),
                "approved_at": _iso(now),
                "expires_at": _iso(expires),
                "session_id_hash": session_hash,
                "status": "approved",
            }
            self._validate(record)
            self.store.save_approval(approval_id, record)
            # Mirror status onto plan approval block without changing plan_sha256 inputs.
            plan_approval = dict(plan.get("approval") or {})
            plan_approval["status"] = "approved"
            plan_approval["approved_by"] = actor
            plan_approval["approved_at"] = record["approved_at"]
            plan_approval["expires_at"] = record["expires_at"]
            # Keep original nonce on plan; approval has its own.
            updated = dict(stored_plan)
            updated_plan = dict(plan)
            updated_plan["approval"] = plan_approval
            updated["plan"] = updated_plan
            self.store.update_plan_metadata(plan_id, updated)
            return record

    def revoke(self, approval_id: str, *, actor: str) -> Dict[str, Any]:
        with self._lock_for(approval_id):
            record = self.store.get_approval(approval_id)
            if record.get("actor") != actor:
                raise ForbiddenError(code="actor_mismatch", message="actor mismatch")
            return self._transition(record, "revoked")

    def get(self, approval_id: str) -> Dict[str, Any]:
        record = self.store.get_approval(approval_id)
        return self._expire_if_needed(record)

    def consume(self, approval_id: str, *, expected_plan_sha256: str) -> Dict[str, Any]:
        """Atomic consume for future apply (#11D.2). Exposed for tests."""
        with self._lock_for(approval_id):
            record = self._expire_if_needed(self.store.get_approval(approval_id))
            if record.get("plan_sha256") != expected_plan_sha256:
                raise ValidationFailedError("plan hash mismatch", code="plan_hash_mismatch")
            return self._transition(record, "consumed")

    def _expire_if_needed(self, record: Dict[str, Any]) -> Dict[str, Any]:
        now = _utcnow(self.clock)
        if record.get("status") in {"approved", "pending"} and now >= _parse(record["expires_at"]):
            return self._transition(record, "expired")
        return record

    def _transition(self, record: Dict[str, Any], new_status: str) -> Dict[str, Any]:
        old = record["status"]
        if (old, new_status) not in ALLOWED_TRANSITIONS:
            raise SecureDeployError(
                "approval_transition",
                f"illegal transition {old} -> {new_status}",
                409,
            )
        updated = dict(record)
        updated["status"] = new_status
        self._validate(updated)
        self.store.save_approval_atomic_replace(record["approval_id"], updated, expected_status=old)
        return updated

    def _validate(self, record: Dict[str, Any]) -> None:
        schema = load_schema("secure-deploy-approval-v1.schema.json")
        clean = {k: v for k, v in record.items() if not k.startswith("store_")}
        try:
            _validate(clean, schema, "$")
        except SchemaValidationError as exc:
            raise ValidationFailedError(str(exc)) from exc
