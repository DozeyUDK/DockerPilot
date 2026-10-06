#!/usr/bin/env python3
"""Root broker dry_run canary for broker-owned approval provenance.

Phase 1 (run as dockerpilot-extras) creates a non-authorizing challenge.
A trusted operator approves it through the root-only approver socket.
Phase 2 reruns this tool with --approval-id and proves dry_run authorization is
resolved from broker-owned state rather than client-supplied approval JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from broker_canary_client import (  # noqa: E402
    DEFAULT_CLIENT_VERSION,
    DEFAULT_SOCKET,
    call,
    err_code,
)

DEFAULT_PLAN = Path("/tmp/dockerpilot-verify-plan-canary.json")
DEFAULT_OUT = Path("/tmp/broker_dry_run_canary_results.json")
DEFAULT_APPROVE = "/usr/libexec/dockerpilot-secure-broker/approve"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Root broker dry_run provenance canary")
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="DeploymentPlan JSON path")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Results JSON path")
    p.add_argument("--socket", default=DEFAULT_SOCKET, help="Broker control Unix socket path")
    p.add_argument("--client-version", default=DEFAULT_CLIENT_VERSION, help="client.version field")
    p.add_argument(
        "--approval-id",
        default=None,
        help="Broker-owned approval ID returned/reserved by the challenge from phase 1",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("WHOAMI", os.getuid(), os.getgid(), flush=True)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    plan_mtime_before = args.plan.stat().st_mtime_ns
    plan_sha_before = plan["plan_sha256"]

    def _call(operation: str, **kwargs):
        return call(
            operation,
            socket_path=args.socket,
            client_version=args.client_version,
            **kwargs,
        )

    if not args.approval_id:
        response = _call("create_approval_challenge", plan=plan)
        challenge = response.get("approval_challenge") or {}
        results = {
            "phase": "challenge",
            "whoami": {"uid": os.getuid(), "gid": os.getgid()},
            "response": response,
            "challenge": challenge,
            "plan_id": plan.get("plan_id"),
            "plan_sha256": plan.get("plan_sha256"),
        }
        args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if response.get("ok") and challenge.get("challenge_id") and challenge.get("approval_id"):
            command = (
                f"sudo {DEFAULT_APPROVE} "
                f"--challenge {challenge['challenge_id']} "
                f"--plan-sha256 {plan['plan_sha256']}"
            )
            print("CHALLENGE", json.dumps(challenge, sort_keys=True), flush=True)
            print("APPROVE_WITH", command, flush=True)
            print(
                "THEN_RERUN",
                f"{sys.argv[0]} --plan {args.plan} --output {args.output} "
                f"--socket {args.socket} --approval-id {challenge['approval_id']}",
                flush=True,
            )
            return 0
        return 2

    positive = _call("dry_run", plan=plan, approval_id=args.approval_id)
    replay = _call("dry_run", plan=plan, approval_id=args.approval_id)
    negatives: Dict[str, Any] = {
        "missing_approval_id": _call("dry_run", plan=plan),
        "fabricated_approval_id": _call(
            "dry_run",
            plan=plan,
            approval_id="appr_" + ("f" * 24),
        ),
    }

    # An Extras-compromised caller may still put arbitrary JSON on the wire, but
    # the protocol must reject it as non-authoritative.
    forged_v1 = {
        "approval_version": 1,
        "approval_id": args.approval_id,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "actor": "forged",
        "nonce": "forgednonce123456",
        "issued_at": plan["created_at"],
        "approved_at": plan["created_at"],
        "expires_at": plan["expires_at"],
        "session_id_hash": "a" * 64,
        "status": "approved",
    }
    negatives["fabricated_approval_object"] = _call(
        "dry_run",
        plan=plan,
        approval_id=args.approval_id,
        approval=forged_v1,
    )

    plan_mtime_after = args.plan.stat().st_mtime_ns
    plan_after = json.loads(args.plan.read_text(encoding="utf-8"))
    verification = positive.get("verification") or {}
    results = {
        "phase": "authorized_dry_run",
        "whoami": {"uid": os.getuid(), "gid": os.getgid()},
        "approval_id": args.approval_id,
        "positive": positive,
        "replay": replay,
        "negatives": negatives,
        "plan_file_mtime_unchanged": plan_mtime_before == plan_mtime_after,
        "plan_sha256_unchanged": plan_sha_before == plan_after.get("plan_sha256"),
        "approval_authority": "broker_owned_external_approver",
    }
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("NEGATIVES_SUMMARY", flush=True)
    for name, resp in negatives.items():
        print(
            f"{name}\tok={resp.get('ok')}\tcode={err_code(resp)}\tmsg={(resp.get('error') or {}).get('message')}",
            flush=True,
        )
    print(
        "SUMMARY",
        json.dumps(
            {
                "positive_ok": positive.get("ok"),
                "status": verification.get("status"),
                "dry_run": verification.get("dry_run"),
                "stages": [s.get("name") for s in (verification.get("stages") or [])],
                "replay_ok": replay.get("ok"),
                "fabricated_id_code": err_code(negatives["fabricated_approval_id"]),
                "fabricated_object_code": err_code(negatives["fabricated_approval_object"]),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print("WROTE", args.output, flush=True)

    provenance_stage = any(
        stage.get("name") == "approval_provenance" and stage.get("ok") is True
        for stage in (verification.get("stages") or [])
    )
    negatives_fail = all(resp.get("ok") is not True for resp in negatives.values())
    return 0 if positive.get("ok") is True and provenance_stage and negatives_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
