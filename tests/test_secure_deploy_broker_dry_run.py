"""Approval + broker dry-run canary tests (no root, no Docker, no firewall)."""

from __future__ import annotations

import importlib
import json
import os
import socket
import struct
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_restful")

ROOT = Path(__file__).resolve().parents[1]
FAKE_DG = ROOT / "tests" / "fixtures" / "secure_deploy_preview" / "fake_dozeyguard.py"
POLICY = ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "policy" / "preview.toml"
PASS_SPEC = ROOT / "tests" / "fixtures" / "secure_deploy" / "pass" / "minimal_production_localhost.json"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _totp(secret: str = TOTP_SECRET, now: float | None = None) -> str:
    import base64
    import hashlib
    import hmac as hm
    import struct as st

    secret_bytes = base64.b32decode(secret, casefold=True)
    counter = int((now if now is not None else time.time()) // 30)
    digest = hm.new(secret_bytes, st.pack(">Q", counter), hashlib.sha1).digest()
    idx = digest[-1] & 0x0F
    otp_int = st.unpack(">I", digest[idx : idx + 4])[0] & 0x7FFFFFFF
    return str(otp_int % 1_000_000).zfill(6)


def _load_app(monkeypatch, tmp_path, *, auth=True, totp=True, broker_socket: str | None = None, trusted_client: bool = True):
    extras = ROOT / "DockerPilotExtras"
    if str(extras) not in sys.path:
        sys.path.insert(0, str(extras))
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))

    monkeypatch.setenv("WEB_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("WEB_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "test-pass")
    monkeypatch.setenv("WEB_AUTH_TOTP_SECRET", TOTP_SECRET if totp else "")
    monkeypatch.setenv("SECURE_DEPLOY_STORE_ROOT", str(tmp_path / "sdstore"))
    monkeypatch.setenv("DOZEYGUARD_BIN", str(FAKE_DG))
    monkeypatch.setenv("DOZEYGUARD_POLICY_PATH", str(POLICY))
    monkeypatch.setenv("BROKER_DOZEYGUARD_BIN", str(FAKE_DG))
    monkeypatch.setenv("BROKER_DOZEYGUARD_POLICY_PATH", str(POLICY))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("SECURE_DEPLOY_TRUSTED_CLIENT", "true" if trusted_client else "false")
    if broker_socket:
        monkeypatch.setenv("SECURE_DEPLOY_BROKER_SOCKET", broker_socket)
    else:
        monkeypatch.delenv("SECURE_DEPLOY_BROKER_SOCKET", raising=False)

    for name in list(sys.modules):
        if name == "backend.app" or name.startswith("backend.secure_deploy") or name == "backend.api":
            sys.modules.pop(name, None)
        if name.startswith("dockerpilot.secure_deploy_broker"):
            sys.modules.pop(name, None)
    return importlib.import_module("backend.app")


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, auth=True, totp=True)
    monkeypatch.setattr(module, "_verify_password", lambda _p: True)
    client = module.app.test_client()
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "x", "totp_code": _totp()},
    )
    assert resp.status_code == 200
    status = client.get("/api/auth/status").get_json()
    csrf = status.get("secure_deploy_csrf")
    assert csrf
    return module, client, csrf


