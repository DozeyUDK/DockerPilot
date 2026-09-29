from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dockerpilot.secure_deploy import compute_plan_sha256
from dockerpilot.secure_deploy_broker.errors import VerificationError
from dockerpilot.secure_deploy_broker.verifier import BrokerDozeyguardConfig, verify_plan_independent

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "secure_deploy"


def _self_contained_plan() -> dict:
    plan = json.loads((FIXTURES / "cross" / "deployment_plan_template.json").read_text(encoding="utf-8"))
    plan["source_spec"] = json.loads(
        (FIXTURES / "pass" / "minimal_production_localhost.json").read_text(encoding="utf-8")
    )
    plan["contract_extension"] = "v1.1"
    plan_sha = compute_plan_sha256(plan)
    plan["plan_sha256"] = plan_sha
    plan["approval"]["plan_sha256"] = plan_sha
    return plan


def _verify_until_hash_or_ttl(plan: dict) -> None:
    def must_not_continue(*_args, **_kwargs):
        pytest.fail("verification continued beyond plan identity / TTL gate")

    verify_plan_independent(
        plan,
        None,
        dozeyguard_config=BrokerDozeyguardConfig(
            executable="/not-used/dozeyguard",
            policy_path="/not-used/policy.toml",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        require_approval=False,
        run_dozeyguard=must_not_continue,
        normalize_spec_to_compose=must_not_continue,
        plan_firewall_actions=must_not_continue,
    )


def test_extending_plan_expiry_without_new_hash_is_rejected_at_plan_identity():
    plan = _self_contained_plan()
    approved_hash = plan["plan_sha256"]

    tampered = copy.deepcopy(plan)
    tampered["expires_at"] = "2026-08-31T12:00:00+00:00"

    # AT-08B: the already-approved identity is retained by the attacker.
    assert tampered["plan_sha256"] == approved_hash
    assert compute_plan_sha256(tampered) != approved_hash

    with pytest.raises(VerificationError) as exc_info:
        _verify_until_hash_or_ttl(tampered)
    assert exc_info.value.code == "plan_hash_mismatch"


def test_expired_plan_with_recomputed_hash_reaches_independent_ttl_gate():
    plan = _self_contained_plan()
    expired = copy.deepcopy(plan)
    expired["expires_at"] = "2026-06-30T12:00:00+00:00"
    expired["plan_sha256"] = compute_plan_sha256(expired)
    expired["approval"]["plan_sha256"] = expired["plan_sha256"]

    with pytest.raises(VerificationError) as exc_info:
        _verify_until_hash_or_ttl(expired)
    assert exc_info.value.code == "plan_expired"
