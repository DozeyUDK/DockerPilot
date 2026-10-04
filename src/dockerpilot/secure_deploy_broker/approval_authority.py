"""Broker-owned approval provenance authority.

Client-supplied approval JSON is not authority here. This module owns one-shot
approval challenges and resulting approval records in broker-controlled state.
A later transport layer supplies the approver UID from SO_PEERCRED on a socket
that is unavailable to the DockerPilot Extras service account.
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
except ImportError:  # pragma: no cover
    fcntl = None

from .errors import VerificationError

CHALLENGE_TTL_SECONDS = 120
APPROVAL_TTL_SECONDS = 600
MAX_RECORD_BYTES = 64 * 1024
MAX_CHALLENGE_RECORDS = 1000
MAX_APPROVAL_RECORDS = 1000
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")


def _utcnow(clock: Optional[Callable[[], datetime]] = None) -> datetime:
    value = clock() if clock else datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise VerificationError("approval_authority_time", "broker clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: object) -> datetime:
    if not isinstance(value, str):
        raise VerificationError("approval_authority_time", "approval timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("approval_authority_time", "invalid approval timestamp") from exc
    if parsed.tzinfo is None:
        raise VerificationError("approval_authority_time", "approval timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), mode)


def _validate_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise VerificationError("approval_authority_id", f"invalid {field}")
    return value


def _validate_plan_sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise VerificationError("approval_authority_plan_hash", "invalid plan_sha256")
    return value


def validate_broker_approval_record(
    record: Dict[str, Any],
    *,
    expected_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a broker-owned approval v2 record independent of storage."""
    if record.get("approval_version") != 2:
        raise VerificationError("approval_authority_record", "unsupported broker approval version")
    approval_id = _validate_id(record.get("approval_id"), field="approval_id")
    if expected_id is not None and approval_id != expected_id:
        raise VerificationError("approval_authority_record", "approval record ID mismatch")
    _validate_id(record.get("challenge_id"), field="challenge_id")
    _validate_id(record.get("plan_id"), field="plan_id")
    _validate_plan_sha(record.get("plan_sha256"))
    nonce = record.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16 or len(nonce) > 128:
        raise VerificationError("approval_authority_record", "invalid approval nonce")
    _parse_ts(record.get("issued_at"))
    _parse_ts(record.get("approved_at"))
    _parse_ts(record.get("expires_at"))
    if record.get("status") not in {"approved", "consumed", "expired", "revoked"}:
        raise VerificationError("approval_authority_record", "invalid approval status")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"kind", "uid"}:
        raise VerificationError("approval_authority_provenance", "broker approval provenance invalid")
    if provenance.get("kind") != "unix_peer_uid":
        raise VerificationError("approval_authority_provenance", "unsupported broker approval provenance")
    try:
        uid = int(provenance.get("uid"))
    except (TypeError, ValueError) as exc:
        raise VerificationError("approval_authority_provenance", "broker approval UID invalid") from exc
    if uid < 0:
        raise VerificationError("approval_authority_provenance", "broker approval UID invalid")
    return record


