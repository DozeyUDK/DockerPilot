"""Broker-side approval binding checks (no trust of client status alone)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dockerpilot.secure_deploy.schemas import SchemaValidationError, load_schema
from dockerpilot.secure_deploy.schemas import _validate

from .errors import VerificationError

ALLOWED_TRANSITIONS = {
    ("pending", "approved"),
    ("pending", "expired"),
    ("pending", "revoked"),
    ("approved", "consumed"),
    ("approved", "expired"),
    ("approved", "revoked"),
}


def parse_ts(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def validate_approval_record(approval: Dict[str, Any]) -> Dict[str, Any]:
    schema = load_schema("secure-deploy-approval-v1.schema.json")
    try:
        _validate(approval, schema, "$")
    except SchemaValidationError as exc:
        raise VerificationError("approval_schema", str(exc)) from exc
    return approval


def assert_transition_allowed(old: str, new: str) -> None:
    if (old, new) not in ALLOWED_TRANSITIONS:
        raise VerificationError("approval_transition", f"illegal transition {old} -> {new}")


def assert_approval_binds_plan(
    approval: Dict[str, Any],
    *,
    plan_id: str,
    plan_sha256: str,
    now: Optional[datetime] = None,
    require_status: str = "approved",
) -> None:
    validate_approval_record(approval)
    if approval.get("plan_id") != plan_id:
        raise VerificationError("approval_plan_mismatch", "approval plan_id mismatch")
    if approval.get("plan_sha256") != plan_sha256:
        raise VerificationError("approval_hash_mismatch", "approval plan_sha256 mismatch")
    if approval.get("status") != require_status:
        raise VerificationError("approval_status", f"approval status must be {require_status}")
    clock = now or datetime.now(timezone.utc)
    expires = parse_ts(str(approval["expires_at"]))
    if clock >= expires:
        raise VerificationError("approval_expired", "approval expired")
