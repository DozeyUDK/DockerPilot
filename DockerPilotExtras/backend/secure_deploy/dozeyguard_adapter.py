"""Trusted Dozeyguard subprocess adapter (fixed argv, shell=False)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dockerpilot.secure_deploy.canonical import sha256_hex, sha256_canonical

from .errors import ScannerError, ValidationFailedError

DEFAULT_MAX_INPUT = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_OUTPUT = 2 * 1024 * 1024
CONTROLLED_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass
class DozeyguardConfig:
    executable: str
    policy_path: str
    fail_on: str = "high"
    max_input_bytes: int = DEFAULT_MAX_INPUT
    timeout_seconds: int = DEFAULT_TIMEOUT
    max_output_bytes: int = DEFAULT_MAX_OUTPUT


def resolve_dozeyguard_config(
    *,
    executable: Optional[str] = None,
    policy_path: Optional[str] = None,
) -> DozeyguardConfig:
    """Resolve broker/Extras scanner paths.

    Runtime must use an explicit binary via ``executable`` / ``DOZEYGUARD_BIN`` /
    ``PATH`` — never a monorepo ``components/.../target`` path.
    """
    exe = executable or os.environ.get("DOZEYGUARD_BIN") or shutil.which("dozeyguard")
    policy = policy_path or os.environ.get("DOZEYGUARD_POLICY_PATH")
    if not policy:
        policy = str(Path(__file__).resolve().parent / "policy" / "preview.toml")
    if not exe or not Path(exe).is_file():
        raise ScannerError("dozeyguard executable not configured", code="dozeyguard_missing")
    if not Path(policy).is_file():
        raise ScannerError("dozeyguard policy not configured", code="dozeyguard_policy_missing")
    # Refuse request-controlled paths by requiring absolute trusted paths only.
    exe_path = Path(exe).resolve()
    policy_file = Path(policy).resolve()
    if exe_path.is_symlink() or policy_file.is_symlink():
        raise ScannerError("symlink executable/policy rejected", code="dozeyguard_symlink")
    # Fail closed if someone points DOZEYGUARD_BIN at an in-tree cargo target.
    parts = {p.lower() for p in exe_path.parts}
    if "components" in parts and "dozeyguard" in parts and "target" in parts:
        raise ScannerError(
            "dozeyguard runtime must not use components/dozeyguard/target; "
            "set DOZEYGUARD_BIN to an installed/staging binary",
            code="dozeyguard_dev_path",
        )
    return DozeyguardConfig(executable=str(exe_path), policy_path=str(policy_file))


def run_dozeyguard_scan(compose_obj: Dict[str, Any], config: DozeyguardConfig) -> Dict[str, Any]:
    """Run Dozeyguard against Compose JSON via stdin."""
    payload = json.dumps(compose_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > config.max_input_bytes:
        raise ValidationFailedError("compose input exceeds max-input-bytes")

    expected_input_sha = sha256_hex(payload)
    argv = [
        config.executable,
        "scan",
        "--input",
        "-",
        "--input-format",
        "compose-json",
        "--policy",
        config.policy_path,
        "--output",
        "json",
        "--fail-on",
        config.fail_on,
        "--max-input-bytes",
        str(config.max_input_bytes),
    ]

    env = {
        "PATH": CONTROLLED_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }

    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=config.timeout_seconds,
            shell=False,
            close_fds=True,
            env=env,
            cwd="/",
        )
    except subprocess.TimeoutExpired as exc:
        raise ScannerError("dozeyguard timed out", code="dozeyguard_timeout") from exc
    except OSError as exc:
        raise ScannerError(f"failed to execute dozeyguard: {exc}", code="dozeyguard_exec") from exc

    if len(completed.stdout) > config.max_output_bytes or len(completed.stderr) > config.max_output_bytes:
        raise ScannerError("dozeyguard output exceeds limit", code="dozeyguard_output_limit")

    stderr_text = completed.stderr.decode("utf-8", errors="replace")
    stdout_text = completed.stdout.decode("utf-8", errors="replace")
    for sentinel in ("SECRET_DO_NOT_PRINT", "SUPER_SECRET_SENTINEL"):
        if sentinel in stderr_text or sentinel in stdout_text:
            raise ScannerError("secret sentinel detected in scanner output", code="dozeyguard_secret_leak")

    if completed.returncode == 1:
        # Prefer JSON error envelope when present.
        try:
            report = _parse_json_strict(stdout_text)
        except ScannerError:
            raise ScannerError("dozeyguard scanner error", code="dozeyguard_error") from None
        raise ScannerError(
            (report.get("result") or {}).get("error", {}).get("message") or "dozeyguard scanner error",
            code="dozeyguard_error",
        )

    report = _parse_json_strict(stdout_text)
    _validate_report(report, expected_input_sha=expected_input_sha, returncode=completed.returncode)
    return report


def _parse_json_strict(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ScannerError("empty dozeyguard stdout", code="dozeyguard_invalid_json")
    decoder = json.JSONDecoder()
    try:
        obj, index = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise ScannerError("invalid dozeyguard JSON", code="dozeyguard_invalid_json") from exc
    trailing = stripped[index:].strip()
    if trailing:
        raise ScannerError("trailing garbage after dozeyguard JSON", code="dozeyguard_invalid_json")
    if not isinstance(obj, dict):
        raise ScannerError("dozeyguard JSON must be an object", code="dozeyguard_invalid_json")
    return obj


def _validate_report(report: Dict[str, Any], *, expected_input_sha: str, returncode: int) -> None:
    if report.get("contract_version") != 1:
        raise ScannerError("unsupported dozeyguard contract_version", code="dozeyguard_contract")
    scanner = report.get("scanner") or {}
    if scanner.get("name") != "dozeyguard":
        raise ScannerError("unexpected scanner name", code="dozeyguard_contract")
    input_info = report.get("input") or {}
    if input_info.get("format") != "compose-json":
        raise ScannerError("unexpected input format", code="dozeyguard_contract")
    if input_info.get("sha256") != expected_input_sha:
        raise ScannerError("input hash mismatch", code="dozeyguard_hash_mismatch")

    result = report.get("result") or {}
    if result.get("exit_code") != returncode:
        raise ScannerError("exit/result mismatch", code="dozeyguard_exit_mismatch")
    if returncode not in (0, 2):
        raise ScannerError("unexpected dozeyguard exit code", code="dozeyguard_exit_mismatch")

    reported_sha = result.get("result_sha256")
    if not isinstance(reported_sha, str) or len(reported_sha) != 64:
        raise ScannerError("missing result_sha256", code="dozeyguard_hash_mismatch")

    # Recompute using same logical payload as contract (exclude result_sha256 and error).
    hash_payload = {
        "contract_version": report.get("contract_version"),
        "scanner": report.get("scanner"),
        "input": report.get("input"),
        "policy": report.get("policy"),
        "summary": report.get("summary"),
        "findings": report.get("findings"),
        "result": {
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
        },
    }
    computed = sha256_canonical(hash_payload)
    if computed != reported_sha:
        # Dozeyguard hashes via its own canonicalize; accept process integrity via
        # exit/input checks when recomputation differs due to key ordering of nested maps.
        # Still require presence and hex shape; mismatch against recomputed digest is soft-warn
        # only when findings empty and exit 0 — for preview we fail-closed on mismatch.
        raise ScannerError("result hash mismatch", code="dozeyguard_hash_mismatch")
