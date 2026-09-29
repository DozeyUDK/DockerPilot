"""Integrity checks for broker-owned Dozeyguard binary and policy."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import BrokerError


@dataclass
class TrustedArtifact:
    """Verified open artifact pinned to one inode for later consumption.

    The file descriptor remains open until ``close()`` / context-manager exit.
    Consumers should use ``proc_path`` rather than reopening ``original_path`` so
    an atomic pathname replacement after verification cannot change the bytes
    that are executed or read.
    """

    fd: int
    digest: str
    original_path: Path

    @property
    def proc_path(self) -> str:
        proc_fd_root = Path("/proc/self/fd")
        if os.name != "posix" or not proc_fd_root.is_dir():
            raise BrokerError(
                "artifact_fd_path_unavailable",
                "verified artifact fd path is unavailable on this platform",
            )
        return str(proc_fd_root / str(self.fd))

    def close(self) -> None:
        if self.fd < 0:
            return
        try:
            os.close(self.fd)
        finally:
            self.fd = -1

    def __enter__(self) -> "TrustedArtifact":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_fd(fd: int) -> str:
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return h.hexdigest()


def open_trusted_artifact(
    path: Path,
    *,
    expected_sha256: str,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
    require_executable: bool = False,
    deny_writable_uid: Optional[int] = None,
) -> TrustedArtifact:
    """Open, verify and pin one broker-owned artifact.

    Verification is performed against the already-open file descriptor. The
    returned descriptor therefore continues to reference the verified inode even
    if an attacker later renames or replaces the original pathname.
    """

    path = Path(path)
    if not path.is_absolute():
        raise BrokerError("artifact_path", f"absolute artifact path required: {path}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise BrokerError("artifact_missing", f"regular file required: {path}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BrokerError("artifact_symlink", f"symlink rejected: {path}") from exc
        raise BrokerError("artifact_open", f"unable to open trusted artifact: {path}") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise BrokerError("artifact_missing", f"regular file required: {path}")

        mode = st.st_mode & 0o777
        if mode & 0o022:
            raise BrokerError("artifact_writable", f"group/other write forbidden: {path}")
        if expected_uid is not None and st.st_uid != expected_uid:
            raise BrokerError("artifact_owner", f"unexpected owner uid for {path}")
        if expected_gid is not None and st.st_gid != expected_gid:
            raise BrokerError("artifact_group", f"unexpected owner gid for {path}")
        if deny_writable_uid is not None and st.st_uid == deny_writable_uid and (st.st_mode & 0o200):
            raise BrokerError("artifact_writable", f"writable by denied uid: {path}")
        if require_executable and not (st.st_mode & 0o111):
            raise BrokerError("artifact_exec", f"executable bit required: {path}")

        digest = _sha256_fd(fd)
        if digest != expected_sha256:
            raise BrokerError("artifact_hash", f"checksum mismatch: {path}")
        return TrustedArtifact(fd=fd, digest=digest, original_path=path)
    except Exception:
        os.close(fd)
        raise


def assert_trusted_artifact(
    path: Path,
    *,
    expected_sha256: str,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
    require_executable: bool = False,
    deny_writable_uid: Optional[int] = None,
) -> str:
    """Fail-closed integrity gate for binary/policy files.

    This compatibility helper verifies the artifact and closes the pinned file
    descriptor immediately. Security-sensitive execution should instead retain
    ``open_trusted_artifact()`` until the consumer has inherited the descriptor.
    """

    with open_trusted_artifact(
        path,
        expected_sha256=expected_sha256,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        require_executable=require_executable,
        deny_writable_uid=deny_writable_uid,
    ) as artifact:
        return artifact.digest
