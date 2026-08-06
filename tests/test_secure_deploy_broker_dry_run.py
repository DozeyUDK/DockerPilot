"""Approval + broker dry-run canary tests (no root, no Docker, no firewall)."""

from __future__ import annotations

import importlib
import json
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
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


def _prefer_local_paths() -> None:
    for path in (ROOT / "DockerPilotExtras", ROOT / "src"):
        text = str(path)
        while text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)


def _clear_secure_deploy_modules() -> None:
    _prefer_local_paths()
    for name in list(sys.modules):
        if name == "dockerpilot" or name.startswith("dockerpilot.secure_deploy"):
            sys.modules.pop(name, None)
        if name == "backend" or name.startswith("backend.secure_deploy"):
            sys.modules.pop(name, None)


def _allow_local_unix_socket_connects() -> None:
    import pytest_socket

    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)


_prefer_local_paths()


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
    _prefer_local_paths()
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


def _create_approved_plan(client, csrf: str):
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    plan_resp = client.post("/api/secure-deploy/plan", json={"spec": spec}, headers=_headers(csrf))
    assert plan_resp.status_code == 201, plan_resp.get_json()
    plan = plan_resp.get_json()["plan"]
    approve_resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/approve",
        json={"plan_sha256": plan["plan_sha256"], "totp_code": _totp()},
        headers=_headers(csrf),
    )
    if approve_resp.status_code != 201:
        time.sleep(1)
        approve_resp = client.post(
            f"/api/secure-deploy/plans/{plan['plan_id']}/approve",
            json={"plan_sha256": plan["plan_sha256"], "totp_code": _totp()},
            headers=_headers(csrf),
        )
    assert approve_resp.status_code == 201, approve_resp.get_json()
    return plan, approve_resp.get_json()["approval"]


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
    _clear_secure_deploy_modules()
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


def test_expired_approval_does_not_block_new_approval_without_get(tmp_path):
    _clear_secure_deploy_modules()
    from backend.secure_deploy.approval import ApprovalService
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig
    from backend.secure_deploy.service import SecureDeployService
    from backend.secure_deploy.store import FileSecureDeployStore

    store = FileSecureDeployStore(tmp_path / "s")
    svc = SecureDeployService(
        store,
        dozeyguard_config=DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
    )
    clock = {"t": datetime.now(timezone.utc)}
    approvals = ApprovalService(store, clock=lambda: clock["t"], ttl_minutes=10)
    env = _make_plan(svc)
    stored = svc.get_plan(env["plan"]["plan_id"])

    old = approvals.approve_plan(stored, actor="admin", session_hash="d" * 64)
    clock["t"] = clock["t"] + timedelta(minutes=11)
    new = approvals.approve_plan(stored, actor="admin", session_hash="e" * 64)

    assert old["status"] == "approved"
    assert new["status"] == "approved"
    assert new["approval_id"] != old["approval_id"]


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


def test_http_broker_dry_run_does_not_require_client_supplied_admission_bundle(authed_client, monkeypatch):
    module, client, csrf = authed_client
    plan, approval = _create_approved_plan(client, csrf)
    captured = {}

    def fake_dry_run(plan_arg, approval_arg):
        captured["plan_id"] = plan_arg["plan_id"]
        captured["approval_id"] = approval_arg["approval_id"]
        return {
            "verification": {
                "status": "pass",
                "broker_verification_sha256": "b" * 64,
                "plan_sha256": plan_arg["plan_sha256"],
                "dozeyguard_exit_code": 0,
                "blocking_findings": 0,
                "dry_run": True,
                "canary_admission_bundle_sha256": "c" * 64,
                "stages": [{"name": "independent_dozeyguard", "ok": True}],
            }
        }

    monkeypatch.setattr(module._secure_deploy_broker, "dry_run", fake_dry_run)
    resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/broker-dry-run",
        json={"approval_id": approval["approval_id"]},
        headers=_headers(csrf),
    )

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["verification"]["canary_admission_bundle_sha256"] == "c" * 64
    assert captured == {"plan_id": plan["plan_id"], "approval_id": approval["approval_id"]}


def test_http_canary_admit_passes_bundle_and_deploy_rejects_revoked_approval(authed_client, monkeypatch):
    module, client, csrf = authed_client
    plan, approval = _create_approved_plan(client, csrf)
    bundle_sha = "d" * 64
    captured = {}

    def fake_admit_canary_execution(**kwargs):
        captured["admit"] = kwargs
        return {
            "canary_admission": {
                "status": "approved",
                "execution_id": "exec_" + "a" * 24,
                "template_id": "dockerpilot-secure-canary-v1",
            }
        }

    deploy_calls = []

    def fake_deploy_canary(**kwargs):
        deploy_calls.append(kwargs)
        return {"canary_result": {"status": "pass"}}

    monkeypatch.setattr(module._secure_deploy_broker, "admit_canary_execution", fake_admit_canary_execution)
    monkeypatch.setattr(module._secure_deploy_broker, "deploy_canary", fake_deploy_canary)

    admit_resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/canary/admit",
        json={"approval_id": approval["approval_id"], "admission_bundle_sha256": bundle_sha},
        headers=_headers(csrf),
    )

    assert admit_resp.status_code == 200, admit_resp.get_json()
    assert captured["admit"]["admission_bundle_sha256"] == bundle_sha
    assert captured["admit"]["plan_sha256"] == plan["plan_sha256"]

    module._secure_deploy_approvals.revoke(approval["approval_id"], actor="admin")
    deploy_resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/canary/deploy",
        json={"approval_id": approval["approval_id"]},
        headers=_headers(csrf),
    )

    assert deploy_resp.status_code != 200
    assert deploy_resp.get_json()["error"]["code"] == "approval_status"
    assert deploy_calls == []


