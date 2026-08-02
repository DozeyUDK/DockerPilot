"""Independent plan verification for the Secure Deploy broker."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dockerpilot.secure_deploy import (
    SchemaValidationError,
    compute_plan_sha256,
    validate_deployment_plan,
    validate_secure_deployment_spec,
)
from dockerpilot.secure_deploy.canonical import sha256_canonical, sha256_hex

from .approval import assert_approval_binds_plan, parse_ts
from .errors import VerificationError


@dataclass
class BrokerDozeyguardConfig:
    executable: str
    policy_path: str
    expected_policy_sha256: Optional[str] = None


def resolve_broker_dozeyguard_config(
    *,
    executable: Optional[str] = None,
    policy_path: Optional[str] = None,
    expected_policy_sha256: Optional[str] = None,
    allow_writable_by_uid: Optional[int] = None,
) -> BrokerDozeyguardConfig:
    """Resolve broker-owned Dozeyguard paths. Does NOT inherit Flask env vars."""
    exe = executable or os.environ.get("BROKER_DOZEYGUARD_BIN")
    policy = policy_path or os.environ.get("BROKER_DOZEYGUARD_POLICY_PATH")
    if not exe or not policy:
        raise VerificationError("broker_dozeyguard_missing", "BROKER_DOZEYGUARD_BIN/POLICY required")
    exe_path = Path(exe)
    policy_file = Path(policy)
    if not exe_path.is_file() or not policy_file.is_file():
        raise VerificationError("broker_dozeyguard_missing", "broker Dozeyguard paths not found")
    if exe_path.is_symlink() or policy_file.is_symlink():
        raise VerificationError("broker_dozeyguard_symlink", "broker Dozeyguard symlink rejected")
    # Fail-closed if policy is writable by the untrusted service UID (canary check).
    deny_uid = allow_writable_by_uid
    if deny_uid is not None:
        mode = policy_file.stat().st_mode
        if policy_file.stat().st_uid == deny_uid and (mode & 0o200):
            raise VerificationError(
                "broker_policy_writable",
                "broker policy writable by untrusted UID",
            )
    policy_sha = sha256_hex(policy_file.read_bytes())
    expected = expected_policy_sha256 or os.environ.get("BROKER_DOZEYGUARD_POLICY_SHA256")
    if expected and expected != policy_sha:
        raise VerificationError("broker_policy_hash", "broker policy checksum mismatch")
    return BrokerDozeyguardConfig(
        executable=str(exe_path.resolve()),
        policy_path=str(policy_file.resolve()),
        expected_policy_sha256=policy_sha,
    )


def _stage(stages: List[Dict[str, Any]], name: str, ok: bool, detail: str = "") -> None:
    stages.append({"name": name, "ok": ok, "detail": detail[:256]})


def verify_plan_independent(
    plan: Dict[str, Any],
    approval: Optional[Dict[str, Any]],
    *,
    dozeyguard_config: BrokerDozeyguardConfig,
    now: Optional[datetime] = None,
    require_approval: bool = False,
    run_dozeyguard,
    normalize_spec_to_compose,
    plan_firewall_actions,
) -> Dict[str, Any]:
    """Revalidate plan without trusting client-provided digests/status."""
    stages: List[Dict[str, Any]] = []
    clock = now or datetime.now(timezone.utc)

    try:
        validate_deployment_plan(plan)
        _stage(stages, "plan_schema", True)
    except SchemaValidationError as exc:
        _stage(stages, "plan_schema", False, str(exc))
        raise VerificationError("plan_schema", str(exc)) from exc

    source_spec = plan.get("source_spec")
    if not isinstance(source_spec, dict):
        _stage(stages, "source_spec", False, "missing source_spec (contract v1.1)")
        raise VerificationError("plan_not_self_contained", "plan missing source_spec for revalidation")
    _stage(stages, "source_spec", True)

    recomputed = compute_plan_sha256(plan)
    claimed = plan.get("plan_sha256")
    if claimed != recomputed:
        _stage(stages, "plan_sha256", False, "forged or drifted plan hash")
        raise VerificationError("plan_hash_mismatch", "plan_sha256 mismatch")
    _stage(stages, "plan_sha256", True)

    try:
        expires = parse_ts(str(plan["expires_at"]))
    except Exception as exc:  # noqa: BLE001
        raise VerificationError("plan_expiry", "invalid plan expires_at") from exc
    if clock >= expires:
        _stage(stages, "plan_ttl", False, "plan expired")
        raise VerificationError("plan_expired", "plan expired")
    _stage(stages, "plan_ttl", True)

    if require_approval:
        if not isinstance(approval, dict):
            raise VerificationError("approval_required", "approval required")
        assert_approval_binds_plan(
            approval,
            plan_id=str(plan["plan_id"]),
            plan_sha256=recomputed,
            now=clock,
            require_status="approved",
        )
        _stage(stages, "approval_binding", True)

    try:
        validate_secure_deployment_spec(source_spec)
        _stage(stages, "spec_schema", True)
    except SchemaValidationError as exc:
        _stage(stages, "spec_schema", False, str(exc))
        raise VerificationError("spec_schema", str(exc)) from exc

    spec_sha = sha256_canonical(source_spec)
    if spec_sha != plan.get("spec_sha256"):
        _stage(stages, "spec_sha256", False)
        raise VerificationError("spec_hash_mismatch", "spec_sha256 mismatch")
    _stage(stages, "spec_sha256", True)

    compose = normalize_spec_to_compose(source_spec)
    compose_sha = sha256_canonical(compose)
    if compose_sha != plan.get("normalized_compose_sha256"):
        _stage(stages, "compose_hash", False)
        raise VerificationError("compose_hash_mismatch", "normalized compose hash mismatch")
    if compose != (plan.get("compose") or {}).get("normalized_model"):
        _stage(stages, "compose_model", False)
        raise VerificationError("compose_model_mismatch", "normalized compose model mismatch")
    _stage(stages, "compose_recompute", True)

    report = run_dozeyguard(compose, dozeyguard_config)
    summary = report.get("summary") or {}
    result = report.get("result") or {}
    dg = plan.get("dozeyguard") or {}
    policy_info = report.get("policy") or {}
    if int(dg.get("contract_version") or 0) != 1:
        raise VerificationError("scanner_contract", "unexpected dozeyguard contract in plan")
    if policy_info.get("sha256") and dg.get("policy_sha256") and policy_info.get("sha256") != dg.get("policy_sha256"):
        # Compare against broker-owned scan; client-claimed policy must match fresh scan.
        _stage(stages, "policy_hash", False)
        raise VerificationError("policy_hash_mismatch", "policy hash mismatch")
    if result.get("result_sha256") != dg.get("result_sha256"):
        _stage(stages, "dozeyguard_result", False, "forged PASS/result hash")
        raise VerificationError("dozeyguard_result_mismatch", "dozeyguard result hash mismatch")
    exit_code = result.get("exit_code")
    plan_exit = dg.get("exit_code")
    if exit_code is None or plan_exit is None or int(exit_code) != int(plan_exit):
        _stage(stages, "dozeyguard_exit", False)
        raise VerificationError("dozeyguard_exit_mismatch", "dozeyguard exit mismatch")
    blocking = int(summary.get("blocking") or 0)
    plan_blocking = dg.get("blocking_findings")
    if plan_blocking is None or blocking != int(plan_blocking):
        _stage(stages, "blocking_findings", False)
        raise VerificationError("blocking_mismatch", "blocking findings mismatch")
    if blocking > 0 or int(exit_code) == 2:
        _stage(stages, "dozeyguard_policy", False, "blocking findings present")
        raise VerificationError("dozeyguard_blocking", "blocking Dozeyguard findings")
    _stage(stages, "dozeyguard_revalidate", True)

    firewall = plan_firewall_actions(source_spec, plan_sha256=recomputed)
    planned = plan.get("firewall") or {}
    if bool(firewall.get("required")) != bool(planned.get("required")):
        raise VerificationError("firewall_mismatch", "firewall.required mismatch")
    # Semantic compare on rule_ids + ports/sources for docker-user actions.
    left = sorted(
        (
            a.get("rule_id"),
            a.get("host_port") or a.get("port"),
            a.get("source") or a.get("cidr"),
            a.get("protocol") or a.get("proto"),
        )
        for a in (firewall.get("docker_user_actions") or [])
    )
    right_actions = planned.get("docker_user_actions") or []
    right = sorted(
        (
            a.get("rule_id"),
            a.get("port"),
            a.get("cidr"),
            a.get("proto"),
        )
        for a in right_actions
    )
    if left != right:
        _stage(stages, "firewall_plan", False)
        raise VerificationError("firewall_mismatch", "firewall plan semantic mismatch")
    _stage(stages, "firewall_recompute", True)

    digest_payload = {
        "plan_id": plan.get("plan_id"),
        "plan_sha256": recomputed,
        "spec_sha256": spec_sha,
        "compose_sha256": compose_sha,
        "dozeyguard_result_sha256": result.get("result_sha256"),
        "policy_sha256": policy_info.get("sha256") or dg.get("policy_sha256"),
        "stages": [s["name"] for s in stages if s["ok"]],
    }
    broker_sha = sha256_canonical(digest_payload)
    return {
        "status": "pass",
        "broker_verification_sha256": broker_sha,
        "stages": stages,
        "plan_id": plan.get("plan_id"),
        "plan_sha256": recomputed,
        "dozeyguard_exit_code": int(result.get("exit_code") or 0),
        "blocking_findings": blocking,
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
