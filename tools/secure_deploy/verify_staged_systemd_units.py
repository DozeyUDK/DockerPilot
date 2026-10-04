#!/usr/bin/env python3
"""Preflight systemd-analyze verify for root-broker canary staging (#11D.2B).

Rewrites ExecStart only in a temporary unit copy so a clean host (without the
installed /usr/libexec/.../broker) can still verify unit syntax/security. The
on-disk staged/installed unit files are never modified.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INSTALLED_BROKER = "/usr/libexec/dockerpilot-secure-broker/broker"
SERVICE_NAME = "dockerpilot-secure-broker.service"
SOCKET_NAME = "dockerpilot-secure-broker.socket"
APPROVER_SERVICE_NAME = "dockerpilot-secure-approver.service"
APPROVER_SOCKET_NAME = "dockerpilot-secure-approver.socket"


def rewrite_exec_start(service_text: str, staged_broker: Path) -> str:
    """Replace only the installed ExecStart binary path with the staged broker."""
    needle = f"ExecStart={INSTALLED_BROKER}"
    if needle not in service_text:
        raise SystemExit(
            f"service unit missing expected ExecStart={INSTALLED_BROKER} "
            "(refusing to rewrite an unexpected unit)"
        )
    replacement = f"ExecStart={staged_broker.resolve()}"
    # Exactly one ExecStart line targeting the installed broker path.
    count = service_text.count(needle)
    if count != 1:
        raise SystemExit(f"expected exactly one '{needle}', found {count}")
    return service_text.replace(needle, replacement, 1)


def verify_staged_units(
    *,
    service_src: Path,
    socket_src: Path,
    staged_broker: Path,
) -> subprocess.CompletedProcess[str]:
    """Copy units to a private tempdir, rewrite ExecStart, run systemd-analyze verify."""
    if not service_src.is_file() or service_src.is_symlink():
        raise SystemExit(f"service unit missing/invalid: {service_src}")
    if not socket_src.is_file() or socket_src.is_symlink():
        raise SystemExit(f"socket unit missing/invalid: {socket_src}")
    if not staged_broker.is_file() or staged_broker.is_symlink():
        raise SystemExit(f"staged broker missing/invalid: {staged_broker}")
    if not os.access(staged_broker, os.X_OK):
        raise SystemExit(f"staged broker is not executable: {staged_broker}")

    original = service_src.read_text(encoding="utf-8")
    if f"ExecStart={INSTALLED_BROKER}" not in original:
        raise SystemExit(
            f"refusing verify: source unit does not use ExecStart={INSTALLED_BROKER}"
        )

    rewritten = rewrite_exec_start(original, staged_broker)
    tmp: Path | None = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="dp-broker-systemd-verify-"))
        os.chmod(tmp, 0o700)
        service_dst = tmp / service_src.name
        socket_dst = tmp / socket_src.name
        service_dst.write_text(rewritten, encoding="utf-8")
        os.chmod(service_dst, 0o600)
        shutil.copy2(socket_src, socket_dst)
        os.chmod(socket_dst, 0o600)
        # Ensure we never left a staging path in the source tree.
        if INSTALLED_BROKER not in service_src.read_text(encoding="utf-8"):
            raise SystemExit("source service unit lost installed ExecStart during verify")
        return subprocess.run(
            ["systemd-analyze", "verify", str(service_dst), str(socket_dst)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if tmp is not None and tmp.exists():
            # Controlled cleanup of our private directory only (no globs / rm -rf shell).
            for path in sorted(tmp.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            tmp.rmdir()


def verify_from_staging(staging: Path) -> int:
    bundle = staging / "bundle"
    broker = bundle / "usr/libexec/dockerpilot-secure-broker/broker"
    pairs = (
        (SERVICE_NAME, SOCKET_NAME),
        (APPROVER_SERVICE_NAME, APPROVER_SOCKET_NAME),
    )
    for service_name, socket_name in pairs:
        service = bundle / "etc/systemd/system" / service_name
        socket_unit = bundle / "etc/systemd/system" / socket_name
        proc = verify_staged_units(
            service_src=service,
            socket_src=socket_unit,
            staged_broker=broker,
        )
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        if proc.returncode != 0:
            print(
                f"ERROR: systemd-analyze verify failed for {service_name} (exit {proc.returncode})",
                file=sys.stderr,
            )
            return proc.returncode
        text = service.read_text(encoding="utf-8")
        if f"ExecStart={INSTALLED_BROKER}" not in text:
            print(f"ERROR: staged service ExecStart was mutated: {service_name}", file=sys.stderr)
            return 2
        for line in text.splitlines():
            if line.startswith("ExecStart=") and str(staging) in line:
                print(f"ERROR: staged service ExecStart points at staging: {service_name}", file=sys.stderr)
                return 2
    print("systemd-analyze verify (broker + approver staged ExecStart rewrite): OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "staging",
        type=Path,
        help="Path to .staging/11d2a (contains bundle/)",
    )
    args = parser.parse_args(argv)
    staging = args.staging.resolve()
    if not staging.is_dir():
        print(f"ERROR: staging dir missing: {staging}", file=sys.stderr)
        return 1
    return verify_from_staging(staging)


if __name__ == "__main__":
    raise SystemExit(main())
