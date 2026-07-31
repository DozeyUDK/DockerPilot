#!/usr/bin/env python3
"""Fake dozeyguard for Secure Deploy adapter tests (no Docker)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow importing dockerpilot from repo src
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from dockerpilot.secure_deploy.canonical import sha256_canonical, sha256_hex  # noqa: E402


def main() -> int:
    raw = sys.stdin.buffer.read()
    input_sha = sha256_hex(raw)
    mode = "pass"
    if b"FORCE_FAIL" in raw:
        mode = "fail"
    if b"FORCE_ERROR" in raw:
        report = {
            "contract_version": 1,
            "scanner": {"name": "dozeyguard", "version": "0.0.0-fake"},
            "input": {"format": "compose-json", "sha256": input_sha, "bytes": len(raw)},
            "policy": {"sha256": "0" * 64, "exceptions_applied": []},
            "summary": {"services": 0, "findings": 0, "blocking": 0, "warnings": 0, "info": 0},
            "findings": [],
            "result": {
                "status": "error",
                "exit_code": 1,
                "error": {"code": "parse_error", "message": "forced error"},
            },
        }
        # no result_sha256 required for error path consumed as scanner error
        print(json.dumps(report))
        return 1

    findings = []
    blocking = 0
    warnings = 0
    exit_code = 0
    status = "pass"
    if mode == "fail":
        findings = [
            {
                "rule_id": "DG001",
                "severity": "critical",
                "blocking": True,
                "service": "web",
                "path": "privileged",
                "message": "forced finding",
                "remediation": "remove privileged",
                "exception": None,
            }
        ]
        blocking = 1
        exit_code = 2
        status = "fail"

    payload = {
        "contract_version": 1,
        "scanner": {"name": "dozeyguard", "version": "0.0.0-fake"},
        "input": {"format": "compose-json", "sha256": input_sha, "bytes": len(raw)},
        "policy": {"sha256": "0" * 64, "exceptions_applied": []},
        "summary": {
            "services": 1,
            "findings": len(findings),
            "blocking": blocking,
            "warnings": warnings,
            "info": 0,
        },
        "findings": findings,
        "result": {"status": status, "exit_code": exit_code},
    }
    result_sha = sha256_canonical(payload)
    payload["result"]["result_sha256"] = result_sha
    print(json.dumps(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
