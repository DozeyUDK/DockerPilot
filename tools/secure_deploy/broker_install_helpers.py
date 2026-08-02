#!/usr/bin/env python3
"""Install-time helpers for root broker canary (no Docker / firewall / secrets)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


EXTRAS_USER = "dockerpilot-extras"
BROKER_GROUP = "dockerpilot-secure-broker"
RUN_DIR = Path("/run/dockerpilot-secure-broker")
RUNTIME_CONFIG_DEST = Path("/etc/dockerpilot-secure-broker/config.json")
BACKUP_MANIFEST_MARKER = "install-manifest.sha256"


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


def assert_backup_dir_compatible(backup_dir: Path, manifest_hash: str) -> None:
    """Refuse to mutate a backup dir bound to a different install manifest.

    Empty / missing dirs are OK. Existing dirs without a marker fail closed.
    Same hash → idempotent (OK).
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
    if not marker.is_file():
        raise ValueError(
            f"backup dir {backup_dir} exists without {BACKUP_MANIFEST_MARKER}; "
            "refuse to mutate (foreign or pre-binding backup)"
        )
    existing = marker.read_text(encoding="utf-8").strip()
    if existing != manifest_hash:
        raise ValueError(
            f"backup dir belongs to a different install "
            f"(have {existing[:16]}… want {manifest_hash[:16]}…)"
        )


def write_backup_manifest_marker(backup_dir: Path, manifest_hash: str) -> None:
    """Record the install manifest hash for this backup directory."""
    assert_backup_dir_compatible(backup_dir, manifest_hash)
    backup_dir.mkdir(parents=True, exist_ok=True)
    marker = backup_dir / BACKUP_MANIFEST_MARKER
    if marker.exists() and marker.is_symlink():
        raise ValueError(f"refusing symlink marker: {marker}")
    marker.write_text(manifest_hash.strip() + "\n", encoding="utf-8")
    os.chmod(marker, 0o644)


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
