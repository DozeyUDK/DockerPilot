"""Broker-owned Secure Deploy container canary.

Clients provide only identifiers and hashes. The broker owns the canary
template, workdir, command argv, environment, health checks, cleanup behavior,
and immutable admission bundle storage.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from dockerpilot.secure_deploy import compute_plan_sha256, validate_deployment_plan
from dockerpilot.secure_deploy.canonical import canonical_json_bytes, sha256_canonical, sha256_hex
from dockerpilot.secure_deploy.schemas import SchemaValidationError, load_schema
from dockerpilot.secure_deploy.schemas import _validate as validate_schema

from .approval import assert_approval_binds_plan, parse_ts, validate_approval_record
from .errors import VerificationError
from .verifier import BrokerDozeyguardConfig, verify_plan_independent

TEMPLATE_ID = "dockerpilot-secure-canary-v1"
PROJECT = "dockerpilot-secure-canary"
SERVICE = "web"
HOST = "127.0.0.1"
PORT = 18080
WORKDIR = Path("/var/lib/dockerpilot-secure-broker/canary/dockerpilot-secure-canary")
DEFAULT_CANARY_IMAGE = (
    "docker.io/library/nginx@"
    "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)
PLACEHOLDER_DIGEST = "sha256:" + ("a" * 64)
EXPECTED_HTTP_STATUS = 200
EXPECTED_HTTP_BODY = "Welcome to nginx"
STAGED = "staged"
APPROVED = "approved"
EXECUTING = "executing"
CONSUMED = "consumed"
EXPIRED = "expired"
REVOKED = "revoked"
FAILED_CLEANUP_OK = "failed_cleanup_ok"
FAILED_CLEANUP_FAILED = "failed_cleanup_failed"
REMOVED = "removed"
TERMINAL_STATES = {CONSUMED, EXPIRED, REVOKED, FAILED_CLEANUP_OK, FAILED_CLEANUP_FAILED, REMOVED}
MAX_JSON_BYTES = 1024 * 1024
MAX_AUDIT_EVENT_BYTES = 16 * 1024
MAX_EXECUTION_RECORDS = 1000
MAX_BUNDLE_RECORDS = 1000
HTTP_BODY_LIMIT = 4096
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_EXEC_RE = re.compile(r"^exec_[A-Fa-f0-9]{24}$")


@dataclass(frozen=True)
class CanaryPolicy:
    template_id: str = TEMPLATE_ID
    project: str = PROJECT
    service: str = SERVICE
    workdir: Path = WORKDIR
    host: str = HOST
    port: int = PORT
    image: str = DEFAULT_CANARY_IMAGE
    health_timeout_seconds: int = 60
    staged_bundle_ttl_seconds: int = 300
    health_poll_interval_seconds: float = 1.0
    command_timeout_seconds: int = 30
    expected_http_status: int = EXPECTED_HTTP_STATUS
    expected_http_body: str = EXPECTED_HTTP_BODY
    live_mode: bool = False

    def __post_init__(self) -> None:
        _digest_image_parts(self.image)
        if self.staged_bundle_ttl_seconds <= 0 or self.staged_bundle_ttl_seconds > 600:
            raise VerificationError("canary_bundle_ttl", "canary staged bundle TTL out of range")
        if self.live_mode and self.image.endswith("@" + PLACEHOLDER_DIGEST):
            raise VerificationError("canary_image_placeholder", "live canary image digest must be root-owned and non-placeholder")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None


class CommandRunner:
    """Run broker-owned commands with bounded streaming output."""

    def __init__(self, *, output_limit: int = 8192):
        self.output_limit = output_limit

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Dict[str, str],
        timeout: int,
    ) -> CommandResult:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            return CommandResult(-1, "", "", error=f"{type(exc).__name__}: {exc}")

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        flags = {"stdout_truncated": False, "stderr_truncated": False}

        def reader(stream, chunks: list[bytes], flag_name: str) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    current = sum(len(part) for part in chunks)
                    remaining = max(0, self.output_limit - current)
                    if remaining:
                        chunks.append(chunk[:remaining])
                    if len(chunk) > remaining:
                        flags[flag_name] = True
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        stdout_thread = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks, "stdout_truncated"), daemon=True)
        stderr_thread = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks, "stderr_truncated"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            returncode = proc.wait(timeout=5)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return CommandResult(
            returncode,
            stdout,
            stderr,
            timed_out=timed_out,
            stdout_truncated=flags["stdout_truncated"],
            stderr_truncated=flags["stderr_truncated"],
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _open_no_follow(path: Path, flags: int, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    return os.open(str(path), flags | nofollow, mode)


def _atomic_write_bytes(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise VerificationError("canary_symlink", f"symlink refused: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    fd = _open_no_follow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, mode)
        _fsync_dir(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _atomic_write_json(path: Path, payload: Dict[str, Any], *, mode: int = 0o600) -> None:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(raw) > MAX_JSON_BYTES:
        raise VerificationError("canary_json_too_large", "canary ledger object exceeds size limit")
    _atomic_write_bytes(path, raw, mode=mode)


def _read_bytes_no_follow(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> bytes:
    if path.is_symlink():
        raise VerificationError("canary_symlink", f"symlink refused: {path.name}")
    fd = _open_no_follow(path, os.O_RDONLY)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise VerificationError("canary_json_too_large", "canary ledger object exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise VerificationError("canary_read", "cannot read canary file") from exc
    finally:
        os.close(fd)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_read_bytes_no_follow(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("canary_ledger_read", "cannot read canary ledger") from exc
    if not isinstance(data, dict):
        raise VerificationError("canary_ledger_read", "ledger record must be object")
    return data


def _digest_image_parts(image: str) -> tuple[str, str]:
    if "@" not in image:
        raise VerificationError("canary_image", "canary image must be digest-only")
    reference, digest = image.split("@", 1)
    if not reference or not digest.startswith("sha256:") or len(digest) != 71:
        raise VerificationError("canary_image", "invalid canary image digest")
    if not _SHA_RE.fullmatch(digest.removeprefix("sha256:")):
        raise VerificationError("canary_image", "invalid canary image digest")
    return reference, digest


def canonical_compose(policy: CanaryPolicy) -> Dict[str, Any]:
    return {
        "name": policy.project,
        "services": {
            policy.service: {
                "image": policy.image,
                "user": "101:101",
                "read_only": True,
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "cap_add": [],
                "restart": "no",
                "pids_limit": 128,
                "mem_limit": "128m",
                "cpus": "0.25",
                "ports": [
                    {
                        "target": 80,
                        "published": policy.port,
                        "protocol": "tcp",
                        "host_ip": policy.host,
                    }
                ],
                "tmpfs": ["/var/cache/nginx", "/var/run", "/tmp"],
                "healthcheck": {
                    "test": ["CMD", "nginx", "-t"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 12,
                    "start_period": "0s",
                },
            }
        },
        "x-dockerpilot-secret-refs": [],
    }


def compose_bytes(compose: Dict[str, Any]) -> bytes:
    return canonical_json_bytes(compose) + b"\n"


def compose_sha256(compose: Dict[str, Any]) -> str:
    return sha256_hex(compose_bytes(compose).rstrip(b"\n"))


def controlled_env() -> Dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def up_argv(policy: CanaryPolicy) -> list[str]:
    return ["docker", "compose", "-p", policy.project, "-f", "compose.yaml", "up", "-d", "--remove-orphans"]


def down_argv(policy: CanaryPolicy) -> list[str]:
    return ["docker", "compose", "-p", policy.project, "-f", "compose.yaml", "down", "--volumes", "--remove-orphans"]


def ps_argv(policy: CanaryPolicy) -> list[str]:
    return ["docker", "compose", "-p", policy.project, "-f", "compose.yaml", "ps", "--format", "json", policy.service]


class CanaryLedger:
    def __init__(self, workdir: Path, *, max_records: int = MAX_EXECUTION_RECORDS):
        self.workdir = Path(workdir)
        self.max_records = max_records
        self._thread_lock = threading.RLock()

    @property
    def executions_dir(self) -> Path:
        return self.workdir / "executions"

    @property
    def replay_dir(self) -> Path:
        return self.workdir / "replay"

    @property
    def request_dir(self) -> Path:
        return self.workdir / "requests"

    @property
    def nonce_dir(self) -> Path:
        return self.workdir / "nonces"

    @property
    def bundles_dir(self) -> Path:
        return self.workdir / "admission_bundles"

    @property
    def audit_path(self) -> Path:
        return self.workdir / "audit.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.workdir / ".canary.lock"

    def _with_lock(self, fn):
        with self._thread_lock:
            self._prepare_dirs_unlocked()
            if self.lock_path.is_symlink():
                raise VerificationError("canary_lock_symlink", "canary lock symlink refused")
            fd = _open_no_follow(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fn()
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _prepare_dirs_unlocked(self) -> None:
        ensure_controlled_workdir(self.workdir)
        for directory in (self.executions_dir, self.replay_dir, self.request_dir, self.nonce_dir, self.bundles_dir):
            if directory.exists() and directory.is_symlink():
                raise VerificationError("canary_workdir_symlink", f"symlink refused: {directory.name}")
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        _fsync_dir(self.workdir)

    def prepare_dirs(self) -> None:
        self._with_lock(lambda: None)

    def execution_path(self, execution_id: str) -> Path:
        if not _EXEC_RE.fullmatch(execution_id or ""):
            raise VerificationError("canary_execution_id", "invalid canary execution id")
        return self.executions_dir / f"{execution_id}.json"

    def _checked_hash_path(self, base: Path, digest: str) -> Path:
        if not _SHA_RE.fullmatch(digest or ""):
            raise VerificationError("canary_hash", "invalid canary hash")
        return base / f"{digest}.json"

    def replay_path(self, replay_key: str) -> Path:
        return self._checked_hash_path(self.replay_dir, replay_key)

    def request_path(self, request_key: str) -> Path:
        return self._checked_hash_path(self.request_dir, request_key)

    def nonce_path(self, nonce_hash: str) -> Path:
        return self._checked_hash_path(self.nonce_dir, nonce_hash)

    def bundle_path(self, bundle_sha256: str) -> Path:
        return self._checked_hash_path(self.bundles_dir, bundle_sha256)

    def _request_key(self, *, plan_id: str, plan_sha256: str, approval_id: str) -> str:
        return sha256_canonical({"approval_id": approval_id, "plan_id": plan_id, "plan_sha256": plan_sha256})

    def _count_json_records(self, directory: Path) -> int:
        count = 0
        for path in directory.iterdir():
            if path.is_symlink():
                raise VerificationError("canary_symlink", f"symlink refused: {path.name}")
            if path.name.endswith(".json"):
                count += 1
                if count > self.max_records:
                    raise VerificationError("canary_record_limit", "canary record limit exceeded")
        return count

    def save_admission_bundle(
        self,
        *,
        plan: Dict[str, Any],
        approval: Dict[str, Any],
        template_id: str,
        staged_at: str,
        staged_bundle_expires_at: str,
    ) -> str:
        bundle = {
            "schema_version": 1,
            "template_id": template_id,
            "plan_id": plan.get("plan_id"),
            "plan_sha256": plan.get("plan_sha256"),
            "approval_id": approval.get("approval_id"),
            "staged_at": staged_at,
            "staged_bundle_expires_at": staged_bundle_expires_at,
            "plan": plan,
            "approval": approval,
        }
        self._validate_bundle_shape(bundle)
        bundle_sha256 = sha256_canonical(bundle)

        def op() -> str:
            if self._count_json_records(self.bundles_dir) >= MAX_BUNDLE_RECORDS:
                raise VerificationError("canary_bundle_limit", "canary admission bundle limit exceeded")
            path = self.bundle_path(bundle_sha256)
            if path.exists() and not path.is_symlink():
                return bundle_sha256
            _atomic_write_json(path, bundle)
            return bundle_sha256

        return self._with_lock(op)

    def save_staged(self, record: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        def op() -> tuple[Dict[str, Any], bool]:
            request = self.request_path(str(record["request_key"]))
            if request.exists() and not request.is_symlink():
                existing_id = _read_json(request).get("execution_id")
                if isinstance(existing_id, str):
                    existing = self._get_unlocked(existing_id)
                    if existing.get("admission_bundle_sha256") != record.get("admission_bundle_sha256"):
                        raise VerificationError("canary_staged_conflict", "staged admission record conflict")
                    return existing, False
            if self._count_json_records(self.executions_dir) >= self.max_records:
                raise VerificationError("canary_record_limit", "canary record limit exceeded")
            path = self.execution_path(str(record["execution_id"]))
            if path.exists() or path.is_symlink():
                raise VerificationError("canary_execution_exists", "execution id collision")
            _atomic_write_json(path, record)
            _atomic_write_json(request, {"execution_id": record["execution_id"]})
            return dict(record), True

        return self._with_lock(op)

    def load_admission_bundle(self, bundle_sha256: str) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            path = self.bundle_path(bundle_sha256)
            if not path.exists():
                raise VerificationError("canary_bundle_missing", "canary admission bundle missing")
            bundle = _read_json(path)
            self._validate_bundle_shape(bundle)
            if sha256_canonical(bundle) != bundle_sha256:
                raise VerificationError("canary_bundle_hash", "canary admission bundle hash mismatch")
            return bundle

        return self._with_lock(op)

    def _validate_bundle_shape(self, bundle: Dict[str, Any]) -> None:
        try:
            validate_schema(bundle, load_schema("secure-deploy-canary-admission-bundle-v1.schema.json"), "$")
        except SchemaValidationError as exc:
            raise VerificationError("canary_bundle_schema", str(exc)) from exc

    def save_new(self, record: Dict[str, Any]) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            request = self.request_path(str(record["request_key"]))
            if request.exists() and not request.is_symlink():
                existing_id = _read_json(request).get("execution_id")
                if isinstance(existing_id, str):
                    return self._get_unlocked(existing_id)
            replay = self.replay_path(str(record["replay_key"]))
            if replay.exists() and not replay.is_symlink():
                existing_id = _read_json(replay).get("execution_id")
                if isinstance(existing_id, str):
                    return self._get_unlocked(existing_id)
            nonce = self.nonce_path(str(record["approval_nonce_hash"]))
            if nonce.exists() and not nonce.is_symlink():
                existing_id = _read_json(nonce).get("execution_id")
                if isinstance(existing_id, str):
                    raise VerificationError("canary_nonce_replay", "approval nonce already used")
            if self._count_json_records(self.executions_dir) >= self.max_records:
                raise VerificationError("canary_record_limit", "canary record limit exceeded")
            path = self.execution_path(str(record["execution_id"]))
            if path.exists() or path.is_symlink():
                raise VerificationError("canary_execution_exists", "execution id collision")
            _atomic_write_json(path, record)
            pointer = {"execution_id": record["execution_id"]}
            _atomic_write_json(request, pointer)
            _atomic_write_json(replay, pointer)
            _atomic_write_json(nonce, pointer)
            return dict(record)

        return self._with_lock(op)

    def approve_staged(
        self,
        record: Dict[str, Any],
        *,
        nonce_hash: str,
        replay_key: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            current = self._get_unlocked(str(record["execution_id"]))
            if current.get("state") != STAGED:
                return current
            if current.get("admission_bundle_sha256") != record.get("admission_bundle_sha256"):
                raise VerificationError("canary_staged_conflict", "staged admission record conflict")
            replay = self.replay_path(replay_key)
            if replay.exists() and not replay.is_symlink():
                existing_id = _read_json(replay).get("execution_id")
                if isinstance(existing_id, str):
                    return self._get_unlocked(existing_id)
            nonce = self.nonce_path(nonce_hash)
            if nonce.exists() and not nonce.is_symlink():
                raise VerificationError("canary_nonce_replay", "approval nonce already used")
            updated = dict(current)
            updated.update(fields)
            updated["state"] = APPROVED
            updated["replay_key"] = replay_key
            updated["approval_nonce_hash"] = nonce_hash
            _atomic_write_json(self.execution_path(str(updated["execution_id"])), updated)
            pointer = {"execution_id": updated["execution_id"]}
            _atomic_write_json(replay, pointer)
            _atomic_write_json(nonce, pointer)
            return updated

        return self._with_lock(op)

    def _get_unlocked(self, execution_id: str) -> Dict[str, Any]:
        path = self.execution_path(execution_id)
        if not path.exists():
            raise VerificationError("canary_execution_missing", "canary execution not found")
        return _read_json(path)

    def get(self, execution_id: str) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            return self._get_unlocked(execution_id)

        return self._with_lock(op)

    def find(self, *, plan_id: str, plan_sha256: str, approval_id: str) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            request = self.request_path(self._request_key(plan_id=plan_id, plan_sha256=plan_sha256, approval_id=approval_id))
            if not request.exists():
                raise VerificationError("canary_execution_missing", "canary execution not admitted")
            existing_id = _read_json(request).get("execution_id")
            if not isinstance(existing_id, str):
                raise VerificationError("canary_execution_missing", "canary execution pointer invalid")
            return self._get_unlocked(existing_id)

        return self._with_lock(op)

    def replace(self, record: Dict[str, Any], *, expected_state: Optional[str] = None) -> Dict[str, Any]:
        def op() -> Dict[str, Any]:
            path = self.execution_path(str(record["execution_id"]))
            current = _read_json(path)
            if expected_state is not None and current.get("state") != expected_state:
                return current
            updated = dict(record)
            _atomic_write_json(path, updated)
            return updated

        return self._with_lock(op)

    def append_audit(self, event: Dict[str, Any]) -> str:
        def op() -> str:
            if self.audit_path.is_symlink():
                raise VerificationError("canary_audit_symlink", "canary audit symlink refused")
            clean = sanitize_audit_event(redact_event(event))
            raw = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_AUDIT_EVENT_BYTES:
                raise VerificationError("canary_audit_too_large", "canary audit event exceeds size limit")
            audit_id = "audit_" + sha256_hex(raw)[:24]
            fd = _open_no_follow(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, raw + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_dir(self.workdir)
            return audit_id

        return self._with_lock(op)


def redact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if any(word in key.lower() for word in ("secret", "token", "password")) else scrub(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(event)


def sanitize_audit_event(event: Dict[str, Any]) -> Dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {clean(str(key)): clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value[:128]]
        if isinstance(value, str):
            text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in value)
            return text[:512]
        return value

    cleaned = clean(event)
    if not isinstance(cleaned, dict):
        raise VerificationError("canary_audit", "audit event must be object")
    return cleaned


def ensure_controlled_workdir(workdir: Path) -> None:
    if workdir.exists() and workdir.is_symlink():
        raise VerificationError("canary_workdir_symlink", "canary workdir symlink refused")
    workdir.mkdir(parents=True, exist_ok=True)
    os.chmod(workdir, 0o700)
    if workdir.is_symlink() or not workdir.is_dir():
        raise VerificationError("canary_workdir", "canary workdir invalid")
    allowed = {
        ".canary.lock",
        "compose.yaml",
        "executions",
        "replay",
        "requests",
        "nonces",
        "admission_bundles",
        "audit.jsonl",
    }
    for child in workdir.iterdir():
        is_broker_compose_tmp = child.name.startswith("compose.yaml.tmp.")
        if child.name not in allowed and not is_broker_compose_tmp:
            raise VerificationError("canary_workdir_dirty", f"unexpected canary workdir entry: {child.name}")
        if child.is_symlink():
            raise VerificationError("canary_workdir_symlink", f"symlink refused: {child.name}")


def write_compose(workdir: Path, compose: Dict[str, Any]) -> str:
    ensure_controlled_workdir(workdir)
    path = workdir / "compose.yaml"
    if path.is_symlink():
        raise VerificationError("canary_compose_symlink", "compose.yaml symlink refused")
    raw = compose_bytes(compose)
    _atomic_write_bytes(path, raw, mode=0o600)
    return sha256_hex(read_compose_bytes(workdir).rstrip(b"\n"))


def read_compose_bytes(workdir: Path) -> bytes:
    path = workdir / "compose.yaml"
    if path.is_symlink() or not path.is_file():
        raise VerificationError("canary_compose_missing", "compose.yaml missing or symlink")
    return _read_bytes_no_follow(path, max_bytes=MAX_JSON_BYTES)


def compose_file_sha256(workdir: Path) -> str:
    return sha256_hex(read_compose_bytes(workdir).rstrip(b"\n"))


def port_is_free(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def http_get(host: str, port: int, *, timeout: float) -> tuple[int, str]:
    url = f"http://{host}:{port}/"
    try:
        with urllib_request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed localhost URL.
            body = response.read(HTTP_BODY_LIMIT + 1)
            if len(body) > HTTP_BODY_LIMIT:
                raise VerificationError("canary_http_body", "canary HTTP body exceeds limit")
            return int(response.status), body.decode("utf-8", errors="replace")
    except urllib_error.URLError as exc:
        raise VerificationError("canary_http", "canary HTTP probe failed") from exc


def _parse_ps(stdout: str, policy: CanaryPolicy) -> Dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise VerificationError("canary_ps_empty", "docker compose ps returned no data")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerificationError("canary_ps_json", "docker compose ps JSON invalid") from exc
    entries = data if isinstance(data, list) else [data]
    if not entries:
        raise VerificationError("canary_ps_empty", "canary service not listed")
    matches = []
    for item in entries:
        if not isinstance(item, dict):
            raise VerificationError("canary_ps_json", "docker compose ps entry invalid")
        service = _field_ci(item, "Service", "service")
        project = _field_ci(item, "Project", "project")
        name = _field_ci(item, "Name", "name")
        if service == policy.service and (not project or project == policy.project) and (not name or policy.project in name):
            matches.append(item)
    if len(matches) != 1:
        raise VerificationError("canary_identity", "canary compose ps identity mismatch")
    return matches[0]


def _field_ci(data: Dict[str, Any], *names: str) -> str:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return str(value)
    return ""


class CanaryManager:
    def __init__(
        self,
        *,
        policy: CanaryPolicy,
        ledger: Optional[CanaryLedger] = None,
        command_runner: Optional[CommandRunner] = None,
        port_checker: Callable[[str, int], bool] = port_is_free,
        http_probe: Callable[[str, int, float], tuple[int, str]] | None = None,
        now: Callable[[], datetime] = _utcnow,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        pre_up_hook: Callable[[], None] | None = None,
    ):
        self.policy = policy
        self.ledger = ledger or CanaryLedger(policy.workdir)
        self.command_runner = command_runner or CommandRunner()
        self.port_checker = port_checker
        self.http_probe = http_probe or (lambda host, port, timeout: http_get(host, port, timeout=timeout))
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.pre_up_hook = pre_up_hook

    def admit(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        approval_id: str,
        admission_bundle_sha256: str,
        template_id: str,
        dozeyguard_config: BrokerDozeyguardConfig,
        run_dozeyguard: Callable,
        normalize_spec_to_compose: Callable,
        plan_firewall_actions: Callable,
    ) -> Dict[str, Any]:
        if template_id != self.policy.template_id:
            raise VerificationError("canary_template", "unsupported canary template")
        bundle = self.ledger.load_admission_bundle(admission_bundle_sha256)
        if (
            bundle.get("template_id") != template_id
            or bundle.get("plan_id") != plan_id
            or bundle.get("plan_sha256") != plan_sha256
            or bundle.get("approval_id") != approval_id
        ):
            raise VerificationError("canary_bundle_binding", "canary admission bundle does not match request")
        plan = bundle["plan"]
        approval = bundle["approval"]
        self._assert_staged_bundle_active(bundle)
        staged = self.ledger.find(plan_id=plan_id, plan_sha256=plan_sha256, approval_id=approval_id)
        if staged.get("admission_bundle_sha256") != admission_bundle_sha256:
            raise VerificationError("canary_bundle_binding", "staged admission record does not match bundle")
        if staged.get("state") == REVOKED:
            raise VerificationError("canary_revoked", "canary admission revoked")
        if staged.get("state") == APPROVED:
            return {"status": staged["state"], "execution_id": staged["execution_id"], "template_id": staged["template_id"], "audit_id": staged.get("audit_id")}
        if staged.get("state") != STAGED:
            raise VerificationError("canary_admission_state", "canary admission is not staged")
        self._validate_plan_approval(plan, approval, require_approval=True)
        verification = verify_plan_independent(
            plan,
            approval,
            dozeyguard_config=dozeyguard_config,
            now=self.now(),
            require_approval=True,
            run_dozeyguard=run_dozeyguard,
            normalize_spec_to_compose=normalize_spec_to_compose,
            plan_firewall_actions=plan_firewall_actions,
        )
        self._assert_canary_plan(plan, normalize_spec_to_compose)
        nonce_hash = sha256_hex(str(approval["nonce"]).encode("utf-8"))
        replay_key = sha256_canonical(
            {
                "approval_id": approval["approval_id"],
                "nonce_hash": nonce_hash,
                "plan_id": plan["plan_id"],
                "plan_sha256": plan["plan_sha256"],
                "template_id": self.policy.template_id,
            }
        )
        saved = self.ledger.approve_staged(
            staged,
            nonce_hash=nonce_hash,
            replay_key=replay_key,
            fields={
                "actor": approval["actor"],
                "approval_expires_at": approval["expires_at"],
                "admitted_at": _iso(self.now()),
                "broker_verification_sha256": verification.get("broker_verification_sha256"),
                "compose_sha256": compose_sha256(canonical_compose(self.policy)),
            },
        )
        if saved.get("state") == REVOKED:
            raise VerificationError("canary_revoked", "canary admission revoked")
        if saved.get("state") != APPROVED:
            raise VerificationError("canary_admission_state", "canary admission is not approved")
        audit_id = self.ledger.append_audit(
            {
                "event": "canary_admitted",
                "execution_id": saved["execution_id"],
                "plan_id": saved["plan_id"],
                "plan_sha256": saved["plan_sha256"],
                "approval_id": saved["approval_id"],
                "approval_nonce_hash": saved["approval_nonce_hash"],
                "actor": saved["actor"],
                "template_id": saved["template_id"],
                "compose_sha256": saved["compose_sha256"],
            }
        )
        saved["audit_id"] = audit_id
        self.ledger.replace(saved)
        return {"status": saved["state"], "execution_id": saved["execution_id"], "template_id": saved["template_id"], "audit_id": audit_id}

    def maybe_stage_admission_bundle(
        self,
        *,
        plan: Dict[str, Any],
        approval: Dict[str, Any],
        normalize_spec_to_compose: Callable,
    ) -> str | None:
        if not self._looks_like_canary_plan(plan):
            return None
        self._validate_plan_approval(plan, approval, require_approval=True)
        self._assert_canary_plan(plan, normalize_spec_to_compose)
        request_key = self.ledger._request_key(
            plan_id=str(plan["plan_id"]),
            plan_sha256=str(plan["plan_sha256"]),
            approval_id=str(approval["approval_id"]),
        )
        try:
            existing = self.ledger.find(
                plan_id=str(plan["plan_id"]),
                plan_sha256=str(plan["plan_sha256"]),
                approval_id=str(approval["approval_id"]),
            )
        except VerificationError as exc:
            if exc.code != "canary_execution_missing":
                raise
        else:
            if existing.get("state") == REVOKED:
                raise VerificationError("canary_revoked", "canary admission revoked")
            if self._record_staged_bundle_expired(existing):
                raise VerificationError("canary_bundle_expired", "canary staged bundle expired")
            bundle_sha = str(existing.get("admission_bundle_sha256") or "")
            if _SHA_RE.fullmatch(bundle_sha):
                return bundle_sha
            raise VerificationError("canary_bundle_missing", "staged admission bundle missing")
        now = self.now()
        staged_at = _iso(now)
        staged_bundle_expires_at = _iso(now + timedelta(seconds=self.policy.staged_bundle_ttl_seconds))
        bundle_sha = self.ledger.save_admission_bundle(
            plan=plan,
            approval=approval,
            template_id=self.policy.template_id,
            staged_at=staged_at,
            staged_bundle_expires_at=staged_bundle_expires_at,
        )
        record, created = self.ledger.save_staged(
            {
                "schema_version": 1,
                "execution_id": "exec_" + secrets.token_hex(12),
                "template_id": self.policy.template_id,
                "state": STAGED,
                "plan_id": plan["plan_id"],
                "plan_sha256": plan["plan_sha256"],
                "approval_id": approval["approval_id"],
                "admission_bundle_sha256": bundle_sha,
                "request_key": request_key,
                "staged_at": staged_at,
                "staged_bundle_expires_at": staged_bundle_expires_at,
            }
        )
        if record.get("state") == REVOKED:
            raise VerificationError("canary_revoked", "canary admission revoked")
        if record.get("state") != STAGED and record.get("admission_bundle_sha256") != bundle_sha:
            raise VerificationError("canary_staged_conflict", "staged admission record conflict")
        audit_id = self.ledger.append_audit(
            {
                "event": "canary_admission_staged",
                "plan_id": plan["plan_id"],
                "plan_sha256": plan["plan_sha256"],
                "approval_id": approval["approval_id"],
                "admission_bundle_sha256": bundle_sha,
                "staged_bundle_expires_at": record.get("staged_bundle_expires_at"),
                "created": created,
            }
        )
        if created:
            updated = dict(record)
            updated["audit_id"] = audit_id
            self.ledger.replace(updated)
        return bundle_sha

    def revoke_admission(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        approval_id: str,
        admission_bundle_sha256: str,
    ) -> Dict[str, Any]:
        bundle = self.ledger.load_admission_bundle(admission_bundle_sha256)
        if (
            bundle.get("template_id") != self.policy.template_id
            or bundle.get("plan_id") != plan_id
            or bundle.get("plan_sha256") != plan_sha256
            or bundle.get("approval_id") != approval_id
        ):
            raise VerificationError("canary_bundle_binding", "canary admission bundle does not match request")
        record = self.ledger.find(plan_id=plan_id, plan_sha256=plan_sha256, approval_id=approval_id)
        if record.get("admission_bundle_sha256") != admission_bundle_sha256:
            raise VerificationError("canary_bundle_binding", "staged admission record does not match bundle")
        state = record.get("state")
        if state == REVOKED:
            return self._revocation_result(record, replay=True)
        if state == EXECUTING:
            raise VerificationError("canary_revoke_state", "executing canary admission cannot be revoked")
        if state in TERMINAL_STATES:
            raise VerificationError("canary_revoke_state", "terminal canary admission cannot be revoked")
        if state not in {STAGED, APPROVED}:
            raise VerificationError("canary_revoke_state", "canary admission is not revocable")
        updated = dict(record)
        updated["state"] = REVOKED
        updated["revoked_at"] = _iso(self.now())
        revoked = self.ledger.replace(updated, expected_state=str(state))
        if revoked.get("state") != REVOKED:
            raise VerificationError("canary_revoke_state", "concurrent canary admission transition rejected")
        audit_id = self.ledger.append_audit(
            {
                "event": "canary_admission_revoked",
                "execution_id": revoked["execution_id"],
                "plan_id": revoked["plan_id"],
                "plan_sha256": revoked["plan_sha256"],
                "approval_id": revoked["approval_id"],
                "admission_bundle_sha256": admission_bundle_sha256,
                "template_id": revoked["template_id"],
            }
        )
        revoked["audit_id"] = audit_id
        revoked = self.ledger.replace(revoked)
        return self._revocation_result(revoked)

    def deploy(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        approval_id: str,
        run_dozeyguard_bytes: Callable[[bytes, BrokerDozeyguardConfig], Dict[str, Any]],
        dozeyguard_config: BrokerDozeyguardConfig,
    ) -> Dict[str, Any]:
        record = self.ledger.find(plan_id=plan_id, plan_sha256=plan_sha256, approval_id=approval_id)
        if record.get("state") == REVOKED:
            raise VerificationError("canary_revoked", "canary admission revoked")
        if record.get("state") == STAGED:
            raise VerificationError("canary_not_admitted", "canary admission is not approved")
        if record.get("state") != APPROVED:
            return self._result(record, replay=True)
        if self._record_staged_bundle_expired(record):
            expired = dict(record)
            expired["state"] = EXPIRED
            expired["finished_at"] = _iso(self.now())
            expired = self.ledger.replace(expired, expected_state=APPROVED)
            raise VerificationError("canary_bundle_expired", "canary staged bundle expired")
        if self._record_approval_expired(record):
            expired = dict(record)
            expired["state"] = EXPIRED
            expired["finished_at"] = _iso(self.now())
            expired = self.ledger.replace(expired, expected_state=APPROVED)
            audit_id = self.ledger.append_audit(
                {
                    "event": "canary_expired",
                    "execution_id": expired["execution_id"],
                    "plan_id": expired["plan_id"],
                    "plan_sha256": expired["plan_sha256"],
                    "approval_id": expired["approval_id"],
                    "approval_nonce_hash": expired["approval_nonce_hash"],
                    "template_id": expired["template_id"],
                }
            )
            expired["audit_id"] = audit_id
            self.ledger.replace(expired)
            raise VerificationError("approval_expired", "approval expired")
        if not self.port_checker(self.policy.host, self.policy.port):
            raise VerificationError("canary_port_occupied", "canary localhost port is occupied")
        record = dict(record)
        claim_id = "claim_" + sha256_hex(f"{os.getpid()}:{threading.get_ident()}:{self.monotonic()}".encode("utf-8"))[:24]
        record["state"] = EXECUTING
        record["claim_id"] = claim_id
        record["started_at"] = _iso(self.now())
        claimed = self.ledger.replace(record, expected_state=APPROVED)
        if claimed.get("state") != EXECUTING or claimed.get("claim_id") != claim_id:
            return self._result(claimed, replay=True)

        primary_error: dict[str, str] | None = None
        cleanup = {"attempted": False, "ok": False}
        health: Dict[str, Any] | None = None
        try:
            compose = canonical_compose(self.policy)
            fresh_compose_sha = compose_sha256(compose)
            if fresh_compose_sha != claimed.get("compose_sha256"):
                raise VerificationError("canary_compose_hash", "canonical compose hash drift")
            written_sha = write_compose(self.policy.workdir, compose)
            if written_sha != fresh_compose_sha:
                raise VerificationError("canary_compose_write", "written compose hash mismatch")
            final_bytes = read_compose_bytes(self.policy.workdir)
            if sha256_hex(final_bytes.rstrip(b"\n")) != fresh_compose_sha:
                raise VerificationError("canary_compose_drift", "compose hash changed before scan")
            report = run_dozeyguard_bytes(final_bytes, dozeyguard_config)
            summary = report.get("summary") or {}
            result = report.get("result") or {}
            if int(summary.get("blocking") or 0) > 0 or int(result.get("exit_code") or 0) == 2:
                raise VerificationError("canary_dozeyguard", "Dozeyguard blocking findings")
            if self.pre_up_hook:
                self.pre_up_hook()
            if compose_file_sha256(self.policy.workdir) != fresh_compose_sha:
                raise VerificationError("canary_compose_drift", "compose hash changed before apply")
            up = self._run_owned(up_argv(self.policy))
            if up.timed_out:
                raise VerificationError("canary_compose_up_timeout", "compose up timed out")
            if up.returncode != 0:
                raise VerificationError("canary_compose_up", "compose up failed")
            health = self._health()
            cleanup = self._cleanup()
            state = CONSUMED if cleanup["ok"] else FAILED_CLEANUP_FAILED
            return self._terminal(claimed, state, health=health, cleanup=cleanup)
        except VerificationError as exc:
            primary_error = {"code": exc.code, "message": exc.message}
            cleanup = self._cleanup()
            state = FAILED_CLEANUP_OK if cleanup["ok"] else FAILED_CLEANUP_FAILED
            self._terminal(claimed, state, health=health, cleanup=cleanup, primary_error=primary_error)
            raise
        except Exception as exc:  # noqa: BLE001
            primary_error = {"code": "canary_internal", "message": "canary internal error"}
            cleanup = self._cleanup()
            state = FAILED_CLEANUP_OK if cleanup["ok"] else FAILED_CLEANUP_FAILED
            self._terminal(claimed, state, health=health, cleanup=cleanup, primary_error=primary_error)
            raise VerificationError("canary_internal", "canary internal error") from exc

    def remove(self, *, execution_id: str) -> Dict[str, Any]:
        record = self.ledger.get(execution_id)
        cleanup = self._cleanup()
        updated = dict(record)
        updated["state"] = REMOVED if cleanup["ok"] else FAILED_CLEANUP_FAILED
        updated["cleanup"] = cleanup
        updated["finished_at"] = _iso(self.now())
        updated = self.ledger.replace(updated)
        audit_id = self.ledger.append_audit(
            {"event": "canary_removed", "execution_id": execution_id, "state": updated["state"], "cleanup": cleanup, "project": self.policy.project}
        )
        updated["audit_id"] = audit_id
        updated = self.ledger.replace(updated)
        return self._result(updated)

    def _validate_plan_approval(self, plan: Dict[str, Any], approval: Dict[str, Any], *, require_approval: bool) -> None:
        try:
            validate_deployment_plan(plan)
        except SchemaValidationError as exc:
            raise VerificationError("plan_schema", str(exc)) from exc
        validate_approval_record(approval)
        recomputed = compute_plan_sha256(plan)
        if plan.get("plan_sha256") != recomputed:
            raise VerificationError("plan_hash_mismatch", "plan_sha256 mismatch")
        if approval.get("actor") != plan.get("actor"):
            raise VerificationError("approval_actor_mismatch", "approval actor mismatch")
        if require_approval:
            assert_approval_binds_plan(approval, plan_id=str(plan["plan_id"]), plan_sha256=str(plan["plan_sha256"]), now=self.now(), require_status=APPROVED)

    def _looks_like_canary_plan(self, plan: Dict[str, Any]) -> bool:
        source_spec = plan.get("source_spec")
        if not isinstance(source_spec, dict):
            return False
        metadata = source_spec.get("metadata") or {}
        if metadata.get("project") != self.policy.project or metadata.get("service") != self.policy.service:
            return False
        image = source_spec.get("image") or {}
        try:
            reference, digest = _digest_image_parts(self.policy.image)
        except VerificationError:
            return False
        return image.get("reference") == reference and image.get("digest") == digest

    def _record_approval_expired(self, record: Dict[str, Any]) -> bool:
        try:
            expires = parse_ts(str(record["approval_expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError("approval_expired", "approval expiry invalid") from exc
        return self.now() >= expires

    def _assert_staged_bundle_active(self, bundle: Dict[str, Any]) -> None:
        try:
            expires = parse_ts(str(bundle["staged_bundle_expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError("canary_bundle_expired", "canary staged bundle expiry invalid") from exc
        if self.now() >= expires:
            raise VerificationError("canary_bundle_expired", "canary staged bundle expired")

    def _record_staged_bundle_expired(self, record: Dict[str, Any]) -> bool:
        try:
            expires = parse_ts(str(record["staged_bundle_expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError("canary_bundle_expired", "canary staged bundle expiry invalid") from exc
        return self.now() >= expires

    def _assert_canary_plan(self, plan: Dict[str, Any], normalize_spec_to_compose: Callable) -> None:
        source_spec = plan.get("source_spec")
        if not isinstance(source_spec, dict):
            raise VerificationError("canary_source_spec", "canary plan requires source_spec")
        reference, digest = _digest_image_parts(self.policy.image)
        metadata = source_spec.get("metadata") or {}
        image = source_spec.get("image") or {}
        network = source_spec.get("network") or {}
        storage = source_spec.get("storage") or {}
        secrets = source_spec.get("secrets") or {}
        runtime = source_spec.get("runtime") or {}
        health = source_spec.get("health") or {}
        deployment = source_spec.get("deployment") or {}
        if metadata.get("project") != self.policy.project or metadata.get("service") != self.policy.service:
            raise VerificationError("canary_identity", "canary project/service mismatch")
        if image.get("reference") != reference or image.get("digest") != digest:
            raise VerificationError("canary_image", "canary image mismatch")
        if storage.get("volumes") or (secrets.get("refs") or []):
            raise VerificationError("canary_storage", "canary forbids volumes and secrets")
        if runtime.get("read_only") is not True or runtime.get("no_new_privileges") is not True:
            raise VerificationError("canary_runtime", "canary runtime invariants missing")
        if runtime.get("user") != "101:101" or runtime.get("restart_policy") != "no":
            raise VerificationError("canary_runtime", "canary user/restart policy mismatch")
        if [str(cap).upper() for cap in (runtime.get("cap_drop") or [])] != ["ALL"]:
            raise VerificationError("canary_caps", "canary must drop ALL capabilities")
        if runtime.get("cap_add"):
            raise VerificationError("canary_caps", "canary must not add capabilities")
        if health != {"required": True, "test": ["CMD", "nginx", "-t"], "interval_seconds": 5, "timeout_seconds": 3, "retries": 12, "start_period_seconds": 0}:
            raise VerificationError("canary_healthcheck", "canary healthcheck mismatch")
        if int(deployment.get("health_timeout_seconds") or 0) != self.policy.health_timeout_seconds:
            raise VerificationError("canary_health_timeout", "canary health timeout mismatch")
        ports = network.get("published_ports") or []
        if network.get("exposure") != "localhost" or len(ports) != 1:
            raise VerificationError("canary_network", "canary must expose exactly one localhost port")
        port = ports[0]
        if port.get("bind_address") != self.policy.host or int(port.get("host_port") or 0) != self.policy.port or int(port.get("container_port") or 0) != 80:
            raise VerificationError("canary_port", "canary port mismatch")
        compose = canonical_compose(self.policy)
        normalized = normalize_spec_to_compose(source_spec)
        if normalized != compose:
            raise VerificationError("canary_compose", "plan normalized compose differs from broker template")
        if plan.get("normalized_compose_sha256") != compose_sha256(compose):
            raise VerificationError("canary_compose_hash", "plan compose hash differs from broker template")

    def _run_owned(self, argv: list[str]) -> CommandResult:
        return self.command_runner.run(argv, cwd=self.policy.workdir, env=controlled_env(), timeout=self.policy.command_timeout_seconds)

    def _cleanup(self) -> Dict[str, Any]:
        try:
            result = self._run_owned(down_argv(self.policy))
            return {
                "attempted": True,
                "ok": result.returncode == 0 and not result.timed_out and result.error is None,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
                "error": result.error,
            }
        except Exception as exc:  # noqa: BLE001
            return {"attempted": True, "ok": False, "returncode": None, "timed_out": False, "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}"}

    def _health(self) -> Dict[str, Any]:
        deadline = self.monotonic() + self.policy.health_timeout_seconds
        last: Dict[str, Any] = {}
        while self.monotonic() <= deadline:
            ps = self._run_owned(ps_argv(self.policy))
            if ps.timed_out:
                last = {"running": False, "healthy": False, "detail": "ps timed out"}
            elif ps.returncode != 0:
                last = {"running": False, "healthy": False, "detail": ps.stderr}
            else:
                data = _parse_ps(ps.stdout, self.policy)
                state = _field_ci(data, "State", "state")
                health = _field_ci(data, "Health", "HealthStatus", "health")
                running = state.lower() == "running"
                healthy = health.lower() == "healthy"
                if running and healthy:
                    try:
                        status, body = self.http_probe(self.policy.host, self.policy.port, 3.0)
                    except Exception as exc:  # noqa: BLE001
                        last = {"running": True, "healthy": True, "http_error": f"{type(exc).__name__}: {exc}"}
                    else:
                        if len(body) > HTTP_BODY_LIMIT:
                            last = {"running": True, "healthy": True, "http_body_oversized": True}
                        else:
                            http_ok = status == self.policy.expected_http_status and self.policy.expected_http_body in body
                            if http_ok:
                                return {"ok": True, "running": True, "healthy": True, "http_status": status}
                            last = {"running": True, "healthy": True, "http_status": status, "http_ok": False}
                else:
                    last = {"running": running, "healthy": healthy, "state": state, "health": health}
            self.sleep(min(self.policy.health_poll_interval_seconds, max(0.0, deadline - self.monotonic())))
        raise VerificationError("canary_health", f"canary health failed: {last}")

    def _terminal(self, record: Dict[str, Any], state: str, **fields: Any) -> Dict[str, Any]:
        updated = dict(record)
        updated.update(fields)
        updated["state"] = state
        updated["finished_at"] = _iso(self.now())
        updated = self.ledger.replace(updated)
        audit_id = self.ledger.append_audit(
            {
                "event": "canary_finished",
                "execution_id": updated["execution_id"],
                "state": updated["state"],
                "plan_id": updated["plan_id"],
                "plan_sha256": updated["plan_sha256"],
                "approval_id": updated["approval_id"],
                "approval_nonce_hash": updated["approval_nonce_hash"],
                "compose_sha256": updated.get("compose_sha256"),
                "health": updated.get("health"),
                "cleanup": updated.get("cleanup"),
                "primary_error": updated.get("primary_error"),
            }
        )
        updated["audit_id"] = audit_id
        updated = self.ledger.replace(updated)
        return self._result(updated)

    def _result(self, record: Dict[str, Any], *, replay: bool = False) -> Dict[str, Any]:
        return {
            "status": "pass" if record.get("state") in {CONSUMED, REMOVED} else "fail",
            "state": record.get("state"),
            "replay": replay,
            "execution_id": record.get("execution_id"),
            "template_id": record.get("template_id"),
            "project": self.policy.project,
            "service": self.policy.service,
            "compose_sha256": record.get("compose_sha256"),
            "plan_sha256": record.get("plan_sha256"),
            "broker_verification_sha256": record.get("broker_verification_sha256"),
            "health": record.get("health"),
            "cleanup": record.get("cleanup"),
            "audit_id": record.get("audit_id"),
            "primary_error": record.get("primary_error"),
        }

    def _revocation_result(self, record: Dict[str, Any], *, replay: bool = False) -> Dict[str, Any]:
        return {
            "status": "pass",
            "state": record.get("state"),
            "replay": replay,
            "execution_id": record.get("execution_id"),
            "template_id": record.get("template_id"),
            "project": self.policy.project,
            "service": self.policy.service,
            "plan_sha256": record.get("plan_sha256"),
            "audit_id": record.get("audit_id"),
            "error": None,
        }