def test_http_revoke_propagates_to_broker_and_pending_blocks_deploy(authed_client, monkeypatch):
    module, client, csrf = authed_client
    from backend.secure_deploy.errors import SecureDeployError

    plan, approval = _create_approved_plan(client, csrf)
    bundle_sha = "e" * 64

    def fake_dry_run(plan_arg, _approval_arg):
        return {
            "verification": {
                "status": "pass",
                "broker_verification_sha256": "b" * 64,
                "plan_sha256": plan_arg["plan_sha256"],
                "dozeyguard_exit_code": 0,
                "blocking_findings": 0,
                "dry_run": True,
                "canary_admission_bundle_sha256": bundle_sha,
                "stages": [{"name": "independent_dozeyguard", "ok": True}],
            }
        }

    monkeypatch.setattr(module._secure_deploy_broker, "dry_run", fake_dry_run)
    dry_resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/broker-dry-run",
        json={"approval_id": approval["approval_id"]},
        headers=_headers(csrf),
    )
    assert dry_resp.status_code == 200, dry_resp.get_json()
    bound = module._secure_deploy_approvals.get(approval["approval_id"])
    assert bound["canary_admission_bundle_sha256"] == bundle_sha

    revoke_calls = []

    def unavailable_revoke(**kwargs):
        revoke_calls.append(kwargs)
        raise SecureDeployError("broker_unavailable", "broker socket missing", 503)

    deploy_calls = []
    monkeypatch.setattr(module._secure_deploy_broker, "revoke_canary_admission", unavailable_revoke)
    monkeypatch.setattr(module._secure_deploy_broker, "deploy_canary", lambda **kwargs: deploy_calls.append(kwargs) or {"canary_result": {}})

    revoke_resp = client.post(
        f"/api/secure-deploy/approvals/{approval['approval_id']}/revoke",
        json={"totp_code": _totp(now=time.time() + 30)},
        headers=_headers(csrf),
    )
    assert revoke_resp.status_code == 202, revoke_resp.get_json()
    body = revoke_resp.get_json()
    assert body["revocation_complete"] is False
    assert body["approval"]["status"] == "revocation_pending"
    assert revoke_calls[0]["admission_bundle_sha256"] == bundle_sha

    deploy_resp = client.post(
        f"/api/secure-deploy/plans/{plan['plan_id']}/canary/deploy",
        json={"approval_id": approval["approval_id"]},
        headers=_headers(csrf),
    )
    assert deploy_resp.status_code != 200
    assert deploy_resp.get_json()["error"]["code"] == "approval_status"
    assert deploy_calls == []

    def confirmed_revoke(**kwargs):
        return {"canary_result": {"status": "pass", "state": "revoked", "replay": False}}

    monkeypatch.setattr(module._secure_deploy_broker, "revoke_canary_admission", confirmed_revoke)
    retry_resp = client.post(
        f"/api/secure-deploy/approvals/{approval['approval_id']}/revoke",
        json={"totp_code": _totp(now=time.time() - 30)},
        headers=_headers(csrf),
    )
    assert retry_resp.status_code == 200, retry_resp.get_json()
    assert retry_resp.get_json()["approval"]["status"] == "revoked"


def test_broker_canary_dry_run(tmp_path, socket_enabled):
    _clear_secure_deploy_modules()
    _allow_local_unix_socket_connects()
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


def test_broker_error_envelope_preserves_rejected_operation(tmp_path, socket_enabled):
    _clear_secure_deploy_modules()
    _allow_local_unix_socket_connects()
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


def test_broker_accept_loop_survives_client_disconnect_before_response():
    _clear_secure_deploy_modules()
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from dockerpilot.secure_deploy_broker.protocol import PROTOCOL_VERSION, recv_message, send_message
    from dockerpilot.secure_deploy_broker.server import BrokerServer, BrokerRuntimeConfig

    class FakeListener:
        def __init__(self, sockets):
            self._sockets = deque(sockets)
            self._closed = False

        def settimeout(self, _timeout):
            return None

        def accept(self):
            if self._closed:
                raise OSError("listener closed")
            try:
                return self._sockets.popleft(), None
            except IndexError:
                time.sleep(0.01)
                raise socket.timeout()

        def close(self):
            self._closed = True

    req = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "breq_" + "d" * 12,
        "operation": "ping",
        "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
    }
    first_client, first_server = socket.socketpair()
    second_client, second_server = socket.socketpair()
    first_client.settimeout(5)
    second_client.settimeout(5)
    send_message(first_client, req)
    first_client.close()
    send_message(second_client, {**req, "request_id": "breq_" + "e" * 12})

    server = BrokerServer(
        BrokerRuntimeConfig(
            socket_path=None,
            dozeyguard=None,
            expected_peer_uid=os.getuid(),
        ),
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    listener = FakeListener([first_server, second_server])
    server._sock = listener
    server._stop.clear()
    server._thread = threading.Thread(target=server._serve, name="test-broker", daemon=True)
    server._thread.start()
    try:
        resp = recv_message(second_client, timeout=5)
        assert resp["ok"] is True
        assert resp["operation"] == "ping"
        assert server._thread.is_alive()
    finally:
        second_client.close()
        server.stop()


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
