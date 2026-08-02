#!/usr/bin/env python3
"""Root broker verify_plan canary — run as dockerpilot-extras only (no system mutation)."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Set

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
DEFAULT_OUT = Path("/tmp/broker_verify_plan_canary_results.json")

# Mirror dockerpilot.secure_deploy.models.PLAN_HASH_EXCLUDED_FIELDS (stdlib only —
# do not import dockerpilot package: its __init__ pulls the Docker SDK).
_PLAN_HASH_EXCLUDED: Set[str] = {"plan_sha256", "created_at", "expires_at", "approval"}


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


def _sha256_canonical(obj: Any) -> str:
    raw = json.dumps(
        _canonicalize(obj),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _strip_excluded(obj: Any, excluded_top: Set[str], depth: int = 0) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, value in obj.items():
            if depth == 0 and key in excluded_top:
                continue
            out[key] = _strip_excluded(value, excluded_top, depth + 1)
        return out
    if isinstance(obj, list):
        return [_strip_excluded(item, excluded_top, depth + 1) for item in obj]
    return obj


def recompute_plan_sha256(plan: Mapping[str, Any]) -> str:
    """Stdlib-only plan_sha256 (no Docker SDK / dockerpilot package import)."""
    return _sha256_canonical(_strip_excluded(dict(plan), _PLAN_HASH_EXCLUDED))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Root broker verify_plan canary (extras UID only)")
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="DeploymentPlan JSON path")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Results JSON path")
    p.add_argument("--socket", default=DEFAULT_SOCKET, help="Broker Unix socket path")
    p.add_argument("--client-version", default=DEFAULT_CLIENT_VERSION, help="client.version field")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("WHOAMI", os.getuid(), os.getgid(), flush=True)
    base = json.loads(args.plan.read_text(encoding="utf-8"))
    plan_mtime_before = args.plan.stat().st_mtime_ns

    assert recompute_plan_sha256(base) == base["plan_sha256"], "stdlib plan hash mismatch vs fixture"

    def _call(operation: str, **kwargs):
        return call(
            operation,
            socket_path=args.socket,
            client_version=args.client_version,
            **kwargs,
        )

    positive = _call("verify_plan", plan=base)
    print("POSITIVE", json.dumps(positive, indent=2, sort_keys=True), flush=True)

    negatives: dict = {}

    p = copy.deepcopy(base)
    p["actor"] = str(base.get("actor")) + "_forged"
    negatives["plan_hash"] = _call("verify_plan", plan=p)

    p = copy.deepcopy(base)
    p["source_spec"] = copy.deepcopy(base["source_spec"])
    meta = dict(p["source_spec"].get("metadata") or {})
    meta["service"] = str(meta.get("service") or "svc") + "_tampered"
    p["source_spec"]["metadata"] = meta
    p["plan_sha256"] = recompute_plan_sha256(p)
    negatives["source_spec"] = _call("verify_plan", plan=p)

    p = copy.deepcopy(base)
    model = copy.deepcopy((base.get("compose") or {}).get("normalized_model") or {})
    if isinstance(model, dict):
        model["tampered"] = True
    p.setdefault("compose", {})["normalized_model"] = model
    p["plan_sha256"] = recompute_plan_sha256(p)
    negatives["compose_model"] = _call("verify_plan", plan=p)

    p = copy.deepcopy(base)
    p["dozeyguard"] = dict(base["dozeyguard"])
    p["dozeyguard"]["result_sha256"] = "f" * 64
    p["plan_sha256"] = recompute_plan_sha256(p)
    negatives["dozeyguard_result"] = _call("verify_plan", plan=p)

    p = copy.deepcopy(base)
    p["firewall"] = dict(base.get("firewall") or {})
    p["firewall"]["required"] = not bool(p["firewall"].get("required"))
    p["plan_sha256"] = recompute_plan_sha256(p)
    negatives["firewall_plan"] = _call("verify_plan", plan=p)

    p = copy.deepcopy(base)
    p["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    p["plan_sha256"] = recompute_plan_sha256(p)
    negatives["expired"] = _call("verify_plan", plan=p)

    plan_mtime_after = args.plan.stat().st_mtime_ns
    results = {
        "whoami": {"uid": os.getuid(), "gid": os.getgid()},
        "positive": positive,
        "negatives": negatives,
        "plan_file_mtime_unchanged": plan_mtime_before == plan_mtime_after,
        "plan_id": base.get("plan_id"),
        "plan_sha256": base.get("plan_sha256"),
    }
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("NEGATIVES_SUMMARY", flush=True)
    for name, resp in negatives.items():
        print(f"{name}\tok={resp.get('ok')}\tcode={err_code(resp)}\tmsg={(resp.get('error') or {}).get('message')}", flush=True)
    print("WROTE", args.output, flush=True)
    return 0 if positive.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
