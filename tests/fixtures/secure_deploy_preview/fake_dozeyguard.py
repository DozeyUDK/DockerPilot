#!/usr/bin/env python3
"""Fake dozeyguard for Secure Deploy adapter tests (no Docker)."""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


def _canonicalize(obj: Any) -> Any:
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("non-finite floats are not allowed in canonical JSON")
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return {key: _canonicalize(obj[key]) for key in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(item) for item in obj]
    raise TypeError(f"unsupported type for canonical JSON: {type(obj)!r}")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_canonical(obj: Any) -> str:
    canonical = _canonicalize(obj)
    raw = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_hex(raw)


def main() -> int:
    raw = sys.stdin.buffer.read()
    input_sha = _sha256_hex(raw)
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
    result_sha = _sha256_canonical(payload)
    payload["result"]["result_sha256"] = result_sha
    print(json.dumps(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
