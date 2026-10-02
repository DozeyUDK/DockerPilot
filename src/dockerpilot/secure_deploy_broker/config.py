"""Closed broker config model (fail-closed on unknown fields)."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from .errors import BrokerError
from .protocol import MAX_FRAME_BYTES, SUPPORTED_OPERATIONS

CANARY_OPERATIONS = frozenset({"admit_canary_execution", "revoke_canary_admission", "deploy_canary", "remove_canary"})
PLACEHOLDER_DIGEST = "sha256:" + ("a" * 64)
_SHA_IMAGE_RE = re.compile(r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")
_CONFIG_MAX_BYTES = 1024 * 1024

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


def _assert_trusted_parent_chain(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: Optional[int] = None,
    stop_at: Optional[Path] = None,
) -> None:
    """Require every parent in scope to be trusted and non-writable by peers.

    Production callers walk from the config directory to ``/``. ``stop_at`` is
    only useful for isolated tests that build a synthetic trusted subtree.
    """

    if not path.is_absolute():
        raise BrokerError("config_path", "trusted runtime config path must be absolute")
    stop = Path("/") if stop_at is None else Path(stop_at)
    if not stop.is_absolute():
        raise BrokerError("config_parent_scope", "trusted parent scope must be absolute")

    current = path.parent
    while True:
        try:
            st = os.lstat(current)
        except OSError as exc:
            raise BrokerError("config_parent_missing", f"trusted config parent unavailable: {current}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise BrokerError("config_parent_symlink", f"symlink parent rejected: {current}")
        if not stat.S_ISDIR(st.st_mode):
            raise BrokerError("config_parent_type", f"directory required in config path: {current}")
        if st.st_uid != expected_uid:
            raise BrokerError("config_parent_owner", f"unexpected owner uid for config parent: {current}")
        if expected_gid is not None and st.st_gid != expected_gid:
            raise BrokerError("config_parent_group", f"unexpected owner gid for config parent: {current}")
        if stat.S_IMODE(st.st_mode) & 0o022:
            raise BrokerError("config_parent_writable", f"group/other-writable config parent rejected: {current}")
        if current == stop:
            break
        parent = current.parent
        if parent == current:
            raise BrokerError("config_parent_scope", "trusted parent scope is not an ancestor of config path")
        current = parent


def _read_config_fd(fd: int, *, size_hint: int) -> bytes:
    if size_hint < 0 or size_hint > _CONFIG_MAX_BYTES:
        raise BrokerError("config_too_large", "broker config exceeds size limit")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, _CONFIG_MAX_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _CONFIG_MAX_BYTES:
            raise BrokerError("config_too_large", "broker config exceeds size limit")
    return b"".join(chunks)


def load_broker_config(
    path: Path,
    *,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
    require_trusted_parents: bool = False,
) -> BrokerConfig:
    """Open and parse broker config with fail-closed filesystem trust checks.

    When ``require_trusted_parents`` is enabled, ``expected_uid`` is mandatory
    and every containing directory through ``/`` must be owned by the expected
    UID, optionally GID, and not writable by group/other. The config itself is
    opened once with no-follow semantics, validated via ``fstat`` and read from
    that same file descriptor so pathname replacement cannot swap the bytes.
    """

    path = Path(path)
    if require_trusted_parents:
        if expected_uid is None:
            raise BrokerError("config_owner", "expected config owner required for trusted-parent mode")
        _assert_trusted_parent_chain(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise BrokerError("config_missing", f"broker config missing: {path}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BrokerError("config_symlink", "config path must not be a symlink") from exc
        raise BrokerError("config_open", f"unable to open broker config: {path}") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise BrokerError("config_file_type", "broker config must be a regular file")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o022:
            raise BrokerError("config_writable", "broker config must not be group/other writable")
        if expected_uid is not None and st.st_uid != expected_uid:
            raise BrokerError("config_owner", "broker config has unexpected owner uid")
        if expected_gid is not None and st.st_gid != expected_gid:
            raise BrokerError("config_group", "broker config has unexpected owner gid")
        raw = _read_config_fd(fd, size_hint=st.st_size)
    finally:
        os.close(fd)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrokerError("config_encoding", "broker config must be UTF-8 JSON") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BrokerError("config_json", "broker config must contain valid JSON") from exc
    if not isinstance(data, dict):
        raise BrokerError("config_type", "config must be a JSON object")
    return BrokerConfig(data)
