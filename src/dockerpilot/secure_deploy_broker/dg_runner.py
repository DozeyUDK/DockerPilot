"""Broker-owned Dozeyguard subprocess runner (fixed argv, shell=False)."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict

from dockerpilot.secure_deploy.canonical import sha256_canonical, sha256_hex

from .errors import VerificationError
from .verifier import BrokerDozeyguardConfig


def run_broker_dozeyguard(compose_obj: Dict[str, Any], config: BrokerDozeyguardConfig) -> Dict[str, Any]:
    payload = json.dumps(compose_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
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
        "high",
        "--max-input-bytes",
        str(2 * 1024 * 1024),
    ]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=15,
            shell=False,
            close_fds=True,
            env=env,
            cwd="/",
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("dozeyguard_timeout", "broker Dozeyguard timed out") from exc
    except OSError as exc:
        raise VerificationError("dozeyguard_exec", f"broker Dozeyguard exec failed: {exc}") from exc

    if completed.returncode == 1:
        raise VerificationError("dozeyguard_error", "broker Dozeyguard scanner error")

    text = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerificationError("dozeyguard_invalid_json", "invalid broker Dozeyguard JSON") from exc
    if not isinstance(report, dict):
        raise VerificationError("dozeyguard_invalid_json", "broker Dozeyguard JSON must be object")
    if report.get("contract_version") != 1:
        raise VerificationError("scanner_contract", "unsupported contract_version")
    if (report.get("scanner") or {}).get("name") != "dozeyguard":
        raise VerificationError("scanner_contract", "unexpected scanner name")
    input_info = report.get("input") or {}
    if input_info.get("sha256") != expected_input_sha:
        raise VerificationError("input_hash_mismatch", "broker input hash mismatch")
    result = report.get("result") or {}
    if result.get("exit_code") != completed.returncode:
        raise VerificationError("exit_mismatch", "exit/result mismatch")
    if completed.returncode not in (0, 2):
        raise VerificationError("exit_mismatch", "unexpected exit code")

    # Recompute result hash the same way as the extras adapter.
    hash_payload = {
        "contract_version": report.get("contract_version"),
        "scanner": report.get("scanner"),
        "input": report.get("input"),
        "policy": report.get("policy"),
        "summary": report.get("summary"),
        "findings": report.get("findings"),
        "result": {"status": result.get("status"), "exit_code": result.get("exit_code")},
    }
    computed = sha256_canonical(hash_payload)
    if result.get("result_sha256") != computed:
        raise VerificationError("result_hash_mismatch", "broker result hash mismatch")
    return report
