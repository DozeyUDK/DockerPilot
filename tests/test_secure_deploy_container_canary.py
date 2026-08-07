"""Secure Deploy broker-owned container canary tests (no real Docker)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_DG = ROOT / "tests" / "fixtures" / "secure_deploy_preview" / "fake_dozeyguard.py"
POLICY = ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "policy" / "preview.toml"


def _prefer_local_paths() -> None:
    for path in (ROOT / "DockerPilotExtras", ROOT / "src"):
        text = str(path)
        while text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)


_prefer_local_paths()


def _load_modules():
    _prefer_local_paths()
    from backend.secure_deploy.approval import ApprovalService
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from backend.secure_deploy.service import SecureDeployService
    from backend.secure_deploy.store import FileSecureDeployStore
    from dockerpilot.secure_deploy import compute_plan_sha256
    from dockerpilot.secure_deploy_broker.canary import (
        CONSUMED,
        FAILED_CLEANUP_FAILED,
        FAILED_CLEANUP_OK,
        REMOVED,
        TEMPLATE_ID,
        CanaryLedger,
        CanaryManager,
        CanaryPolicy,
        CommandResult,
        CommandRunner,
        canonical_compose,
        controlled_env,
        down_argv,
        read_compose_bytes,
        up_argv,
    )
    from dockerpilot.secure_deploy_broker.dg_runner import run_broker_dozeyguard, run_broker_dozeyguard_bytes
    from dockerpilot.secure_deploy_broker.errors import ProtocolError, VerificationError
    from dockerpilot.secure_deploy_broker.protocol import PROTOCOL_VERSION, validate_request, validate_response
    from dockerpilot.secure_deploy_broker.verifier import BrokerDozeyguardConfig

    return SimpleNamespace(**locals())


class FakeRunner:
    def __init__(self, results: list[Any]):
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def run(self, argv, *, cwd, env, timeout):
        with self.lock:
            self.calls.append({"argv": list(argv), "cwd": Path(cwd), "env": dict(env), "timeout": timeout})
            item = self.results.pop(0) if self.results else (0, "", "")
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _load_modules().CommandResult):
            return item
        return _result(*item)


def _result(returncode=0, stdout="", stderr="", **kwargs):
    mods = _load_modules()
    return mods.CommandResult(returncode, stdout, stderr, **kwargs)


def _ps(policy, *, state="running", health="healthy", service=None, project=None, name=None):
    return json.dumps(
        [
            {
                "Project": project or policy.project,
                "Service": service or policy.service,
                "Name": name or f"{policy.project}-{policy.service}-1",
                "State": state,
                "Health": health,
            }
        ]
    )


def _canary_spec(policy):
    reference, digest = policy.image.split("@", 1)
    return {
        "schema_version": 1,
        "metadata": {
            "project": policy.project,
            "service": policy.service,
            "owner": "dockerpilot",
            "environment": "production",
            "change_ticket": "SD-12A",
        },
        "image": {
            "reference": reference,
            "digest": digest,
            "pull_policy": "if_not_present",
            "allow_mutable_tag": False,
            "exception_ref": None,
        },
        "runtime": {
            "user": "101:101",
            "read_only": True,
            "no_new_privileges": True,
            "restart_policy": "no",
            "cap_drop": ["ALL"],
            "cap_add": [],
            "pids_limit": 128,
            "memory_limit": "128m",
            "cpu_limit": "0.25",
            "tmpfs": [
                {"target": "/var/cache/nginx"},
                {"target": "/var/run"},
                {"target": "/tmp"},
            ],
        },
        "network": {
            "exposure": "localhost",
            "published_ports": [
                {
                    "container_port": 80,
                    "host_port": policy.port,
                    "protocol": "tcp",
                    "bind_address": policy.host,
                    "exposure": "localhost",
                }
            ],
            "allowed_sources": [],
            "networks": [],
            "dns": [],
        },
        "storage": {"volumes": []},
        "secrets": {"refs": []},
        "health": {
            "required": True,
            "test": ["CMD", "nginx", "-t"],
            "interval_seconds": 5,
            "timeout_seconds": 3,
            "retries": 12,
            "start_period_seconds": 0,
        },
        "deployment": {
            "strategy": "recreate",
            "rollback_enabled": True,
            "health_timeout_seconds": policy.health_timeout_seconds,
            "max_unavailable": 0,
            "lock_scope": "project_service",
        },
        "exceptions": {"policy_refs": []},
    }


def _fixture(tmp_path, *, now: datetime | None = None, policy=None):
    mods = _load_modules()
    now = now or datetime.now(timezone.utc)
    policy = policy or mods.CanaryPolicy(workdir=tmp_path / "canary", health_timeout_seconds=3, health_poll_interval_seconds=0.1)
    store = mods.FileSecureDeployStore(tmp_path / "store")
    service = mods.SecureDeployService(
        store,
        dozeyguard_config=mods.DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
    )
    approvals = mods.ApprovalService(store, clock=lambda: now)
    env = service.generate_plan(_canary_spec(policy), "admin", request_id="req_canary")
    plan = env["plan"]
    approval = approvals.approve_plan(service.get_plan(plan["plan_id"]), actor="admin", session_hash="a" * 64)
    dg_config = mods.BrokerDozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY))
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 0.25
        return clock["t"]

    return SimpleNamespace(
        mods=mods,
        policy=policy,
        store=store,
        service=service,
        plan=plan,
        approval=approval,
        dg_config=dg_config,
        normalize=mods.normalize_spec_to_compose,
        firewall=mods.plan_firewall_actions,
        run_dg=mods.run_broker_dozeyguard,
        run_dg_bytes=mods.run_broker_dozeyguard_bytes,
        now=lambda: now,
        monotonic=monotonic,
    )


def _manager(fix, runner, *, port_free=True, http=(200, "Welcome to nginx"), pre_up_hook=None):
    return fix.mods.CanaryManager(
        policy=fix.policy,
        ledger=fix.mods.CanaryLedger(fix.policy.workdir),
        command_runner=runner,
        port_checker=lambda _host, _port: port_free,
        http_probe=lambda _host, _port, _timeout: http,
        now=fix.now,
        monotonic=fix.monotonic,
        sleep=lambda _seconds: None,
        pre_up_hook=pre_up_hook,
    )


def _bundle_sha(manager, fix, *, plan=None, approval=None, staged_at=None, staged_bundle_expires_at=None, save_staged=True):
    plan = plan or fix.plan
    approval = approval or fix.approval
    staged_at = staged_at or fix.now().isoformat().replace("+00:00", "Z")
    staged_bundle_expires_at = staged_bundle_expires_at or (fix.now() + timedelta(seconds=fix.policy.staged_bundle_ttl_seconds)).isoformat().replace("+00:00", "Z")
    bundle_sha = manager.ledger.save_admission_bundle(
        plan=plan,
        approval=approval,
        template_id=fix.mods.TEMPLATE_ID,
        staged_at=staged_at,
        staged_bundle_expires_at=staged_bundle_expires_at,
    )
    request_key = manager.ledger._request_key(
        plan_id=str(plan["plan_id"]),
        plan_sha256=str(plan["plan_sha256"]),
        approval_id=str(approval["approval_id"]),
    )
    execution_id = "exec_" + request_key[:24]
    if save_staged:
        manager.ledger.save_staged(
            {
                "schema_version": 1,
                "execution_id": execution_id,
                "template_id": fix.mods.TEMPLATE_ID,
                "state": "staged",
                "plan_id": plan["plan_id"],
                "plan_sha256": plan["plan_sha256"],
                "approval_id": approval["approval_id"],
                "admission_bundle_sha256": bundle_sha,
                "request_key": request_key,
                "staged_at": staged_at,
                "staged_bundle_expires_at": staged_bundle_expires_at,
            }
        )
    return bundle_sha


def _admit(manager, fix, *, plan_sha256=None, approval=None, bundle_sha=None):
    approval = approval or fix.approval
    bundle_sha = bundle_sha or _bundle_sha(manager, fix, approval=approval)
    return manager.admit(
        plan_id=fix.plan["plan_id"],
        plan_sha256=plan_sha256 or fix.plan["plan_sha256"],
        approval_id=approval["approval_id"],
        admission_bundle_sha256=bundle_sha,
        template_id=fix.mods.TEMPLATE_ID,
        dozeyguard_config=fix.dg_config,
        run_dozeyguard=fix.run_dg,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
    )


def _deploy(manager, fix):
    return manager.deploy(
        plan_id=fix.plan["plan_id"],
        plan_sha256=fix.plan["plan_sha256"],
        approval_id=fix.approval["approval_id"],
        run_dozeyguard_bytes=fix.run_dg_bytes,
        dozeyguard_config=fix.dg_config,
    )


def test_request_schema_accepts_only_closed_canary_operations(tmp_path):
    mods = _load_modules()
    fix = _fixture(tmp_path)
    base = {
        "protocol_version": mods.PROTOCOL_VERSION,
        "request_id": "breq_canary001",
        "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
    }
    mods.validate_request(
        {
            **base,
            "operation": "admit_canary_execution",
            "template_id": mods.TEMPLATE_ID,
            "plan_id": fix.plan["plan_id"],
            "plan_sha256": fix.plan["plan_sha256"],
            "approval_id": fix.approval["approval_id"],
            "admission_bundle_sha256": "a" * 64,
        }
    )
    for forbidden in ("plan", "approval"):
        with pytest.raises(mods.ProtocolError):
            mods.validate_request(
                {
                    **base,
                    "operation": "admit_canary_execution",
                    "template_id": mods.TEMPLATE_ID,
                    "plan_id": fix.plan["plan_id"],
                    "plan_sha256": fix.plan["plan_sha256"],
                    "approval_id": fix.approval["approval_id"],
                    "admission_bundle_sha256": "a" * 64,
                    forbidden: {},
                }
            )
    mods.validate_request(
        {
            **base,
            "operation": "deploy_canary",
            "plan_id": fix.plan["plan_id"],
            "plan_sha256": fix.plan["plan_sha256"],
            "approval_id": fix.approval["approval_id"],
        }
    )
    mods.validate_request(
        {
            **base,
            "operation": "revoke_canary_admission",
            "plan_id": fix.plan["plan_id"],
            "plan_sha256": fix.plan["plan_sha256"],
            "approval_id": fix.approval["approval_id"],
            "admission_bundle_sha256": "a" * 64,
        }
    )
    for forbidden in ("template_id", "plan", "approval"):
        with pytest.raises(mods.ProtocolError):
            mods.validate_request(
                {
                    **base,
                    "operation": "revoke_canary_admission",
                    "plan_id": fix.plan["plan_id"],
                    "plan_sha256": fix.plan["plan_sha256"],
                    "approval_id": fix.approval["approval_id"],
                    "admission_bundle_sha256": "a" * 64,
                    forbidden: mods.TEMPLATE_ID if forbidden == "template_id" else {},
                }
            )
    mods.validate_request({**base, "operation": "remove_canary", "canary_execution_id": "exec_" + "a" * 24})

    for field, value in {
        "project": "x",
        "service": "web",
        "workdir": "/tmp/x",
        "image": "nginx",
        "port": 80,
        "health_timeout_seconds": 1,
        "command": "id",
        "args": ["id"],
        "argv": ["id"],
        "env": {"A": "B"},
        "nonce": "abc",
        "compose": {},
        "expected_compose_sha256": "a" * 64,
        "cleanup_flags": {"force": True},
    }.items():
        with pytest.raises(mods.ProtocolError):
            mods.validate_request(
                {
                    **base,
                    "operation": "deploy_canary",
                    "plan_id": fix.plan["plan_id"],
                    "plan_sha256": fix.plan["plan_sha256"],
                    "approval_id": fix.approval["approval_id"],
                    field: value,
                }
            )


def test_canonical_compose_is_broker_owned_and_restricted(tmp_path):
    mods = _load_modules()
    policy = mods.CanaryPolicy(workdir=tmp_path / "c")
    compose = mods.canonical_compose(policy)
    service = compose["services"][policy.service]
    assert compose["name"] == "dockerpilot-secure-canary"
    assert service["image"] == policy.image
    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]
    assert service["cap_add"] == []
    assert service["ports"] == [{"target": 80, "published": 18080, "protocol": "tcp", "host_ip": "127.0.0.1"}]
    assert service["pids_limit"] == 128
    assert service["mem_limit"] == "128m"
    assert service["cpus"] == "0.25"
    assert "volumes" not in service
    assert "devices" not in service
    assert "privileged" not in service
    assert "network_mode" not in service
    assert "secrets" not in compose


def test_invalid_tag_only_digest_and_live_placeholder_rejected(tmp_path):
    mods = _load_modules()
    with pytest.raises(mods.VerificationError, match="digest"):
        mods.CanaryPolicy(workdir=tmp_path / "tag", image="docker.io/library/nginx:latest")
    with pytest.raises(mods.VerificationError, match="digest"):
        mods.CanaryPolicy(workdir=tmp_path / "bad", image="docker.io/library/nginx@sha256:nothex")
    with pytest.raises(mods.VerificationError, match="placeholder"):
        mods.CanaryPolicy(workdir=tmp_path / "live", live_mode=True)


def test_admission_loads_broker_owned_bundle_and_binds_actor_ttl_nonce_audit(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([])
    manager = _manager(fix, runner)
    admitted = _admit(manager, fix)
    record = manager.ledger.get(admitted["execution_id"])
    assert record["state"] == "approved"
    assert record["plan_sha256"] == fix.plan["plan_sha256"]
    assert record["approval_id"] == fix.approval["approval_id"]
    assert record["approval_nonce_hash"] != fix.approval["nonce"]
    audit_text = (fix.policy.workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert fix.approval["nonce"] not in audit_text
    assert "secret" not in audit_text.lower()

    bad_actor = {**fix.approval, "approval_id": "appr_" + "c" * 24, "actor": "mallory"}
    with pytest.raises(fix.mods.VerificationError, match="actor"):
        _admit(manager, fix, approval=bad_actor, bundle_sha=_bundle_sha(manager, fix, approval=bad_actor))

    expired = {**fix.approval, "approval_id": "appr_" + "b" * 24, "expires_at": (fix.now() - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}
    with pytest.raises(fix.mods.VerificationError, match="expired"):
        _admit(manager, fix, approval=expired, bundle_sha=_bundle_sha(manager, fix, approval=expired))


def test_broker_dry_run_stages_canary_admission_bundle_for_closed_admit(tmp_path):
    fix = _fixture(tmp_path)
    mods = fix.mods
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer

    manager = _manager(fix, FakeRunner([]))
    server = BrokerServer(
        BrokerRuntimeConfig(socket_path=None, dozeyguard=fix.dg_config, expected_peer_uid=os.getuid()),
        run_dozeyguard=None,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
        canary_manager=manager,
    )
    base = {
        "protocol_version": 1,
        "request_id": "breq_stage_bundle",
        "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
    }

    dry = server._dispatch(
        {
            **base,
            "operation": "dry_run",
            "plan": fix.plan,
            "approval": fix.approval,
        }
    )

    mods.validate_response(dry)
    bundle_sha = dry["verification"]["canary_admission_bundle_sha256"]
    assert len(bundle_sha) == 64
    bundle = manager.ledger.load_admission_bundle(bundle_sha)
    assert bundle["plan"]["plan_id"] == fix.plan["plan_id"]
    assert bundle["approval"]["approval_id"] == fix.approval["approval_id"]

    admit = server._dispatch(
        {
            **base,
            "request_id": "breq_admit_bundle",
            "operation": "admit_canary_execution",
            "template_id": mods.TEMPLATE_ID,
            "plan_id": fix.plan["plan_id"],
            "plan_sha256": fix.plan["plan_sha256"],
            "approval_id": fix.approval["approval_id"],
            "admission_bundle_sha256": bundle_sha,
        }
    )
    mods.validate_response(admit)
    assert admit["ok"] is True
    assert admit["canary_admission"]["status"] == "approved"


def test_failed_dry_run_creates_no_bundle(tmp_path):
    fix = _fixture(tmp_path)
    mods = fix.mods
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer

    manager = _manager(fix, FakeRunner([]))
    server = BrokerServer(
        BrokerRuntimeConfig(socket_path=None, dozeyguard=fix.dg_config, expected_peer_uid=os.getuid()),
        run_dozeyguard=None,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
        canary_manager=manager,
    )
    bad_plan = json.loads(json.dumps(fix.plan))
    bad_plan["plan_sha256"] = "0" * 64
    with pytest.raises(mods.VerificationError, match="plan_sha256"):
        server._dispatch(
            {
                "protocol_version": 1,
                "request_id": "breq_bad_dry",
                "operation": "dry_run",
                "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
                "plan": bad_plan,
                "approval": {**fix.approval, "plan_sha256": "0" * 64},
            }
        )
    assert not list(manager.ledger.bundles_dir.glob("*.json"))
    assert not list(manager.ledger.executions_dir.glob("*.json"))


def test_identical_dry_run_is_idempotent_and_conflict_does_not_overwrite(tmp_path):
    fix = _fixture(tmp_path)
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer

    manager = _manager(fix, FakeRunner([]))
    server = BrokerServer(
        BrokerRuntimeConfig(socket_path=None, dozeyguard=fix.dg_config, expected_peer_uid=os.getuid()),
        run_dozeyguard=None,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
        canary_manager=manager,
    )
    req = {
        "protocol_version": 1,
        "request_id": "breq_same_dry",
        "operation": "dry_run",
        "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
        "plan": fix.plan,
        "approval": fix.approval,
    }
    first = server._dispatch(req)["verification"]["canary_admission_bundle_sha256"]
    second = server._dispatch({**req, "request_id": "breq_same_dry2"})["verification"]["canary_admission_bundle_sha256"]
    original_bundle = manager.ledger.load_admission_bundle(first)
    changed_approval = {**fix.approval, "session_id_hash": "f" * 64}
    third = server._dispatch({**req, "request_id": "breq_same_dry3", "approval": changed_approval})["verification"]["canary_admission_bundle_sha256"]
    assert first == second == third
    assert len(list(manager.ledger.bundles_dir.glob("*.json"))) == 1
    assert len(list(manager.ledger.executions_dir.glob("*.json"))) == 1
    assert manager.ledger.load_admission_bundle(first) == original_bundle


def test_bundle_binding_unknown_tampered_cross_plan_approval_and_expired_rejected(tmp_path):
    fix = _fixture(tmp_path)
    manager = _manager(fix, FakeRunner([]))
    with pytest.raises(fix.mods.VerificationError, match="missing"):
        manager.admit(
            plan_id=fix.plan["plan_id"],
            plan_sha256=fix.plan["plan_sha256"],
            approval_id=fix.approval["approval_id"],
            admission_bundle_sha256="e" * 64,
            template_id=fix.mods.TEMPLATE_ID,
            dozeyguard_config=fix.dg_config,
            run_dozeyguard=fix.run_dg,
            normalize_spec_to_compose=fix.normalize,
            plan_firewall_actions=fix.firewall,
        )

    bundle_sha = _bundle_sha(manager, fix)
    bundle_path = manager.ledger.bundle_path(bundle_sha)
    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["approval_id"] = "appr_" + "d" * 24
    bundle_path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(fix.mods.VerificationError, match="hash"):
        _admit(manager, fix, bundle_sha=bundle_sha)

    cross_plan = _fixture(tmp_path / "cross-plan")
    cross_manager = _manager(cross_plan, FakeRunner([]))
    cross_bundle = _bundle_sha(cross_manager, cross_plan)
    with pytest.raises(cross_plan.mods.VerificationError, match="bundle"):
        cross_manager.admit(
            plan_id=fix.plan["plan_id"],
            plan_sha256=fix.plan["plan_sha256"],
            approval_id=cross_plan.approval["approval_id"],
            admission_bundle_sha256=cross_bundle,
            template_id=cross_plan.mods.TEMPLATE_ID,
            dozeyguard_config=cross_plan.dg_config,
            run_dozeyguard=cross_plan.run_dg,
            normalize_spec_to_compose=cross_plan.normalize,
            plan_firewall_actions=cross_plan.firewall,
        )
    with pytest.raises(cross_plan.mods.VerificationError, match="bundle"):
        cross_manager.admit(
            plan_id=cross_plan.plan["plan_id"],
            plan_sha256=cross_plan.plan["plan_sha256"],
            approval_id=fix.approval["approval_id"],
            admission_bundle_sha256=cross_bundle,
            template_id=cross_plan.mods.TEMPLATE_ID,
            dozeyguard_config=cross_plan.dg_config,
            run_dozeyguard=cross_plan.run_dg,
            normalize_spec_to_compose=cross_plan.normalize,
            plan_firewall_actions=cross_plan.firewall,
        )

    expired_fix = _fixture(tmp_path / "expired-bundle")
    expired_manager = _manager(expired_fix, FakeRunner([]))
    past = (expired_fix.now() - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    expired_bundle = _bundle_sha(expired_manager, expired_fix, staged_bundle_expires_at=past)
    with pytest.raises(expired_fix.mods.VerificationError, match="expired"):
        _admit(expired_manager, expired_fix, bundle_sha=expired_bundle)


def test_wrong_plan_hash_and_duplicate_nonce_rejected(tmp_path):
    fix = _fixture(tmp_path)
    manager = _manager(fix, FakeRunner([]))
    wrong_plan = json.loads(json.dumps(fix.plan))
    wrong_plan["plan_sha256"] = "0" * 64
    wrong_approval = {**fix.approval, "plan_sha256": "0" * 64}
    bundle = _bundle_sha(manager, fix, plan=wrong_plan, approval=wrong_approval)
    with pytest.raises(fix.mods.VerificationError, match="plan_sha256"):
        manager.admit(
            plan_id=wrong_plan["plan_id"],
            plan_sha256=wrong_plan["plan_sha256"],
            approval_id=wrong_approval["approval_id"],
            admission_bundle_sha256=bundle,
            template_id=fix.mods.TEMPLATE_ID,
            dozeyguard_config=fix.dg_config,
            run_dozeyguard=fix.run_dg,
            normalize_spec_to_compose=fix.normalize,
            plan_firewall_actions=fix.firewall,
        )

    _admit(manager, fix)
    second = _fixture(tmp_path / "second")
    second.approval["nonce"] = fix.approval["nonce"]
    second_manager = _manager(second, FakeRunner([]))
    # Share nonce ledger with the first manager.
    second_manager.ledger = manager.ledger
    with pytest.raises(second.mods.VerificationError, match="nonce"):
        _admit(second_manager, second, bundle_sha=_bundle_sha(second_manager, second, approval=second.approval))


def test_deploy_scans_final_bytes_exact_argv_health_and_success_cleanup(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "", ""), (0, _ps(fix.policy), ""), (0, "down", "")])
    manager = _manager(fix, runner)
    _admit(manager, fix)
    seen = {}

    def scan_bytes(raw, config):
        seen["raw"] = raw
        return fix.run_dg_bytes(raw, config)

    result = manager.deploy(
        plan_id=fix.plan["plan_id"],
        plan_sha256=fix.plan["plan_sha256"],
        approval_id=fix.approval["approval_id"],
        run_dozeyguard_bytes=scan_bytes,
        dozeyguard_config=fix.dg_config,
    )
    assert result["status"] == "pass"
    assert result["state"] == fix.mods.CONSUMED
    assert seen["raw"] == fix.mods.read_compose_bytes(fix.policy.workdir)
    assert [call["argv"] for call in runner.calls] == [
        fix.mods.up_argv(fix.policy),
        ["docker", "compose", "-p", "dockerpilot-secure-canary", "-f", "compose.yaml", "ps", "--format", "json", "web"],
        fix.mods.down_argv(fix.policy),
    ]
    assert all(call["cwd"] == fix.policy.workdir for call in runner.calls)
    assert all(call["env"] == fix.mods.controlled_env() for call in runner.calls)
    replay = _deploy(manager, fix)
    assert replay["replay"] is True
    assert len(runner.calls) == 3


def test_deploy_rechecks_approval_expiry_before_docker_commands(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([])
    manager = _manager(fix, runner)
    admitted = _admit(manager, fix)
    record = manager.ledger.get(admitted["execution_id"])
    expired_at = (fix.now() - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    manager.ledger.replace({**record, "approval_expires_at": expired_at})

    with pytest.raises(fix.mods.VerificationError, match="expired"):
        _deploy(manager, fix)

    expired = manager.ledger.get(admitted["execution_id"])
    assert expired["state"] == "expired"
    assert runner.calls == []
    audit_text = (fix.policy.workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert "canary_expired" in audit_text


def test_revoke_before_admit_blocks_admit_and_is_idempotent(tmp_path):
    fix = _fixture(tmp_path)
    manager = _manager(fix, FakeRunner([]))
    bundle_sha = _bundle_sha(manager, fix)
    first = manager.revoke_admission(
        plan_id=fix.plan["plan_id"],
        plan_sha256=fix.plan["plan_sha256"],
        approval_id=fix.approval["approval_id"],
        admission_bundle_sha256=bundle_sha,
    )
    second = manager.revoke_admission(
        plan_id=fix.plan["plan_id"],
        plan_sha256=fix.plan["plan_sha256"],
        approval_id=fix.approval["approval_id"],
        admission_bundle_sha256=bundle_sha,
    )
    assert first["state"] == "revoked"
    assert second["state"] == "revoked"
    assert second["replay"] is True
    with pytest.raises(fix.mods.VerificationError, match="revoked"):
        _admit(manager, fix, bundle_sha=bundle_sha)
    audit_text = (fix.policy.workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert "canary_admission_revoked" in audit_text
    assert fix.approval["nonce"] not in audit_text


def test_revoke_after_admit_blocks_direct_deploy_before_docker_commands(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([])
    manager = _manager(fix, runner)
    bundle_sha = _bundle_sha(manager, fix)
    _admit(manager, fix, bundle_sha=bundle_sha)
    revoked = manager.revoke_admission(
        plan_id=fix.plan["plan_id"],
        plan_sha256=fix.plan["plan_sha256"],
        approval_id=fix.approval["approval_id"],
        admission_bundle_sha256=bundle_sha,
    )
    assert revoked["state"] == "revoked"
    with pytest.raises(fix.mods.VerificationError, match="revoked"):
        _deploy(manager, fix)
    assert runner.calls == []


def test_revoke_unknown_and_executing_fail_closed(tmp_path):
    fix = _fixture(tmp_path)
    manager = _manager(fix, FakeRunner([]))
    bundle_without_record = _bundle_sha(manager, fix, save_staged=False)
    with pytest.raises(fix.mods.VerificationError, match="not admitted|missing"):
        manager.revoke_admission(
            plan_id=fix.plan["plan_id"],
            plan_sha256=fix.plan["plan_sha256"],
            approval_id=fix.approval["approval_id"],
            admission_bundle_sha256=bundle_without_record,
        )

    executing_fix = _fixture(tmp_path / "executing")
    executing_manager = _manager(executing_fix, FakeRunner([]))
    executing_bundle = _bundle_sha(executing_manager, executing_fix)
    admitted = _admit(executing_manager, executing_fix, bundle_sha=executing_bundle)
    record = executing_manager.ledger.get(admitted["execution_id"])
    executing_manager.ledger.replace({**record, "state": "executing"})
    with pytest.raises(executing_fix.mods.VerificationError, match="executing"):
        executing_manager.revoke_admission(
            plan_id=executing_fix.plan["plan_id"],
            plan_sha256=executing_fix.plan["plan_sha256"],
            approval_id=executing_fix.approval["approval_id"],
            admission_bundle_sha256=executing_bundle,
        )


def test_tamper_after_dozeyguard_before_up_fails_before_up(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "cleanup", "")])

    def tamper():
        (fix.policy.workdir / "compose.yaml").write_text("tampered\n", encoding="utf-8")

    manager = _manager(fix, runner, pre_up_hook=tamper)
    _admit(manager, fix)
    with pytest.raises(fix.mods.VerificationError, match="changed before apply"):
        _deploy(manager, fix)
    assert [call["argv"] for call in runner.calls] == [fix.mods.down_argv(fix.policy)]


def test_command_runner_uses_popen_shell_false_devnull_close_fds_and_bounds_output(monkeypatch, tmp_path):
    mods = _load_modules()
    seen: dict[str, Any] = {}

    class Proc:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.stdout = io.BytesIO(b"x" * 20)
            self.stderr = io.BytesIO(b"e" * 20)

        def wait(self, timeout):
            seen["timeout"] = timeout
            return 0

        def kill(self):
            seen["killed"] = True

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        return Proc(**kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    res = mods.CommandRunner(output_limit=5).run(["docker", "compose", "ps"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, timeout=7)
    assert seen["shell"] is False
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["close_fds"] is True
    assert seen["cwd"] == str(tmp_path)
    assert seen["env"] == {"PATH": "/usr/bin:/bin"}
    assert seen["timeout"] == 7
    assert res.stdout == "xxxxx"
    assert res.stderr == "eeeee"
    assert res.stdout_truncated is True
    assert res.stderr_truncated is True


def test_command_timeout_is_reported(tmp_path):
    mods = _load_modules()
    res = mods.CommandRunner(output_limit=64).run(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=1,
    )
    assert res.timed_out is True


def test_occupied_port_fails_before_cas_or_docker_command(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([])
    manager = _manager(fix, runner, port_free=False)
    admitted = _admit(manager, fix)
    with pytest.raises(fix.mods.VerificationError, match="port"):
        _deploy(manager, fix)
    assert manager.ledger.get(admitted["execution_id"])["state"] == "approved"
    assert runner.calls == []


def test_hash_drift_dozeyguard_failure_and_up_nonzero_keep_primary_error(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "cleanup", "")])
    manager = _manager(fix, runner)
    admitted = _admit(manager, fix)
    record = manager.ledger.get(admitted["execution_id"])
    manager.ledger.replace({**record, "compose_sha256": "0" * 64})
    with pytest.raises(fix.mods.VerificationError, match="hash"):
        _deploy(manager, fix)
    failed = manager.ledger.get(admitted["execution_id"])
    assert failed["state"] == fix.mods.FAILED_CLEANUP_OK
    assert failed["primary_error"]["code"] == "canary_compose_hash"

    bad = _fixture(tmp_path / "bad-dg")
    bad_manager = _manager(bad, FakeRunner([(0, "cleanup", "")]))
    bad_admitted = _admit(bad_manager, bad)
    with pytest.raises(bad.mods.VerificationError, match="Dozeyguard"):
        bad_manager.deploy(
            plan_id=bad.plan["plan_id"],
            plan_sha256=bad.plan["plan_sha256"],
            approval_id=bad.approval["approval_id"],
            run_dozeyguard_bytes=lambda _raw, _config: {"summary": {"blocking": 1}, "result": {"exit_code": 2}},
            dozeyguard_config=bad.dg_config,
        )
    assert bad_manager.ledger.get(bad_admitted["execution_id"])["primary_error"]["code"] == "canary_dozeyguard"

    up_fail = _fixture(tmp_path / "up-fail")
    up_runner = FakeRunner([(42, "", "up failed"), (0, "cleanup", "")])
    up_manager = _manager(up_fail, up_runner)
    up_admitted = _admit(up_manager, up_fail)
    with pytest.raises(up_fail.mods.VerificationError, match="compose up"):
        _deploy(up_manager, up_fail)
    assert up_manager.ledger.get(up_admitted["execution_id"])["primary_error"]["code"] == "canary_compose_up"


def test_health_retries_identity_unhealthy_http_timeout_oversized_and_cleanup_failures(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "", ""), (0, _ps(fix.policy, state="exited"), ""), (0, _ps(fix.policy), ""), (0, "", "")])
    manager = _manager(fix, runner)
    _admit(manager, fix)
    result = _deploy(manager, fix)
    assert result["state"] == fix.mods.CONSUMED

    def short_fixture(path):
        policy = fix.mods.CanaryPolicy(
            workdir=path / "canary",
            health_timeout_seconds=1,
            health_poll_interval_seconds=0.1,
        )
        return _fixture(path, policy=policy)

    wrong_identity = short_fixture(tmp_path / "wrong-identity")
    wrong_runner = FakeRunner([(0, "", ""), (0, _ps(wrong_identity.policy, service="other"), ""), (0, "", "")])
    wrong_manager = _manager(wrong_identity, wrong_runner)
    wrong_admitted = _admit(wrong_manager, wrong_identity)
    with pytest.raises(wrong_identity.mods.VerificationError, match="identity"):
        _deploy(wrong_manager, wrong_identity)
    wrong_record = wrong_manager.ledger.get(wrong_admitted["execution_id"])
    assert wrong_record["primary_error"]["code"] == "canary_identity"
    assert wrong_record["cleanup"]["ok"] is True

    unhealthy = short_fixture(tmp_path / "unhealthy")
    unhealthy_runner = FakeRunner(
        [(0, "", "")]
        + [(0, _ps(unhealthy.policy, health="unhealthy"), "")] * 4
        + [(0, "", "")]
    )
    unhealthy_manager = _manager(unhealthy, unhealthy_runner)
    _admit(unhealthy_manager, unhealthy)
    with pytest.raises(unhealthy.mods.VerificationError, match="health"):
        _deploy(unhealthy_manager, unhealthy)

    timeout = short_fixture(tmp_path / "http-timeout")
    timeout_runner = FakeRunner([(0, "", "")] + [(0, _ps(timeout.policy), "")] * 4 + [(0, "", "")])
    timeout_manager = _manager(timeout, timeout_runner, http=TimeoutError("slow"))
    timeout_manager.http_probe = lambda *_args: (_ for _ in ()).throw(TimeoutError("slow"))
    _admit(timeout_manager, timeout)
    with pytest.raises(timeout.mods.VerificationError, match="health"):
        _deploy(timeout_manager, timeout)

    oversized = short_fixture(tmp_path / "oversized")
    oversized_runner = FakeRunner([(0, "", "")] + [(0, _ps(oversized.policy), "")] * 4 + [(0, "", "")])
    oversized_manager = _manager(oversized, oversized_runner, http=(200, "x" * 5000))
    _admit(oversized_manager, oversized)
    with pytest.raises(oversized.mods.VerificationError, match="health"):
        _deploy(oversized_manager, oversized)

    cleanup_fail = _fixture(tmp_path / "cleanup")
    cleanup_runner = FakeRunner([(0, "", ""), (0, _ps(cleanup_fail.policy), ""), (1, "", "down failed")])
    cleanup_manager = _manager(cleanup_fail, cleanup_runner)
    cleanup_admitted = _admit(cleanup_manager, cleanup_fail)
    cleanup_result = _deploy(cleanup_manager, cleanup_fail)
    assert cleanup_result["state"] == cleanup_fail.mods.FAILED_CLEANUP_FAILED
    assert cleanup_manager.ledger.get(cleanup_admitted["execution_id"])["cleanup"]["ok"] is False


def test_remove_unknown_and_already_removed_are_safe(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "", ""), (0, _ps(fix.policy), ""), (0, "", ""), (0, "", ""), (0, "", "")])
    manager = _manager(fix, runner)
    with pytest.raises(fix.mods.VerificationError, match="not found"):
        manager.remove(execution_id="exec_" + "f" * 24)
    admitted = _admit(manager, fix)
    _deploy(manager, fix)
    first = manager.remove(execution_id=admitted["execution_id"])
    second = manager.remove(execution_id=admitted["execution_id"])
    assert first["state"] == fix.mods.REMOVED
    assert second["state"] == fix.mods.REMOVED
    assert first["project"] == "dockerpilot-secure-canary"


def test_ledger_corruption_and_filesystem_symlinks_fail_closed(tmp_path):
    mods = _load_modules()
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "surprise.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(mods.VerificationError, match="unexpected"):
        mods.CanaryLedger(dirty).prepare_dirs()

    symlink_dir = tmp_path / "linkdir"
    symlink_dir.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(mods.VerificationError, match="symlink"):
        mods.CanaryLedger(symlink_dir).prepare_dirs()

    compose_link_root = tmp_path / "compose-link"
    compose_link_root.mkdir()
    (compose_link_root / "compose.yaml").symlink_to(tmp_path / "target.yaml")
    with pytest.raises(mods.VerificationError, match="symlink"):
        mods.CanaryLedger(compose_link_root).prepare_dirs()

    fix = _fixture(tmp_path / "corrupt")
    manager = _manager(fix, FakeRunner([]))
    admitted = _admit(manager, fix)
    (fix.policy.workdir / "executions" / f"{admitted['execution_id']}.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(mods.VerificationError, match="ledger"):
        manager.ledger.get(admitted["execution_id"])


def test_concurrent_double_deploy_runs_command_runner_once(tmp_path):
    fix = _fixture(tmp_path)
    runner = FakeRunner([(0, "", ""), (0, _ps(fix.policy), ""), (0, "", "")])
    manager = _manager(fix, runner)
    _admit(manager, fix)
    results = []
    errors = []

    def worker():
        try:
            results.append(_deploy(manager, fix))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(results) == 2
    assert sum(1 for call in runner.calls if call["argv"] == fix.mods.up_argv(fix.policy)) == 1


def test_audit_secret_redaction_control_chars_and_symlink_refusal(tmp_path):
    fix = _fixture(tmp_path)
    manager = _manager(fix, FakeRunner([]))
    audit_id = manager.ledger.append_audit({"event": "x\nbad", "api_token": "SUPER_SECRET", "message": "a\r\nb"})
    text = (fix.policy.workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert audit_id.startswith("audit_")
    assert "SUPER_SECRET" not in text
    assert "x\\nbad" not in text
    link_root = tmp_path / "audit-link"
    link_root.mkdir()
    (link_root / "audit.jsonl").symlink_to(tmp_path / "target")
    with pytest.raises(fix.mods.VerificationError, match="symlink refused"):
        fix.mods.CanaryLedger(link_root).append_audit({"event": "bad"})


def test_broker_client_payloads_do_not_include_docker_overrides(monkeypatch):
    _prefer_local_paths()
    from backend.secure_deploy.broker_client import BrokerClient

    captured = []

    def fake_send(_sock, payload):
        captured.append(payload)

    monkeypatch.setattr("backend.secure_deploy.broker_client.send_message", fake_send)
    monkeypatch.setattr(
        "backend.secure_deploy.broker_client.recv_message",
        lambda _sock, timeout: {
            "protocol_version": 1,
            "request_id": captured[-1]["request_id"],
            "operation": captured[-1]["operation"],
            "ok": True,
            "canary_admission": {},
            "canary_result": {},
        },
    )
    monkeypatch.setattr("backend.secure_deploy.broker_client.validate_response", lambda resp: resp)

    class Sock:
        def settimeout(self, timeout):
            self.timeout = timeout

        def close(self):
            return None

    client = BrokerClient("/tmp/not-used")
    monkeypatch.setattr(client, "_connect", lambda: Sock())
    plan = {"plan_id": "plan_aaaaaaaaaaaaaaaaaaaaaaaa", "plan_sha256": "a" * 64}
    approval = {"approval_id": "appr_bbbbbbbbbbbbbbbbbbbbbbbb"}
    with pytest.raises(Exception, match="unsupported broker request field"):
        client.request("deploy_canary", project="not-allowed")
    client.admit_canary_execution(
        plan_id=plan["plan_id"],
        plan_sha256=plan["plan_sha256"],
        approval_id=approval["approval_id"],
        admission_bundle_sha256="d" * 64,
    )
    client.revoke_canary_admission(
        plan_id=plan["plan_id"],
        plan_sha256=plan["plan_sha256"],
        approval_id=approval["approval_id"],
        admission_bundle_sha256="d" * 64,
    )
    client.deploy_canary(plan_id=plan["plan_id"], plan_sha256=plan["plan_sha256"], approval_id=approval["approval_id"])
    client.remove_canary(canary_execution_id="exec_" + "c" * 24)
    forbidden = {"project", "service", "workdir", "image", "port", "health_timeout_seconds", "command", "args", "argv", "env", "compose", "cleanup", "plan", "approval", "nonce"}
    assert all(not (forbidden & set(payload)) for payload in captured)
    assert captured[0]["template_id"] == "dockerpilot-secure-canary-v1"
    assert set(captured[0]) == {
        "protocol_version",
        "request_id",
        "operation",
        "client",
        "plan_id",
        "plan_sha256",
        "approval_id",
        "admission_bundle_sha256",
        "template_id",
    }
    assert captured[1]["operation"] == "revoke_canary_admission"
    assert set(captured[1]) == {
        "protocol_version",
        "request_id",
        "operation",
        "client",
        "plan_id",
        "plan_sha256",
        "approval_id",
        "admission_bundle_sha256",
    }
    assert "template_id" not in captured[1]
    assert captured[2]["operation"] == "deploy_canary"
    assert set(captured[2]) == {"protocol_version", "request_id", "operation", "client", "plan_id", "plan_sha256", "approval_id"}
    assert set(captured[3]) == {"protocol_version", "request_id", "operation", "client", "canary_execution_id"}


def test_broker_server_dispatches_closed_canary_operations_and_legacy_regression(tmp_path):
    fix = _fixture(tmp_path)
    mods = fix.mods
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer

    class FakeManager:
        def admit(self, **kwargs):
            assert "plan" not in kwargs and "approval" not in kwargs
            assert kwargs["admission_bundle_sha256"] == "d" * 64
            return {"status": "approved", "execution_id": "exec_" + "a" * 24, "template_id": mods.TEMPLATE_ID, "audit_id": "audit_" + "b" * 24}

        def deploy(self, **kwargs):
            assert "run_dozeyguard_bytes" in kwargs
            return {"status": "pass", "state": "consumed", "replay": False, "execution_id": "exec_" + "a" * 24, "template_id": mods.TEMPLATE_ID, "project": "dockerpilot-secure-canary", "service": "web"}

        def revoke_admission(self, **kwargs):
            assert "template_id" not in kwargs
            assert kwargs["admission_bundle_sha256"] == "d" * 64
            return {"status": "pass", "state": "revoked", "replay": False, "execution_id": "exec_" + "a" * 24, "template_id": mods.TEMPLATE_ID, "project": "dockerpilot-secure-canary", "service": "web"}

        def remove(self, **kwargs):
            assert kwargs["execution_id"] == "exec_" + "a" * 24
            return {"status": "pass", "state": "removed", "replay": False, "execution_id": "exec_" + "a" * 24, "template_id": mods.TEMPLATE_ID, "project": "dockerpilot-secure-canary", "service": "web"}

    server = BrokerServer(
        BrokerRuntimeConfig(socket_path=None, dozeyguard=fix.dg_config, expected_peer_uid=os.getuid()),
        run_dozeyguard=None,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
        canary_manager=FakeManager(),
    )
    base = {"protocol_version": 1, "request_id": "breq_dispatch1", "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"}}
    for resp in (
        server._dispatch({**base, "operation": "ping"}),
        server._dispatch({**base, "operation": "capabilities"}),
    ):
        mods.validate_response(resp)
        assert resp["ok"] is True
    caps = server._dispatch({**base, "operation": "capabilities"})["capabilities"]
    assert caps["apply_supported"] is False
    assert caps["canary_supported"] is True
    assert caps["canary_revocation_supported"] is True
    for resp in [
        server._dispatch({**base, "operation": "admit_canary_execution", "template_id": mods.TEMPLATE_ID, "plan_id": fix.plan["plan_id"], "plan_sha256": fix.plan["plan_sha256"], "approval_id": fix.approval["approval_id"], "admission_bundle_sha256": "d" * 64}),
        server._dispatch({**base, "operation": "revoke_canary_admission", "plan_id": fix.plan["plan_id"], "plan_sha256": fix.plan["plan_sha256"], "approval_id": fix.approval["approval_id"], "admission_bundle_sha256": "d" * 64}),
        server._dispatch({**base, "operation": "deploy_canary", "plan_id": fix.plan["plan_id"], "plan_sha256": fix.plan["plan_sha256"], "approval_id": fix.approval["approval_id"]}),
        server._dispatch({**base, "operation": "remove_canary", "canary_execution_id": "exec_" + "a" * 24}),
    ]:
        mods.validate_response(resp)
        assert resp["ok"] is True


def test_old_preview_allowed_operations_do_not_enable_canary_or_revoke(tmp_path):
    fix = _fixture(tmp_path)
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer

    server = BrokerServer(
        BrokerRuntimeConfig(
            socket_path=None,
            dozeyguard=fix.dg_config,
            expected_peer_uid=os.getuid(),
            allowed_operations=frozenset({"ping", "capabilities", "verify_plan", "dry_run"}),
        ),
        run_dozeyguard=None,
        normalize_spec_to_compose=fix.normalize,
        plan_firewall_actions=fix.firewall,
    )
    caps = server._dispatch(
        {
            "protocol_version": 1,
            "request_id": "breq_old_caps1",
            "operation": "capabilities",
            "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
        }
    )["capabilities"]
    assert caps["apply_supported"] is False
    assert caps["canary_supported"] is False
    assert caps["canary_revocation_supported"] is False
    assert "revoke_canary_admission" not in caps["operations"]


def test_extras_secure_deploy_canary_routes_do_not_call_docker(tmp_path):
    py_files = [
        ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "broker_client.py",
        ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "resources.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in py_files)
    assert "subprocess" not in text
    assert "docker compose" not in text
    assert "docker.from_env" not in text
