"""Strict SSH host-key verification helpers for DockerPilot Extras."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_known_hosts_lock = threading.RLock()


class SSHHostKeyError(RuntimeError):
    """Base class for SSH host-key verification failures."""


class SSHHostKeyRequired(SSHHostKeyError):
    def __init__(self, hostname: str, port: int, fingerprint: str, key_type: str):
        self.hostname = hostname
        self.port = port
        self.fingerprint = fingerprint
        self.key_type = key_type
        super().__init__(
            f"SSH host key is not trusted for {hostname}:{port}; verify fingerprint {fingerprint}"
        )


class SSHHostKeyMismatch(SSHHostKeyError):
    pass


def key_fingerprint_sha256(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def normalize_fingerprint(value: str | None) -> str:
    return (value or "").strip()


def known_host_name(hostname: str, port: int) -> str:
    return hostname if int(port) == 22 else f"[{hostname}]:{int(port)}"


def probe_host_key(hostname: str, port: int = 22, *, timeout: float = 10.0):
    import paramiko

    sock = socket.create_connection((hostname, int(port)), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        if key is None:
            raise SSHHostKeyError(f"SSH server {hostname}:{port} did not provide a host key")
        return key
    finally:
        transport.close()
        try:
            sock.close()
        except OSError:
            pass


def _load_known_hosts(path: Path):
    import paramiko

    host_keys = paramiko.HostKeys()
    if path.exists():
        host_keys.load(str(path))
    return host_keys


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync so an atomic rename is durable on POSIX."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def _known_hosts_update_lock(path: Path):
    """Serialize managed known_hosts read/modify/write across threads and workers."""
    with _known_hosts_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        with open(lock_path, "a+b") as lock_file:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass

            if os.name == "posix":
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                return

            if os.name == "nt":  # pragma: no cover - exercised by Windows CI/runtime
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                return

            # Unknown platform: retain the in-process lock rather than silently
            # dropping all serialization.
            yield


def _save_known_host_key(host_keys, path: Path, host_name: str, key) -> None:
    """Persist a verified key via same-directory temp file + atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    host_keys.add(host_name, key.get_name(), key)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        host_keys.save(str(temp_path))
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass

        # Ensure the completed temp file reaches disk before publication.
        with open(temp_path, "rb") as handle:
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_host_key_trusted(
    server_config: dict,
    *,
    known_hosts_path: str | Path,
    timeout: float = 10.0,
):
    """Verify the current SSH host key against an explicit pin or managed known_hosts.

    An explicit ``host_key_fingerprint`` is authoritative. If it matches the
    observed key, managed known_hosts is synchronized to that key, which allows
    an operator-approved key rotation without manual file edits. Without an
    explicit pin, an existing known_hosts entry must match exactly; unknown or
    changed keys fail closed.
    """
    hostname = str(server_config.get("hostname") or "").strip()
    port = int(server_config.get("port", 22))
    if not hostname:
        raise SSHHostKeyError("Missing SSH hostname")

    key = probe_host_key(hostname, port, timeout=timeout)
    fingerprint = key_fingerprint_sha256(key)
    host_name = known_host_name(hostname, port)
    path = Path(known_hosts_path)
    pinned = normalize_fingerprint(server_config.get("host_key_fingerprint"))

    # Serialize the complete read/compare/update cycle. The file lock prevents
    # separate WSGI workers from publishing stale snapshots over each other;
    # the atomic writer below prevents readers from ever seeing a partial file.
    with _known_hosts_update_lock(path):
        host_keys = _load_known_hosts(path)
        existing = host_keys.lookup(host_name) or {}
        known_key = existing.get(key.get_name())

        # An explicit operator pin is the source of truth. This both prevents a
        # stale known_hosts entry from overriding a new pin and provides a safe,
        # deliberate path for host-key rotation.
        if pinned:
            if not hmac.compare_digest(pinned, fingerprint):
                raise SSHHostKeyMismatch(
                    f"SSH host fingerprint mismatch for {hostname}:{port}: expected {pinned}, observed {fingerprint}"
                )
            if known_key is None or not hmac.compare_digest(known_key.asbytes(), key.asbytes()):
                _save_known_host_key(host_keys, path, host_name, key)
            return key

        if known_key is not None:
            if hmac.compare_digest(known_key.asbytes(), key.asbytes()):
                return key
            raise SSHHostKeyMismatch(
                f"SSH host key changed for {hostname}:{port}; observed {fingerprint}"
            )

    raise SSHHostKeyRequired(hostname, port, fingerprint, key.get_name())


def create_verified_ssh_client(server_config: dict, *, known_hosts_path: str | Path, timeout: float = 10.0):
    """Return an SSHClient restricted to exactly the host key verified above."""
    import paramiko

    verified_key = ensure_host_key_trusted(
        server_config,
        known_hosts_path=known_hosts_path,
        timeout=timeout,
    )
    hostname = str(server_config.get("hostname") or "").strip()
    port = int(server_config.get("port", 22))
    host_name = known_host_name(hostname, port)

    client = paramiko.SSHClient()
    # Do not load system or managed known_hosts into the connection client.
    # ensure_host_key_trusted() has already selected one exact key; loading any
    # broader key set here could let negotiation accept a different stale key.
    client.get_host_keys().add(host_name, verified_key.get_name(), verified_key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client
