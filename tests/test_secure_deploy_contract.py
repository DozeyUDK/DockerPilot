"""Offline contract tests for Secure Deploy Spec/Plan v1."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from dockerpilot.secure_deploy import (
    SchemaValidationError,
    canonical_json_bytes,
    compute_plan_sha256,
    redact_for_log,
    sha256_hex,
    validate_deployment_plan,
    validate_secure_deployment_spec,
)
from dockerpilot.secure_deploy.canonical import sha256_canonical
from dockerpilot.secure_deploy.models import PLAN_HASH_EXCLUDED_FIELDS, plan_hash_payload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "secure_deploy"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_import_secure_deploy_does_not_touch_docker_sdk(monkeypatch):
    """Importing secure_deploy must not initialize Docker SDK."""

    def boom(*_args, **_kwargs):
        raise AssertionError("docker must not be imported/initialized")

    monkeypatch.setitem(sys.modules, "docker", None)
    # Re-import package pieces; dockerpilot.secure_deploy should not need docker.
    import dockerpilot.secure_deploy as sd

    importlib.reload(sd)
    assert hasattr(sd, "validate_secure_deployment_spec")


def test_pass_minimal_production_localhost():
    doc = _load(FIXTURES / "pass" / "minimal_production_localhost.json")
    validate_secure_deployment_spec(doc)


@pytest.mark.parametrize(
    "name",
    sorted(p.name for p in (FIXTURES / "fail").glob("*.json")),
)
def test_fail_fixtures_rejected(name: str):
    doc = _load(FIXTURES / "fail" / name)
    with pytest.raises(SchemaValidationError):
        validate_secure_deployment_spec(doc)


def test_canonical_hash_determinism_and_key_order_independence():
    a = {"b": 1, "a": {"y": 2, "x": [3, 1]}}
    b = {"a": {"x": [3, 1], "y": 2}, "b": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b)
    assert sha256_canonical(a) == sha256_canonical(b)


def test_argv_order_changes_hash():
    left = {"command": ["nginx", "-g", "daemon off;"]}
    right = {"command": ["-g", "nginx", "daemon off;"]}
    assert sha256_canonical(left) != sha256_canonical(right)


def test_single_field_change_changes_hash():
    doc = _load(FIXTURES / "pass" / "minimal_production_localhost.json")
    base = sha256_canonical(doc)
    doc2 = json.loads(json.dumps(doc))
    doc2["metadata"]["owner"] = "other"
    assert sha256_canonical(doc2) != base


def test_plan_hash_excludes_self_and_timestamps():
    plan = _load(FIXTURES / "cross" / "deployment_plan_template.json")
    payload = plan_hash_payload(plan)
    for field in PLAN_HASH_EXCLUDED_FIELDS:
        assert field not in payload
    digest = compute_plan_sha256(plan)
    assert len(digest) == 64
    plan_with_hash = dict(plan)
    plan_with_hash["plan_sha256"] = digest
    assert compute_plan_sha256(plan_with_hash) == digest


def test_plan_validation_and_approval_placeholder():
    plan = _load(FIXTURES / "cross" / "deployment_plan_template.json")
    digest = compute_plan_sha256(plan)
    plan["approval"]["plan_sha256"] = digest
    plan["plan_sha256"] = digest
    validate_deployment_plan(plan)


def test_redaction_sentinel():
    payload = {
        "name": "nginx_htpasswd",
        "db_password": "SUPER_SECRET_SENTINEL_DO_NOT_LEAK",
        "nested": {"api_token": "SUPER_SECRET_SENTINEL_DO_NOT_LEAK"},
    }
    redacted = redact_for_log(payload)
    blob = json.dumps(redacted)
    assert "SUPER_SECRET_SENTINEL_DO_NOT_LEAK" not in blob
    assert redacted["name"] == "nginx_htpasswd"
    assert redacted["db_password"] == "[REDACTED]"


def test_cross_contract_hashes():
    spec = _load(FIXTURES / "pass" / "minimal_production_localhost.json")
    compose = _load(FIXTURES / "cross" / "normalized_compose.json")
    plan = _load(FIXTURES / "cross" / "deployment_plan_template.json")

    validate_secure_deployment_spec(spec)
    spec_sha = sha256_canonical(spec)
    compose_sha = sha256_canonical(compose)

    plan["spec_sha256"] = spec_sha
    plan["normalized_compose_sha256"] = compose_sha
    plan_sha = compute_plan_sha256(plan)
    plan["plan_sha256"] = plan_sha
    plan["approval"]["plan_sha256"] = plan_sha
    validate_deployment_plan(plan)

    # Golden-ish: changing compose changes plan inputs.
    compose2 = json.loads(json.dumps(compose))
    compose2["services"]["web"]["pids_limit"] = 64
    assert sha256_canonical(compose2) != compose_sha


def test_sha256_empty_vector():
    assert (
        sha256_hex(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_schema_mirror_byte_equality():
    root = Path(__file__).resolve().parents[1]
    pairs = [
        (
            root / "schemas" / "secure-deployment-spec-v1.schema.json",
            root
            / "src"
            / "dockerpilot"
            / "secure_deploy"
            / "schemas"
            / "secure-deployment-spec-v1.schema.json",
        ),
        (
            root / "schemas" / "deployment-plan-v1.schema.json",
            root
            / "src"
            / "dockerpilot"
            / "secure_deploy"
            / "schemas"
            / "deployment-plan-v1.schema.json",
        ),
    ]
    for left, right in pairs:
        assert left.read_bytes() == right.read_bytes(), f"schema mirror drift: {left.name}"


def test_fixed_golden_hashes():
    golden = _load(FIXTURES / "cross" / "golden_hashes.json")
    spec = _load(FIXTURES / "pass" / "minimal_production_localhost.json")
    compose = _load(FIXTURES / "cross" / "normalized_compose.json")
    plan = _load(FIXTURES / "cross" / "deployment_plan_template.json")
    dg = _load(FIXTURES / "cross" / "dozeyguard_golden_result.json")

    assert sha256_canonical(spec) == golden["spec_sha256"]
    assert sha256_canonical(compose) == golden["normalized_compose_sha256"]

    plan["spec_sha256"] = golden["spec_sha256"]
    plan["normalized_compose_sha256"] = golden["normalized_compose_sha256"]
    assert compute_plan_sha256(plan) == golden["deployment_plan_sha256"]

    # Dozeyguard golden: hash payload excludes result.result_sha256 (absent here).
    assert sha256_canonical(dg) == golden["dozeyguard_result_sha256"]

    # Mutation must break goldens.
    mutated = json.loads(json.dumps(spec))
    mutated["metadata"]["owner"] = "mutated-owner"
    assert sha256_canonical(mutated) != golden["spec_sha256"]


def test_schema_rejects_host_network_via_forbidden_runtime_key():
    doc = _load(FIXTURES / "pass" / "minimal_production_localhost.json")
    doc = json.loads(json.dumps(doc))
    doc["runtime"]["network_mode"] = "host"
    with pytest.raises(SchemaValidationError):
        validate_secure_deployment_spec(doc)
