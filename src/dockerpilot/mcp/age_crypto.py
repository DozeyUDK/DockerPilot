from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class AgeUnavailable(RuntimeError):
    pass


class AgeError(RuntimeError):
    pass


def _which_age() -> str | None:
    return shutil.which("age")


def _run_age(args: list[str]) -> None:
    try:
        proc = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AgeUnavailable("age binary not found in PATH.") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        msg = stderr or stdout or f"age failed with exit code {proc.returncode}"
        raise AgeError(msg)


def _parse_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


@dataclass(frozen=True)
class AgeSettings:
    recipients: list[str]
    identity_file: Optional[Path]

    @classmethod
    def from_env(cls, environ: Optional[dict[str, str]] = None) -> "AgeSettings":
        env = os.environ if environ is None else environ
        recips = _parse_csv(env.get("DOCKERPILOT_MCP_MIGRATION_AGE_RECIPIENTS"))
        ident_raw = (env.get("DOCKERPILOT_MCP_MIGRATION_AGE_IDENTITY_FILE") or "").strip()
        ident = Path(ident_raw).expanduser() if ident_raw else None
        return cls(recipients=recips, identity_file=ident)


def encrypt_file_age(*, in_path: Path, out_path: Path, settings: AgeSettings) -> dict:
    if _which_age() is None:
        raise AgeUnavailable("age binary not found in PATH. Install 'age' to use age encryption.")
    if not settings.recipients:
        raise AgeError("Missing recipients: set DOCKERPILOT_MCP_MIGRATION_AGE_RECIPIENTS.")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args = ["age"]
    for r in settings.recipients:
        args += ["-r", r]
    args += ["-o", str(out_path), str(in_path)]
    _run_age(args)
    return {"encrypted_path": str(out_path), "recipients": list(settings.recipients)}


def decrypt_file_age(*, in_path: Path, out_path: Path, settings: AgeSettings) -> dict:
    if _which_age() is None:
        raise AgeUnavailable("age binary not found in PATH. Install 'age' to decrypt age bundles.")
    if settings.identity_file is None:
        raise AgeError("Missing identity file: set DOCKERPILOT_MCP_MIGRATION_AGE_IDENTITY_FILE.")
    if not settings.identity_file.exists():
        raise AgeError(f"Identity file not found: {settings.identity_file}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args = ["age", "-d", "-i", str(settings.identity_file), "-o", str(out_path), str(in_path)]
    _run_age(args)
    return {"decrypted_path": str(out_path), "identity_file": str(settings.identity_file)}

