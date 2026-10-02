"""Broker-owned approval provenance authority.

This module deliberately does not trust approval objects supplied by DockerPilot
Extras. It owns one-shot approval challenges and resulting approval records in
broker-controlled state. A later transport layer can feed the approver UID from
SO_PEERCRED on a socket that is not accessible to the Extras service account.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Optional

try:  # Linux broker runtime; keep package importable on Windows CI.
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows import smoke only.
    fcntl = None

from .errors import VerificationError

CHALLENGE_TTL_SECONDS = 120
APPROVAL_TTL_SECONDS = 600
MAX_RECORD_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")


def _utcnow(clock: Optional[Callable[[], datetime]] = None) -> datetime:
    return clock() if clock else datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), mode)


def _validate_id(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise VerificationError("approval_authority_id", f"invalid {field}")
    return value


def _validate_plan_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise VerificationError("approval_authority_plan_hash", "invalid plan_sha256")
    return value


class BrokerApprovalAuthority:
    """Durable broker-owned challenge and approval ledger.

    The caller that invokes :meth:`approve_challenge` must supply an approver UID
    obtained from a trusted transport (planned: SO_PEERCRED on the dedicated
    approver socket). The untrusted control-plane must never be allowed to choose
    that UID.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        allowed_approver_uids: FrozenSet[int],
        clock: Optional[Callable[[], datetime]] = None,
        challenge_ttl_seconds: int = CHALLENGE_TTL_SECONDS,
        approval_ttl_seconds: int = APPROVAL_TTL_SECONDS,
        expected_owner_uid: Optional[int] = None,
    ):
        if not allowed_approver_uids or any(int(uid) < 0 for uid in allowed_approver_uids):
            raise VerificationError("approval_authority_uids", "non-empty approver UID allowlist required")
        if challenge_ttl_seconds <= 0 or challenge_ttl_seconds > 600:
            raise VerificationError("approval_authority_ttl", "challenge TTL out of range")
        if approval_ttl_seconds <= 0 or approval_ttl_seconds > 3600:
            raise VerificationError("approval_authority_ttl", "approval TTL out of range")
        self.state_root = Path(state_root)
        self.allowed_approver_uids = frozenset(int(uid) for uid in allowed_approver_uids)
        self.clock = clock
        self.challenge_ttl_seconds = int(challenge_ttl_seconds)
        self.approval_ttl_seconds = int(approval_ttl_seconds)
        self.expected_owner_uid = expected_owner_uid
        self._thread_lock = threading.RLock()

    @property
    def challenges_dir(self) -> Path:
        return self.state_root / "challenges"

    @property
    def approvals_dir(self) -> Path:
        return self.state_root / "approvals"

    @property
    def lock_path(self) -> Path:
        return self.state_root / ".approval-authority.lock"

    def create_challenge(self, *, plan_id: str, plan_sha256: str) -> Dict[str, Any]:
        plan_id = _validate_id(plan_id, field="plan_id")
        plan_sha256 = _validate_plan_sha(plan_sha256)

        def op() -> Dict[str, Any]:
            now = _utcnow(self.clock)
            record = {
                "challenge_version": 1,
                "challenge_id": "chal_" + secrets.token_hex(12),
                "approval_id": "appr_" + secrets.token_hex(12),
                "plan_id": plan_id,
                "plan_sha256": plan_sha256,
                "nonce": secrets.token_urlsafe(18),
                "issued_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=self.challenge_ttl_seconds)),
                "status": "pending",
            }
            self._write_json_create(self._challenge_path(record["challenge_id"]), record)
            return dict(record)

        return self._with_lock(op)

    def approve_challenge(
        self,
        challenge_id: str,
        *,
        approver_uid: int,
        expected_plan_sha256: str,
    ) -> Dict[str, Any]:
        challenge_id = _validate_id(challenge_id, field="challenge_id")
        expected_plan_sha256 = _validate_plan_sha(expected_plan_sha256)
        uid = int(approver_uid)
        if uid not in self.allowed_approver_uids:
            raise VerificationError("approval_approver_uid", "approver UID is not authorized")

        def op() -> Dict[str, Any]:
            challenge_path = self._challenge_path(challenge_id)
            challenge = self._read_json(challenge_path)
            if challenge.get("status") != "pending":
                raise VerificationError("approval_challenge_status", "approval challenge is not pending")
            if challenge.get("plan_sha256") != expected_plan_sha256:
                raise VerificationError("approval_challenge_hash", "approval challenge plan hash mismatch")
            now = _utcnow(self.clock)
            if now >= _parse_ts(str(challenge.get("expires_at") or "")):
                expired = dict(challenge)
                expired["status"] = "expired"
                self._write_json_replace(challenge_path, expired)
                raise VerificationError("approval_challenge_expired", "approval challenge expired")

            approval_id = _validate_id(str(challenge.get("approval_id") or ""), field="approval_id")
            approval = {
                "approval_version": 2,
                "approval_id": approval_id,
                "challenge_id": challenge_id,
                "plan_id": _validate_id(str(challenge.get("plan_id") or ""), field="plan_id"),
                "plan_sha256": _validate_plan_sha(str(challenge.get("plan_sha256") or "")),
                "nonce": secrets.token_urlsafe(18),
                "issued_at": _iso(now),
                "approved_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=self.approval_ttl_seconds)),
                "status": "approved",
                "provenance": {
                    "kind": "unix_peer_uid",
                    "uid": uid,
                },
            }
            # Create-only approval record prevents challenge replay from silently
            # replacing authority if state becomes inconsistent.
            self._write_json_create(self._approval_path(approval_id), approval)
            approved_challenge = dict(challenge)
            approved_challenge["status"] = "approved"
            approved_challenge["approved_at"] = approval["approved_at"]
            approved_challenge["approver_uid"] = uid
            self._write_json_replace(challenge_path, approved_challenge)
            return dict(approval)

        return self._with_lock(op)

    def require_approval(
        self,
        approval_id: str,
        *,
        plan_id: str,
        plan_sha256: str,
    ) -> Dict[str, Any]:
        approval_id = _validate_id(approval_id, field="approval_id")
        plan_id = _validate_id(plan_id, field="plan_id")
        plan_sha256 = _validate_plan_sha(plan_sha256)

        def op() -> Dict[str, Any]:
            approval = self._read_json(self._approval_path(approval_id))
            if approval.get("status") != "approved":
                raise VerificationError("approval_authority_status", "broker approval is not active")
            if approval.get("plan_id") != plan_id:
                raise VerificationError("approval_authority_plan", "broker approval plan_id mismatch")
            if approval.get("plan_sha256") != plan_sha256:
                raise VerificationError("approval_authority_hash", "broker approval plan hash mismatch")
            provenance = approval.get("provenance")
            if not isinstance(provenance, dict) or provenance.get("kind") != "unix_peer_uid":
                raise VerificationError("approval_authority_provenance", "broker approval provenance missing")
            try:
                uid = int(provenance.get("uid"))
            except (TypeError, ValueError) as exc:
                raise VerificationError("approval_authority_provenance", "broker approval UID invalid") from exc
            if uid not in self.allowed_approver_uids:
                raise VerificationError("approval_authority_provenance", "broker approval UID no longer authorized")
            if _utcnow(self.clock) >= _parse_ts(str(approval.get("expires_at") or "")):
                raise VerificationError("approval_authority_expired", "broker approval expired")
            return dict(approval)

        return self._with_lock(op)

    def _challenge_path(self, challenge_id: str) -> Path:
        return self.challenges_dir / f"{challenge_id}.json"

    def _approval_path(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.json"

    def _prepare_state(self) -> None:
        if fcntl is None:
            raise VerificationError("approval_authority_platform", "approval authority requires POSIX file locking")
        if self.state_root.exists() and self.state_root.is_symlink():
            raise VerificationError("approval_authority_symlink", "approval authority state root symlink rejected")
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        self._assert_controlled_dir(self.state_root)
        for directory in (self.challenges_dir, self.approvals_dir):
            if directory.exists() and directory.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority state directory symlink rejected")
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
            self._assert_controlled_dir(directory)

    def _assert_controlled_dir(self, path: Path) -> None:
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise VerificationError("approval_authority_state", "approval authority state path must be directory")
        if self.expected_owner_uid is not None and st.st_uid != self.expected_owner_uid:
            raise VerificationError("approval_authority_owner", "approval authority state owner mismatch")
        if stat.S_IMODE(st.st_mode) & 0o022:
            raise VerificationError("approval_authority_mode", "approval authority state must not be group/other writable")

    def _with_lock(self, fn):
        with self._thread_lock:
            self._prepare_state()
            if self.lock_path.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority lock symlink rejected")
            fd = _open_nofollow(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fn()
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _read_json(self, path: Path) -> Dict[str, Any]:
        if path.is_symlink():
            raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected")
        try:
            fd = _open_nofollow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError as exc:
            raise VerificationError("approval_authority_missing", "broker-owned approval record not found") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected") from exc
            raise VerificationError("approval_authority_read", "cannot open broker-owned approval record") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise VerificationError("approval_authority_state", "approval authority record must be regular file")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise VerificationError("approval_authority_mode", "approval authority record must be mode 0600")
            if self.expected_owner_uid is not None and st.st_uid != self.expected_owner_uid:
                raise VerificationError("approval_authority_owner", "approval authority record owner mismatch")
            if st.st_size < 0 or st.st_size > MAX_RECORD_BYTES:
                raise VerificationError("approval_authority_size", "approval authority record too large")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(8192, MAX_RECORD_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RECORD_BYTES:
                    raise VerificationError("approval_authority_size", "approval authority record too large")
        finally:
            os.close(fd)
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationError("approval_authority_json", "invalid approval authority record") from exc
        if not isinstance(data, dict):
            raise VerificationError("approval_authority_json", "approval authority record must be object")
        return data

    def _encoded(self, payload: Dict[str, Any]) -> bytes:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > MAX_RECORD_BYTES:
            raise VerificationError("approval_authority_size", "approval authority record too large")
        return raw

    def _write_json_create(self, path: Path, payload: Dict[str, Any]) -> None:
        if path.exists() or path.is_symlink():
            raise VerificationError("approval_authority_exists", "approval authority record already exists")
        raw = self._encoded(payload)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        fd = _open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise VerificationError("approval_authority_exists", "approval authority record already exists") from exc
            os.chmod(path, 0o600, follow_symlinks=False)
            _fsync_dir(path.parent)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _write_json_replace(self, path: Path, payload: Dict[str, Any]) -> None:
        if path.is_symlink():
            raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected")
        raw = self._encoded(payload)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        fd = _open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            os.chmod(path, 0o600, follow_symlinks=False)
            _fsync_dir(path.parent)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
