#!/usr/bin/env python3
"""Install-time helpers for root broker canary (no Docker / firewall / secrets)."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


EXTRAS_USER = "dockerpilot-extras"
BROKER_GROUP = "dockerpilot-secure-broker"
RUN_DIR = Path("/run/dockerpilot-secure-broker")
RUNTIME_CONFIG_DEST = Path("/etc/dockerpilot-secure-broker/config.json")
BACKUP_MANIFEST_MARKER = "install-manifest.sha256"
CANARY_OPERATIONS = {"admit_canary_execution", "revoke_canary_admission", "deploy_canary", "remove_canary"}
PLACEHOLDER_DIGEST = "sha256:" + ("a" * 64)
_DIGEST_IMAGE_RE = re.compile(r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")
_MARKER_MAX_BYTES = 128


def resolve_install_expect_user(
    env: Optional[Mapping[str, str]] = None,
    *,
    override: Optional[str] = None,
) -> str:
    """Resolve the non-root operator expected to invoke the install via sudo.

    Order: explicit override → ``DOCKERPILOT_INSTALL_EXPECT_USER`` → ``SUDO_USER``.
    Never accepts ``root``. Fail-closed if unresolved.
    """
    environ = env if env is not None else os.environ
    candidate = override
    if candidate is None or candidate == "":
        candidate = environ.get("DOCKERPILOT_INSTALL_EXPECT_USER") or None
    if candidate is None or candidate == "":
        candidate = environ.get("SUDO_USER") or None
    if not candidate:
        raise ValueError(
            "cannot resolve non-root install operator; set DOCKERPILOT_INSTALL_EXPECT_USER "
            "or run via sudo so SUDO_USER is set"
        )
    if candidate == "root":
        raise ValueError("install operator must not be root")
    return candidate


def _read_manifest_marker_nofollow(marker: Path) -> str:
    """Read a small regular marker without ever following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker, flags)
    except OSError as exc:
        raise ValueError(f"backup manifest marker is not a safe regular file: {marker}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"backup manifest marker is not a regular file: {marker}")
        if st.st_size > _MARKER_MAX_BYTES:
            raise ValueError(f"backup manifest marker is oversized: {marker}")
        raw = os.read(fd, _MARKER_MAX_BYTES + 1)
        if len(raw) > _MARKER_MAX_BYTES:
            raise ValueError(f"backup manifest marker is oversized: {marker}")
        try:
            return raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(f"backup manifest marker is not ASCII: {marker}") from exc
    finally:
        os.close(fd)


def assert_backup_dir_compatible(backup_dir: Path, manifest_hash: str) -> None:
    """Refuse to mutate a backup dir bound to a different install manifest.

    Empty / missing dirs are OK. Existing dirs without a marker fail closed.
    Same hash → idempotent (OK). Marker reads never follow symlinks and never
    echo foreign marker contents into errors.
    """
    if not manifest_hash or len(manifest_hash) < 32:
        raise ValueError("manifest_hash invalid")
    if not backup_dir.exists():
        return
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise ValueError(f"backup path must be a directory: {backup_dir}")
    children = list(backup_dir.iterdir())
    if not children:
        return
    marker = backup_dir / BACKUP_MANIFEST_MARKER
    if not marker.exists() and not marker.is_symlink():
        raise ValueError(
            f"backup dir {backup_dir} exists without {BACKUP_MANIFEST_MARKER}; "
            "refuse to mutate (foreign or pre-binding backup)"
        )
    existing = _read_manifest_marker_nofollow(marker)
    if existing != manifest_hash:
        raise ValueError("backup dir belongs to a different install manifest")


def write_backup_manifest_marker(backup_dir: Path, manifest_hash: str) -> None:
    """Record the install manifest hash for this backup directory safely."""
    assert_backup_dir_compatible(backup_dir, manifest_hash)
    backup_dir.mkdir(parents=True, exist_ok=True)
    marker = backup_dir / BACKUP_MANIFEST_MARKER

    # Existing compatible marker is already correct; avoid reopening it for write.
    if marker.exists() or marker.is_symlink():
        existing = _read_manifest_marker_nofollow(marker)
        if existing != manifest_hash:
            raise ValueError("backup dir belongs to a different install manifest")
        return

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(marker, flags, 0o644)
    except OSError as exc:
        raise ValueError(f"refusing unsafe backup manifest marker creation: {marker}") from exc
    try:
        payload = (manifest_hash.strip() + "\n").encode("ascii")
        written = 0
        while written < len(payload):
            count = os.write(fd, payload[written:])
            if count <= 0:
                raise OSError("short write while creating backup manifest marker")
            written += count
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(marker, 0o644, follow_symlinks=False)


def materialize_runtime_config(template: Mapping[str, Any], *, peer_uid: int) -> Dict[str, Any]:
    """Build root-owned runtime config from install template.

    Template must declare expected_peer_user (name only). Output has numeric
    expected_peer_uid only — never embeds expected_peer_user.
    """
    if peer_uid < 0:
        raise ValueError("peer_uid must be >= 0")
    if peer_uid == 0:
        raise ValueError("peer_uid must not be root (0)")
    data = dict(template)
    peer_user = data.pop("expected_peer_user", None)
    if peer_user != EXTRAS_USER:
        raise ValueError(f"expected_peer_user must be {EXTRAS_USER!r}, got {peer_user!r}")
    if "expected_peer_uid" in data and data["expected_peer_uid"] is not None:
        raise ValueError("config template must not set expected_peer_uid")
    # Refuse accidental hardcoding of the current host's extras UID in templates.
    raw = json.dumps(template, sort_keys=True)
    if '"expected_peer_uid"' in raw:
        raise ValueError("config template must not contain expected_peer_uid")
    allowed_ops = set(data.get("allowed_operations") or [])
    if allowed_ops & CANARY_OPERATIONS:
        try:
            ttl = int(data.get("canary_staged_bundle_ttl_seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("canary_staged_bundle_ttl_seconds must be set for canary operations") from exc
        if ttl <= 0 or ttl > 600:
            raise ValueError("canary_staged_bundle_ttl_seconds out of range")
        data["canary_staged_bundle_ttl_seconds"] = ttl
        image = os.environ.get("DOCKERPILOT_CANARY_IMAGE") or str(data.get("canary_image") or "")
        if not image or image.startswith("REPLACE_"):
            raise ValueError("DOCKERPILOT_CANARY_IMAGE must be set for canary operations")
        if not _DIGEST_IMAGE_RE.fullmatch(image):
            raise ValueError("DOCKERPILOT_CANARY_IMAGE must be a digest-only image reference")
        if image.endswith("@" + PLACEHOLDER_DIGEST):
            raise ValueError("DOCKERPILOT_CANARY_IMAGE must not use the placeholder digest")
        data["canary_image"] = image
    data["expected_peer_uid"] = int(peer_uid)
    return data


def ensure_runtime_dir(
    run_dir: Path,
    *,
    broker_gid: int,
    owner_uid: int = 0,
    fix: bool = True,
) -> None:
    """Ensure run_dir is owner_uid:broker_gid mode 0750.

    Callers must not mkdir this path under umask 077 without an immediate chmod:
    a pre-created 0700 directory prevents the socket group from connecting.
    If the directory is absent, leave it for systemd DirectoryMode=0750.
    If present with wrong owner/mode: fix when fix=True, else raise.
    """
    if broker_gid < 0:
        raise ValueError("broker_gid invalid")
    if owner_uid < 0:
        raise ValueError("owner_uid invalid")
    if run_dir.exists() or run_dir.is_symlink():
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise RuntimeError(f"runtime path must be a directory: {run_dir}")
        st = run_dir.stat()
        mode = stat.S_IMODE(st.st_mode)
        ok = st.st_uid == owner_uid and st.st_gid == broker_gid and mode == 0o750
        if ok:
            return
        if not fix:
            raise RuntimeError(
                f"{run_dir} has uid={st.st_uid} gid={st.st_gid} mode={oct(mode)}; "
                f"want {owner_uid}:{broker_gid} 0o750"
            )
        os.chown(run_dir, owner_uid, broker_gid)
        os.chmod(run_dir, 0o750)
        st2 = run_dir.stat()
        mode2 = stat.S_IMODE(st2.st_mode)
        if st2.st_uid != owner_uid or st2.st_gid != broker_gid or mode2 != 0o750:
            raise RuntimeError(
                f"failed to repair {run_dir} to {owner_uid}:{broker_gid} 0750"
            )


def assert_socket_mode(sock_path: Path, *, broker_gid: int) -> None:
    if not sock_path.exists() or sock_path.is_symlink():
        raise RuntimeError(f"socket missing/invalid: {sock_path}")
    st = sock_path.stat()
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != 0 or st.st_gid != broker_gid or mode != 0o660:
        raise RuntimeError(
            f"{sock_path} has uid={st.st_uid} gid={st.st_gid} mode={oct(mode)}; "
            f"want root:{broker_gid} 0o660"
        )
