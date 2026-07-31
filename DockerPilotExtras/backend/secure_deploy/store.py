"""File-backed immutable draft/plan store for Secure Deploy preview."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from .errors import NotFoundError, SecureDeployError, ValidationFailedError

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_OBJECT_BYTES = 2 * 1024 * 1024
DEFAULT_TTL_SECONDS = 30 * 60


class SecureDeployStore(Protocol):
    def save_draft(self, draft_id: str, payload: Dict[str, Any]) -> None: ...
    def get_draft(self, draft_id: str) -> Dict[str, Any]: ...
    def save_plan(self, plan_id: str, payload: Dict[str, Any]) -> None: ...
    def get_plan(self, plan_id: str) -> Dict[str, Any]: ...


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _validate_id(object_id: str) -> str:
    if not _ID_RE.match(object_id or ""):
        raise ValidationFailedError("invalid object id", code="invalid_id")
    return object_id


class FileSecureDeployStore:
    """Atomic JSON store under a dedicated root (0700 / 0600)."""

    def __init__(self, root: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.root = Path(root).resolve()
        self.ttl_seconds = ttl_seconds
        self.drafts_dir = self.root / "drafts"
        self.plans_dir = self.root / "plans"
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        for directory in (self.drafts_dir, self.plans_dir):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)

    def _path(self, kind: str, object_id: str) -> Path:
        object_id = _validate_id(object_id)
        base = self.drafts_dir if kind == "draft" else self.plans_dir
        base_resolved = base.resolve()
        candidate = base / f"{object_id}.json"
        if candidate.is_symlink() or candidate.parent.is_symlink():
            raise SecureDeployError("symlink_rejected", "symlink store paths are not allowed", 400)
        path = candidate.resolve()
        if not str(path).startswith(str(base_resolved) + os.sep):
            raise ValidationFailedError("path traversal rejected", code="path_traversal")
        if path.is_symlink():
            raise SecureDeployError("symlink_rejected", "symlink store paths are not allowed", 400)
        return path

    def _atomic_write(self, path: Path, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(raw) > MAX_OBJECT_BYTES:
            raise SecureDeployError("object_too_large", "Stored object exceeds size limit", 413)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(tmp), flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp), str(path))
            os.chmod(path, 0o600)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _read(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise NotFoundError("object not found")
        if path.is_symlink():
            raise SecureDeployError("symlink_rejected", "symlink store paths are not allowed", 400)
        data = json.loads(path.read_text(encoding="utf-8"))
        expires_at = data.get("store_expires_at_ts")
        if isinstance(expires_at, (int, float)) and time.time() > float(expires_at):
            raise NotFoundError("object expired")
        return data

    def save_draft(self, draft_id: str, payload: Dict[str, Any]) -> None:
        path = self._path("draft", draft_id)
        body = dict(payload)
        body["store_expires_at_ts"] = time.time() + self.ttl_seconds
        self._atomic_write(path, body)

    def get_draft(self, draft_id: str) -> Dict[str, Any]:
        return self._read(self._path("draft", draft_id))

    def save_plan(self, plan_id: str, payload: Dict[str, Any]) -> None:
        path = self._path("plan", plan_id)
        if path.exists():
            raise SecureDeployError("immutable_plan", "plans cannot be mutated", 409)
        body = dict(payload)
        body["store_expires_at_ts"] = time.time() + self.ttl_seconds
        self._atomic_write(path, body)

    def get_plan(self, plan_id: str) -> Dict[str, Any]:
        return self._read(self._path("plan", plan_id))
