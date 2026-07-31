"""Plan hashing and log redaction for Secure Deploy contracts."""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Mapping, Set


from .canonical import sha256_canonical

# Fields excluded from plan_sha256 input (non-deterministic or self-referential).
PLAN_HASH_EXCLUDED_FIELDS: Set[str] = {
    "plan_sha256",
    "created_at",
    "expires_at",
    "approval",  # approval binding references plan_sha256; hashed separately by consumers
}

# Nested keys under approval that still participate when hashing an approval record alone.
APPROVAL_BINDING_FIELDS = (
    "plan_sha256",
    "approved_by",
    "expires_at",
    "nonce",
    "status",
)

_REDACT_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "private_key",
    "credential",
    "sudo",
)


def _strip_excluded(obj: Any, excluded_top: Set[str], depth: int = 0) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, value in obj.items():
            if depth == 0 and key in excluded_top:
                continue
            out[key] = _strip_excluded(value, excluded_top, depth + 1)
        return out
    if isinstance(obj, list):
        return [_strip_excluded(item, excluded_top, depth + 1) for item in obj]
    return obj


def plan_hash_payload(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the subset of a plan used for ``plan_sha256``."""
    return _strip_excluded(dict(plan), PLAN_HASH_EXCLUDED_FIELDS)


def compute_plan_sha256(plan: Mapping[str, Any]) -> str:
    """SHA-256 over canonical JSON of the plan without self-hash / timestamps / approval."""
    return sha256_canonical(plan_hash_payload(plan))


def compute_approval_binding_sha256(approval: Mapping[str, Any], actor: str) -> str:
    """Hash for one-time approval binding (plan hash + actor + nonce + expiry)."""
    payload = {
        "actor": actor,
        "plan_sha256": approval.get("plan_sha256"),
        "nonce": approval.get("nonce"),
        "expires_at": approval.get("expires_at"),
        "status": approval.get("status"),
    }
    return sha256_canonical(payload)


def redact_for_log(obj: Any) -> Any:
    """Deep-copy structure replacing secret-like values with ``[REDACTED]``."""

    def _should_redact(key: str) -> bool:
        lowered = key.lower()
        return any(fragment in lowered for fragment in _REDACT_KEY_FRAGMENTS)

    def _walk(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: _walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(item, key) for item in value]
        if key is not None and _should_redact(key) and value not in (None, "", [], {}):
            # Keep structural refs that are names only under allowlisted keys.
            if key in {"name", "provider", "manifest_service", "injection", "environment_key"}:
                return value
            if isinstance(value, str) and value.startswith("sha256:"):
                return value
            if isinstance(value, str) and len(value) == 64 and all(
                ch in "0123456789abcdef" for ch in value
            ):
                return value
            return "[REDACTED]"
        return value

    return _walk(copy.deepcopy(obj))


def detect_disallowed_fields(
    doc: Mapping[str, Any],
    *,
    path: str = "$",
    forbidden: Iterable[str] = ("privileged", "network_mode", "devices"),
) -> list[str]:
    """Return dotted paths of forbidden keys if present."""
    found: list[str] = []
    forbidden_set = set(forbidden)

    def _walk(node: Any, current: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{current}.{key}"
                if key in forbidden_set:
                    found.append(child)
                _walk(value, child)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _walk(item, f"{current}[{index}]")

    _walk(doc, path)
    return found