class BrokerApprovalAuthority:
    """Durable broker-owned challenge and approval ledger."""

    def __init__(
        self,
        state_root: Path,
        *,
        allowed_approver_uids: FrozenSet[int],
        clock: Optional[Callable[[], datetime]] = None,
        challenge_ttl_seconds: int = CHALLENGE_TTL_SECONDS,
        approval_ttl_seconds: int = APPROVAL_TTL_SECONDS,
        expected_owner_uid: int,
        max_challenge_records: int = MAX_CHALLENGE_RECORDS,
        max_approval_records: int = MAX_APPROVAL_RECORDS,
    ):
        if not allowed_approver_uids or any(int(uid) < 0 for uid in allowed_approver_uids):
            raise VerificationError("approval_authority_uids", "non-empty approver UID allowlist required")
        if challenge_ttl_seconds <= 0 or challenge_ttl_seconds > 600:
            raise VerificationError("approval_authority_ttl", "challenge TTL out of range")
        if approval_ttl_seconds <= 0 or approval_ttl_seconds > 3600:
            raise VerificationError("approval_authority_ttl", "approval TTL out of range")
        try:
            owner_uid = int(expected_owner_uid)
        except (TypeError, ValueError) as exc:
            raise VerificationError("approval_authority_owner", "expected approval-authority owner UID required") from exc
        if owner_uid < 0:
            raise VerificationError("approval_authority_owner", "expected approval-authority owner UID required")
        if max_challenge_records <= 0 or max_challenge_records > 10000:
            raise VerificationError("approval_authority_limit", "challenge record limit out of range")
        if max_approval_records <= 0 or max_approval_records > 10000:
            raise VerificationError("approval_authority_limit", "approval record limit out of range")
        self.state_root = Path(state_root)
        self.allowed_approver_uids = frozenset(int(uid) for uid in allowed_approver_uids)
        self.clock = clock
        self.challenge_ttl_seconds = int(challenge_ttl_seconds)
        self.approval_ttl_seconds = int(approval_ttl_seconds)
        self.expected_owner_uid = owner_uid
        self.max_challenge_records = int(max_challenge_records)
        self.max_approval_records = int(max_approval_records)
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
            self._reclaim_completed_records(now)
            if self._count_records(self.challenges_dir) >= self.max_challenge_records:
                raise VerificationError("approval_authority_limit", "approval challenge record limit exceeded")
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
            self._validate_challenge_record(record)
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
            self._validate_challenge_record(challenge, expected_id=challenge_id)
            if challenge["status"] != "pending":
                raise VerificationError("approval_challenge_status", "approval challenge is not pending")
            if challenge["plan_sha256"] != expected_plan_sha256:
                raise VerificationError("approval_challenge_hash", "approval challenge plan hash mismatch")
            now = _utcnow(self.clock)
            self._reclaim_expired_approvals(now)
            if now >= _parse_ts(challenge["expires_at"]):
                expired = dict(challenge)
                expired["status"] = "expired"
                self._write_json_replace(challenge_path, expired)
                raise VerificationError("approval_challenge_expired", "approval challenge expired")
            if self._count_records(self.approvals_dir) >= self.max_approval_records:
                raise VerificationError("approval_authority_limit", "approval record limit exceeded")

            approval_id = _validate_id(challenge["approval_id"], field="approval_id")
            approval = {
                "approval_version": 2,
                "approval_id": approval_id,
                "challenge_id": challenge_id,
                "plan_id": challenge["plan_id"],
                "plan_sha256": challenge["plan_sha256"],
                "nonce": secrets.token_urlsafe(18),
                "issued_at": _iso(now),
                "approved_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=self.approval_ttl_seconds)),
                "status": "approved",
                "provenance": {"kind": "unix_peer_uid", "uid": uid},
            }
            self._validate_approval_record(approval, expected_id=approval_id)
            # Create first. A crash before challenge metadata update remains
            # authorized only because the trusted approver action already passed.
            # Reusing the challenge cannot overwrite this create-only record.
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
            self._validate_approval_record(approval, expected_id=approval_id)
            if approval["status"] != "approved":
                raise VerificationError("approval_authority_status", "broker approval is not active")
            if approval["plan_id"] != plan_id:
                raise VerificationError("approval_authority_plan", "broker approval plan_id mismatch")
            if approval["plan_sha256"] != plan_sha256:
                raise VerificationError("approval_authority_hash", "broker approval plan hash mismatch")
            uid = int(approval["provenance"]["uid"])
            if uid not in self.allowed_approver_uids:
                raise VerificationError("approval_authority_provenance", "broker approval UID no longer authorized")
            if _utcnow(self.clock) >= _parse_ts(approval["expires_at"]):
                raise VerificationError("approval_authority_expired", "broker approval expired")
            return dict(approval)

        return self._with_lock(op)

    def _challenge_path(self, challenge_id: str) -> Path:
        return self.challenges_dir / f"{challenge_id}.json"

    def _approval_path(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.json"

    def _validate_challenge_record(self, record: Dict[str, Any], *, expected_id: Optional[str] = None) -> None:
        if record.get("challenge_version") != 1:
            raise VerificationError("approval_authority_record", "unsupported challenge version")
        challenge_id = _validate_id(record.get("challenge_id"), field="challenge_id")
        if expected_id is not None and challenge_id != expected_id:
            raise VerificationError("approval_authority_record", "challenge record ID mismatch")
        _validate_id(record.get("approval_id"), field="approval_id")
        _validate_id(record.get("plan_id"), field="plan_id")
        _validate_plan_sha(record.get("plan_sha256"))
        nonce = record.get("nonce")
        if not isinstance(nonce, str) or len(nonce) < 16 or len(nonce) > 128:
            raise VerificationError("approval_authority_record", "invalid challenge nonce")
        _parse_ts(record.get("issued_at"))
        _parse_ts(record.get("expires_at"))
        if record.get("status") not in {"pending", "approved", "expired"}:
            raise VerificationError("approval_authority_record", "invalid challenge status")

    def _validate_approval_record(self, record: Dict[str, Any], *, expected_id: Optional[str] = None) -> None:
        validate_broker_approval_record(record, expected_id=expected_id)

    def _prepare_state(self) -> None:
        if fcntl is None:
            raise VerificationError("approval_authority_platform", "approval authority requires POSIX file locking")

        # Initialization happens before the cross-process ledger lock exists, so
        # first use must tolerate another broker process winning mkdir(). Always
        # revalidate the resulting filesystem object after EEXIST/exist_ok.
        try:
            self.state_root.mkdir(mode=0o700, exist_ok=True)
        except FileNotFoundError as exc:
            raise VerificationError("approval_authority_parent", "approval authority parent directory missing") from exc
        except OSError as exc:
            raise VerificationError("approval_authority_state", "cannot initialize approval authority state root") from exc
        self._assert_controlled_dir(self.state_root)

        for directory in (self.challenges_dir, self.approvals_dir):
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise VerificationError("approval_authority_state", "cannot initialize approval authority state directory") from exc
            self._assert_controlled_dir(directory)

    def _assert_controlled_dir(self, path: Path) -> None:
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise VerificationError("approval_authority_state", "approval authority state path must be directory")
        if st.st_uid != self.expected_owner_uid:
            raise VerificationError("approval_authority_owner", "approval authority state owner mismatch")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise VerificationError("approval_authority_mode", "approval authority state directory must be mode 0700")

    def _reclaim_completed_records(self, now: datetime) -> None:
        self._reclaim_completed_challenges(now)
        self._reclaim_expired_approvals(now)

    def _reclaim_completed_challenges(self, now: datetime) -> None:
        changed = False
        for path in self.challenges_dir.iterdir():
            if path.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected")
            if not path.name.endswith(".json"):
                continue
            if not path.is_file():
                raise VerificationError("approval_authority_state", "approval authority record must be regular file")
            record = self._read_json(path)
            expected_id = path.name.removesuffix(".json")
            self._validate_challenge_record(record, expected_id=expected_id)
            status = record["status"]
            # Once a challenge produced a durable broker-owned approval record,
            # the challenge itself no longer carries authority and may be
            # reclaimed. Pending challenges are retained only through their TTL.
            if status in {"approved", "expired"} or (
                status == "pending" and now >= _parse_ts(record["expires_at"])
            ):
                path.unlink()
                changed = True
        if changed:
            _fsync_dir(self.challenges_dir)

    def _reclaim_expired_approvals(self, now: datetime) -> None:
        changed = False
        for path in self.approvals_dir.iterdir():
            if path.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected")
            if not path.name.endswith(".json"):
                continue
            if not path.is_file():
                raise VerificationError("approval_authority_state", "approval authority record must be regular file")
            record = self._read_json(path)
            expected_id = path.name.removesuffix(".json")
            self._validate_approval_record(record, expected_id=expected_id)
            status = record["status"]
            if status in {"consumed", "expired", "revoked"} or now >= _parse_ts(record["expires_at"]):
                path.unlink()
                changed = True
        if changed:
            _fsync_dir(self.approvals_dir)

    def _count_records(self, directory: Path) -> int:
        count = 0
        for path in directory.iterdir():
            if path.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority record symlink rejected")
            if path.name.endswith(".json"):
                if not path.is_file():
                    raise VerificationError("approval_authority_state", "approval authority record must be regular file")
                count += 1
        return count

    def _with_lock(self, fn):
        with self._thread_lock:
            self._prepare_state()
            if self.lock_path.is_symlink():
                raise VerificationError("approval_authority_symlink", "approval authority lock symlink rejected")
            fd = _open_nofollow(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
                    raise VerificationError("approval_authority_mode", "approval authority lock must be regular mode 0600")
                if st.st_uid != self.expected_owner_uid:
                    raise VerificationError("approval_authority_owner", "approval authority lock owner mismatch")
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
                raise VerificationError("approval_authority_mode", "approval authority record must not grant group/other access")
            if st.st_uid != self.expected_owner_uid:
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
