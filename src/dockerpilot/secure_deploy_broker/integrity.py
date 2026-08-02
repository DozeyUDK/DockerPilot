"""Integrity checks for broker-owned Dozeyguard binary and policy."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from .errors import BrokerError


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_trusted_artifact(
    path: Path,
    *,
    expected_sha256: str,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
    require_executable: bool = False,
    deny_writable_uid: Optional[int] = None,
) -> str:
    """Fail-closed integrity gate for binary/policy files."""
    if path.is_symlink():
        raise BrokerError("artifact_symlink", f"symlink rejected: {path}")
    if not path.is_file():
        raise BrokerError("artifact_missing", f"regular file required: {path}")
    st = path.stat()
    mode = st.st_mode & 0o777
    if mode & 0o022:
        raise BrokerError("artifact_writable", f"group/other write forbidden: {path}")
    if expected_uid is not None and st.st_uid != expected_uid:
        raise BrokerError("artifact_owner", f"unexpected owner uid for {path}")
    if expected_gid is not None and st.st_gid != expected_gid:
        raise BrokerError("artifact_group", f"unexpected owner gid for {path}")
    if deny_writable_uid is not None and st.st_uid == deny_writable_uid and (st.st_mode & 0o200):
        raise BrokerError("artifact_writable", f"writable by denied uid: {path}")
    if require_executable and not os.access(path, os.X_OK):
        raise BrokerError("artifact_exec", f"executable bit required: {path}")
    digest = sha256_file(path)
    if digest != expected_sha256:
        raise BrokerError("artifact_hash", f"checksum mismatch: {path}")
    return digest
