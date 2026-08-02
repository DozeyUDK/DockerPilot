#!/usr/bin/env python3
"""Root broker dry_run canary — run as dockerpilot-extras only (no system mutation).

Approval consumption model (from docs/tests/code — not guessed):
- Broker dry_run validates approval binding only (status approved, plan_id,
  plan_sha256, not expired). It does NOT consume approval.
- Consume is Extras ApprovalService for future apply (#11D.2).
- Broker does NOT bind actor/nonce to the plan (only schema presence).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
APPROVAL_TTL_MINUTES = 10


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def make_approval(
    plan: Dict[str, Any],
    *,
    status: str = "approved",
    plan_id: Optional[str] = None,
    plan_sha256: Optional[str] = None,
    actor: Optional[str] = None,
    nonce: Optional[str] = None,
    expires_at: Optional[str] = None,
    approved_at: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    plan_expires = datetime.fromisoformat(str(plan["expires_at"]).replace("Z", "+00:00"))
    expires = now + timedelta(minutes=APPROVAL_TTL_MINUTES)
    if expires > plan_expires:
        expires = plan_expires
    return {
        "approval_version": 1,
        "approval_id": "appr_" + secrets.token_hex(12),
        "plan_id": plan_id if plan_id is not None else plan["plan_id"],
        "plan_sha256": plan_sha256 if plan_sha256 is not None else plan["plan_sha256"],
        "actor": actor if actor is not None else str(plan.get("actor") or "canary"),
        "nonce": nonce if nonce is not None else secrets.token_urlsafe(16),
        "issued_at": _iso(now),
        "approved_at": approved_at if approved_at is not None else _iso(now),
        "expires_at": expires_at if expires_at is not None else _iso(expires),
        "session_id_hash": secrets.token_hex(32),
        "status": status,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Root broker dry_run canary (extras UID only)")
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="DeploymentPlan JSON path")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Results JSON path")
    p.add_argument("--socket", default=DEFAULT_SOCKET, help="Broker Unix socket path")
    p.add_argument("--client-version", default=DEFAULT_CLIENT_VERSION, help="client.version field")
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

    approval = make_approval(plan)
    positive = _call("dry_run", plan=plan, approval=approval)
    print("POSITIVE", json.dumps(positive, indent=2, sort_keys=True), flush=True)

    replay = _call("dry_run", plan=plan, approval=approval)
    print(
        "REPLAY",
        json.dumps(
            {
                "ok": replay.get("ok"),
                "code": err_code(replay),
                "status": (replay.get("verification") or {}).get("status"),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    mutated = dict(approval)
    mutated["actor"] = str(approval["actor"]) + "_mutated"
    mutated["nonce"] = secrets.token_urlsafe(16)
    actor_nonce = _call("dry_run", plan=plan, approval=mutated)
    print(
        "ACTOR_NONCE_MUTATION",
        json.dumps(
            {
                "ok": actor_nonce.get("ok"),
                "code": err_code(actor_nonce),
                "note": "broker assert_approval_binds_plan does not check actor/nonce",
            },
            sort_keys=True,
        ),
        flush=True,
    )

    negatives: Dict[str, Any] = {}
    negatives["missing_approval"] = _call("dry_run", plan=plan)
    negatives["wrong_plan_hash"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, plan_sha256="a" * 64)
    )
    negatives["wrong_plan_id"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, plan_id="plan_deadbeefdeadbeef")
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    negatives["expired_approval"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, expires_at=past, approved_at=past)
    )
    negatives["bad_status_consumed"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, status="consumed")
    )
    negatives["bad_status_pending"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, status="pending")
    )
    negatives["bad_status_revoked"] = _call(
        "dry_run", plan=plan, approval=make_approval(plan, status="revoked")
    )

    plan_mtime_after = args.plan.stat().st_mtime_ns
    plan_after = json.loads(args.plan.read_text(encoding="utf-8"))
    v = positive.get("verification") or {}
    results = {
        "whoami": {"uid": os.getuid(), "gid": os.getgid()},
        "approval_used": {
            "approval_id": approval["approval_id"],
            "status": approval["status"],
            "expires_at": approval["expires_at"],
            "plan_id": approval["plan_id"],
            "plan_sha256": approval["plan_sha256"],
            "actor": approval["actor"],
        },
        "positive": positive,
        "replay": replay,
        "actor_nonce_mutation": actor_nonce,
        "negatives": negatives,
        "plan_file_mtime_unchanged": plan_mtime_before == plan_mtime_after,
        "plan_sha256_unchanged": plan_sha_before == plan_after.get("plan_sha256"),
        "plan_id": plan.get("plan_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "approval_consumption_model": "validate_only_not_consumed",
        "actor_nonce_bound_by_broker": False,
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
                "status": v.get("status"),
                "dry_run": v.get("dry_run"),
                "stages": [s.get("name") for s in (v.get("stages") or [])],
                "broker_verification_sha256": v.get("broker_verification_sha256"),
                "replay_ok": replay.get("ok"),
                "actor_nonce_ok": actor_nonce.get("ok"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print("WROTE", args.output, flush=True)
    return 0 if positive.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
