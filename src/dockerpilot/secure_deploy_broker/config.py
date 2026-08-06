"""Closed broker config model (fail-closed on unknown fields)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from .errors import BrokerError
from .protocol import MAX_FRAME_BYTES, SUPPORTED_OPERATIONS

CANARY_OPERATIONS = frozenset({"admit_canary_execution", "revoke_canary_admission", "deploy_canary", "remove_canary"})
PLACEHOLDER_DIGEST = "sha256:" + ("a" * 64)
_SHA_IMAGE_RE = re.compile(r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")

ALLOWED_CONFIG_KEYS = frozenset(
    {
        "protocol_version",
        "socket_activation",
        "socket_path",
        "max_frame_bytes",
        "request_timeout_seconds",
        "expected_peer_uid",
        "dozeyguard_path",
        "policy_path",
        "expected_binary_sha256",
        "expected_policy_sha256",
        "schemas_root",
        "state_root",
        "allowed_operations",
        "canary_workdir",
        "canary_image",
        "canary_health_timeout_seconds",
        "canary_staged_bundle_ttl_seconds",
        "canary_live_mode",
    }
)


class BrokerConfig:
    def __init__(self, raw: Dict[str, Any]):
        if "expected_peer_user" in raw:
            raise BrokerError(
                "config_peer_user",
                "expected_peer_user is install-template only; runtime config must use expected_peer_uid",
            )
        unknown = set(raw) - ALLOWED_CONFIG_KEYS
        if unknown:
            raise BrokerError("config_unknown_field", f"unknown config fields: {sorted(unknown)}")
        if raw.get("protocol_version") != 1:
            raise BrokerError("config_protocol", "protocol_version must be 1")
        ops = raw.get("allowed_operations")
        if not isinstance(ops, list) or not ops:
            raise BrokerError("config_operations", "allowed_operations required")
        op_set = frozenset(ops)
        if not op_set.issubset(SUPPORTED_OPERATIONS):
            raise BrokerError("config_operations", "allowed_operations contains unsupported op")
        forbidden = {"apply", "deploy", "firewall_apply", "materialize_secrets", "rollback", "exec"}
        if op_set & forbidden:
            raise BrokerError("config_operations", "forbidden operations in config")
        self.protocol_version = 1
        self.socket_activation = bool(raw.get("socket_activation", True))
        self.socket_path = raw.get("socket_path")
        self.max_frame_bytes = int(raw.get("max_frame_bytes", MAX_FRAME_BYTES))
        if self.max_frame_bytes <= 0 or self.max_frame_bytes > MAX_FRAME_BYTES:
            raise BrokerError("config_frame", "max_frame_bytes out of range")
        self.request_timeout_seconds = float(raw.get("request_timeout_seconds", 15))
        if "expected_peer_uid" not in raw or raw["expected_peer_uid"] is None:
            raise BrokerError(
                "config_peer_uid",
                "expected_peer_uid is required (numeric); null is not allowed",
            )
        try:
            self.expected_peer_uid = int(raw["expected_peer_uid"])
        except (TypeError, ValueError) as exc:
            raise BrokerError("config_peer_uid", "expected_peer_uid must be an integer") from exc
        if self.expected_peer_uid < 0:
            raise BrokerError("config_peer_uid", "expected_peer_uid must be >= 0")
        for key in ("dozeyguard_path", "policy_path", "schemas_root", "state_root"):
            if key not in raw or not isinstance(raw[key], str) or not raw[key]:
                raise BrokerError("config_path", f"{key} required")
            if ".." in Path(raw[key]).parts:
                raise BrokerError("config_path_traversal", f"{key} path traversal rejected")
        self.dozeyguard_path = str(Path(raw["dozeyguard_path"]))
        self.policy_path = str(Path(raw["policy_path"]))
        self.schemas_root = str(Path(raw["schemas_root"]))
        self.state_root = str(Path(raw["state_root"]))
        self.expected_binary_sha256 = str(raw.get("expected_binary_sha256") or "")
        self.expected_policy_sha256 = str(raw.get("expected_policy_sha256") or "")
        if len(self.expected_binary_sha256) != 64 or len(self.expected_policy_sha256) != 64:
            raise BrokerError("config_hash", "expected binary/policy sha256 required (64 hex)")
        self.allowed_operations: FrozenSet[str] = op_set
        self.canary_workdir = str(raw.get("canary_workdir") or "")
        self.canary_image = str(raw.get("canary_image") or "")
        try:
            self.canary_health_timeout_seconds = int(raw.get("canary_health_timeout_seconds", 60))
        except (TypeError, ValueError) as exc:
            raise BrokerError("config_canary_timeout", "canary health timeout must be an integer") from exc
        if self.canary_health_timeout_seconds <= 0 or self.canary_health_timeout_seconds > 600:
            raise BrokerError("config_canary_timeout", "canary health timeout out of range")
        self.canary_staged_bundle_ttl_seconds = self._parse_canary_ttl(raw)
        self.canary_live_mode = bool(raw.get("canary_live_mode", True))
        if op_set & CANARY_OPERATIONS:
            if not self.canary_image:
                raise BrokerError("config_canary_image", "canary_image required when canary operations are enabled")
            if not _SHA_IMAGE_RE.fullmatch(self.canary_image):
                raise BrokerError("config_canary_image", "canary_image must be digest-only")
            if self.canary_live_mode and self.canary_image.endswith("@" + PLACEHOLDER_DIGEST):
                raise BrokerError("config_canary_image", "live canary image digest must be non-placeholder")
            if "canary_staged_bundle_ttl_seconds" not in raw:
                raise BrokerError("config_canary_ttl", "canary_staged_bundle_ttl_seconds required when canary operations are enabled")

    def _parse_canary_ttl(self, raw: Dict[str, Any]) -> int:
        if "canary_staged_bundle_ttl_seconds" not in raw:
            return 300
        try:
            ttl = int(raw["canary_staged_bundle_ttl_seconds"])
        except (TypeError, ValueError) as exc:
            raise BrokerError("config_canary_ttl", "canary staged bundle TTL must be an integer") from exc
        if ttl <= 0 or ttl > 600:
            raise BrokerError("config_canary_ttl", "canary staged bundle TTL out of range")
        return ttl


def load_broker_config(path: Path) -> BrokerConfig:
    if path.is_symlink():
        raise BrokerError("config_symlink", "config path must not be a symlink")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BrokerError("config_type", "config must be a JSON object")
    return BrokerConfig(data)