def _headers(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"}


def _make_plan(service, actor="admin"):
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    return service.generate_plan(spec, actor, request_id="req_test")


def test_schema_mirrors_and_goldens_still_pass():
    import importlib.util

    path = ROOT / "tests" / "test_secure_deploy_contract.py"
    spec = importlib.util.spec_from_file_location("secure_deploy_contract_tests", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.test_schema_mirror_byte_equality()
    mod.test_fixed_golden_hashes()


def test_approval_step_up_and_state_machine(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.approval import ApprovalService
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig
    from backend.secure_deploy.errors import SecureDeployError
    from backend.secure_deploy.service import SecureDeployService
    from backend.secure_deploy.store import FileSecureDeployStore
    from backend.secure_deploy.step_up import StepUpTotpGuard

    store = FileSecureDeployStore(tmp_path / "s")
    svc = SecureDeployService(
        store,
        dozeyguard_config=DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
    )
    clock = {"t": datetime.now(timezone.utc)}

    def now():
        return clock["t"]

    approvals = ApprovalService(store, clock=now, ttl_minutes=10)
    env = _make_plan(svc)
    assert env["plan"].get("source_spec")
    assert env["plan"].get("contract_extension") == "v1.1"

    guard = StepUpTotpGuard(verify_fn=lambda s, c, w=1: True, max_attempts=3)
    assert guard.verify(actor_key="admin", secret=TOTP_SECRET, code="111111")
    assert guard.verify(actor_key="admin", secret=TOTP_SECRET, code="111111") is False  # replay

    record = approvals.approve_plan(svc.get_plan(env["plan"]["plan_id"]), actor="admin", session_hash="a" * 64)
    assert record["status"] == "approved"
    with pytest.raises(SecureDeployError):
        approvals.approve_plan(svc.get_plan(env["plan"]["plan_id"]), actor="admin", session_hash="a" * 64)

    consumed = approvals.consume(record["approval_id"], expected_plan_sha256=record["plan_sha256"])
    assert consumed["status"] == "consumed"
    with pytest.raises(SecureDeployError):
        approvals.revoke(record["approval_id"], actor="admin")

    # Expire path
    env2 = _make_plan(svc)
    record2 = approvals.approve_plan(svc.get_plan(env2["plan"]["plan_id"]), actor="admin", session_hash="b" * 64)
    clock["t"] = clock["t"] + timedelta(minutes=11)
    expired = approvals.get(record2["approval_id"])
    assert expired["status"] == "expired"


def test_http_approve_requires_step_up(authed_client):
    module, client, csrf = authed_client
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    plan = client.post("/api/secure-deploy/plan", json={"spec": spec}, headers=_headers(csrf))
    assert plan.status_code == 201
    body = plan.get_json()
    plan_id = body["plan"]["plan_id"]
    plan_sha = body["plan"]["plan_sha256"]

    bad = client.post(
        f"/api/secure-deploy/plans/{plan_id}/approve",
        json={"plan_sha256": plan_sha, "totp_code": "000000"},
        headers=_headers(csrf),
    )
    assert bad.status_code == 403

    # Need fresh code after failed attempt — generate valid
    ok = client.post(
        f"/api/secure-deploy/plans/{plan_id}/approve",
        json={"plan_sha256": plan_sha, "totp_code": _totp()},
        headers=_headers(csrf),
    )
    # May fail if rate limit from previous + same window reuse of generated code path
    if ok.status_code != 201:
        time.sleep(1)
        ok = client.post(
            f"/api/secure-deploy/plans/{plan_id}/approve",
            json={"plan_sha256": plan_sha, "totp_code": _totp()},
            headers=_headers(csrf),
        )
    assert ok.status_code == 201, ok.get_json()
    assert ok.get_json()["approval"]["status"] == "approved"


def test_broker_canary_dry_run(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.approval import ApprovalService
    from backend.secure_deploy.broker_client import BrokerClient
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from backend.secure_deploy.service import SecureDeployService
    from backend.secure_deploy.store import FileSecureDeployStore
    from dockerpilot.secure_deploy_broker.server import BrokerServer, build_canary_config

    sock_path = str(tmp_path / "broker.sock")
    store = FileSecureDeployStore(tmp_path / "s")
    svc = SecureDeployService(
        store,
        dozeyguard_config=DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
    )
    approvals = ApprovalService(store)
    env = _make_plan(svc)
    plan = env["plan"]
    stored = svc.get_plan(plan["plan_id"])
    approval = approvals.approve_plan(stored, actor="admin", session_hash="c" * 64)

    cfg = build_canary_config(sock_path, executable=str(FAKE_DG), policy_path=str(POLICY))
    server = BrokerServer(
        cfg,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    server.start()
    try:
        client = BrokerClient(sock_path)
        ping = client.request("ping")
        assert ping["ok"] is True
        caps = client.request("capabilities")
        assert caps["capabilities"]["apply_supported"] is False
        dry = client.dry_run(plan, approval)
        assert dry["ok"] is True
        assert dry["verification"]["status"] == "pass"
        assert dry["verification"]["dry_run"] is True

        # Forged PASS
        forged = json.loads(json.dumps(plan))
        forged["dozeyguard"]["result_sha256"] = "f" * 64
        forged["dozeyguard"]["exit_code"] = 0
        forged["dozeyguard"]["blocking_findings"] = 0
        from dockerpilot.secure_deploy import compute_plan_sha256

        forged["plan_sha256"] = compute_plan_sha256(forged)
        with pytest.raises(Exception):
            client.dry_run(forged, {**approval, "plan_sha256": forged["plan_sha256"]})
    finally:
        server.stop()
        assert not Path(sock_path).exists()


def test_broker_rejects_forbidden_ops_and_frames(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.protocol import (
        PROTOCOL_VERSION,
        encode_frame,
        sanitize_error_operation,
        validate_request,
        validate_response,
    )
    from dockerpilot.secure_deploy_broker.errors import ProtocolError

    with pytest.raises(ProtocolError):
        validate_request(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "breq_abcdefgh",
                "operation": "apply",
                "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
            }
        )
    with pytest.raises(ProtocolError):
        validate_request(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "breq_abcdefgh",
                "operation": "ping",
                "command": "id",
                "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
            }
        )
    huge = encode_frame({"ok": True})
    # oversized declared length
    bad = struct.pack("!I", 5_000_000) + b"{}"
    from dockerpilot.secure_deploy_broker.protocol import decode_frame

    with pytest.raises(ProtocolError):
        decode_frame(bad)

    assert sanitize_error_operation("dance") == "dance"
    assert sanitize_error_operation("apply") == "apply"
    assert sanitize_error_operation(None) == "unknown"
    assert sanitize_error_operation(123) == "unknown"
    assert sanitize_error_operation({"x": 1}) == "unknown"
    assert sanitize_error_operation("x" * 65) == "unknown"
    assert sanitize_error_operation("HAS_CAPS") == "unknown"
    # Error envelope with rejected op must validate against widened response schema.
    validate_response(
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "canary_dance_001",
            "operation": "dance",
            "ok": False,
            "error": {"code": "broker_operation_not_supported", "message": "operation dance is not supported"},
        }
    )


def test_broker_error_envelope_preserves_rejected_operation(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from dockerpilot.secure_deploy_broker.protocol import PROTOCOL_VERSION, recv_message, send_message
    from dockerpilot.secure_deploy_broker.server import BrokerServer, build_canary_config

    sock_path = str(tmp_path / "env.sock")
    cfg = build_canary_config(
        sock_path,
        executable=str(FAKE_DG),
        policy_path=str(POLICY),
        expected_peer_uid=os.getuid(),
    )
    server = BrokerServer(
        cfg,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    server.start()
    try:

        def raw_call(operation):
            req = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "breq_" + "a" * 12,
                "operation": operation,
                "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
            }
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(sock_path)
            try:
                send_message(sock, req)
                return recv_message(sock, timeout=5)
            finally:
                sock.close()

        dance = raw_call("dance")
        assert dance["ok"] is False
        assert dance["operation"] == "dance"
        assert dance["error"]["code"] == "broker_operation_not_supported"

        apply_resp = raw_call("apply")
        assert apply_resp["ok"] is False
        assert apply_resp["operation"] == "apply"
        assert apply_resp["error"]["code"] == "broker_operation_not_supported"

        missing = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "breq_" + "b" * 12,
            "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
        }
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(sock_path)
        send_message(sock, missing)
        missing_resp = recv_message(sock, timeout=5)
        sock.close()
        assert missing_resp["ok"] is False
        assert missing_resp["operation"] == "unknown"

        for bad_op in (123, {"op": "ping"}, "X" * 200):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(sock_path)
            send_message(
                sock,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "breq_" + "c" * 12,
                    "operation": bad_op,
                    "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
                },
            )
            bad_resp = recv_message(sock, timeout=5)
            sock.close()
            assert bad_resp["ok"] is False
            assert bad_resp["operation"] == "unknown"
            # Must not echo overlong payloads into the envelope.
            assert len(bad_resp["operation"]) <= 64

        ping = raw_call("ping")
        assert ping["ok"] is True
        assert ping["operation"] == "ping"
        caps = raw_call("capabilities")
        assert caps["ok"] is True
        assert caps["operation"] == "capabilities"
        assert caps["capabilities"]["apply_supported"] is False
    finally:
        server.stop()
        assert not Path(sock_path).exists()


def test_broker_module_has_no_docker_or_shell_true():
    root = ROOT / "src" / "dockerpilot" / "secure_deploy_broker"
    combined = ""
    for path in root.rglob("*.py"):
        combined += path.read_text(encoding="utf-8")
    assert "shell=True" not in combined
    assert "os.system" not in combined
    assert "docker.from_env" not in combined
    assert "/var/run/docker.sock" not in combined
    assert "iptables" not in combined
    assert "nftables" not in combined
    assert not any(
        line.strip().startswith("from docker ") or line.strip() == "import docker"
        for line in combined.splitlines()
    )


def test_frontend_source_guards_11d1():
    page = (ROOT / "DockerPilotExtras/frontend/src/pages/SecureDeploy.jsx").read_text(encoding="utf-8")
    api = (ROOT / "DockerPilotExtras/frontend/src/services/api.js").read_text(encoding="utf-8")
    assert "Verify with broker" in page
    assert "Approve plan" in page
    for banned in (">Apply<", "Deploy Now", "Execute", "/api/command/execute"):
        assert banned not in page
    assert "localStorage.setItem" not in page
    assert "sessionStorage.setItem" not in page
    assert "totp" not in api.lower() or "totp_code" in api
    assert "/secure-deploy/apply" not in api
    assert "broker-dry-run" in api


def test_production_origin_required(monkeypatch, tmp_path):
    monkeypatch.setenv("SECURE_DEPLOY_REQUIRE_ORIGIN", "true")
    module = _load_app(monkeypatch, tmp_path, auth=True, totp=True, trusted_client=False)
    monkeypatch.setattr(module, "_verify_password", lambda _p: True)
    client = module.app.test_client()
    client.post("/api/auth/login", json={"username": "admin", "password": "x", "totp_code": _totp()})
    csrf = client.get("/api/auth/status").get_json()["secure_deploy_csrf"]
    resp = client.post(
        "/api/secure-deploy/validate",
        json={"spec": {}},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "secure_deploy_csrf"
