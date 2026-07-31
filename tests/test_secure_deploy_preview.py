"""Secure Deploy preview API tests (no Docker daemon)."""

from __future__ import annotations

import importlib
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_restful")

ROOT = Path(__file__).resolve().parents[1]
FAKE_DG = ROOT / "tests" / "fixtures" / "secure_deploy_preview" / "fake_dozeyguard.py"
POLICY = ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "policy" / "preview.toml"
PASS_SPEC = ROOT / "tests" / "fixtures" / "secure_deploy" / "pass" / "minimal_production_localhost.json"


def _load_app(monkeypatch, tmp_path, *, auth=True, totp=True):
    extras = ROOT / "DockerPilotExtras"
    if str(extras) not in sys.path:
        sys.path.insert(0, str(extras))
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))

    monkeypatch.setenv("WEB_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("WEB_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "test-pass")
    monkeypatch.setenv("WEB_AUTH_TOTP_SECRET", "JBSWY3DPEHPK3PXP" if totp else "")
    monkeypatch.setenv("SECURE_DEPLOY_STORE_ROOT", str(tmp_path / "sdstore"))
    monkeypatch.setenv("DOZEYGUARD_BIN", str(FAKE_DG))
    monkeypatch.setenv("DOZEYGUARD_POLICY_PATH", str(POLICY))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    for name in list(sys.modules):
        if name == "backend.app" or name.startswith("backend.secure_deploy") or name == "backend.api":
            sys.modules.pop(name, None)
    module = importlib.import_module("backend.app")
    importlib.reload(module)
    return module


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, auth=True, totp=True)
    monkeypatch.setattr(module, "_verify_password", lambda _p: True)
    monkeypatch.setattr(module, "_verify_totp_code", lambda *_a, **_k: True)
    client = module.app.test_client()
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "x", "totp_code": "123456"},
    )
    assert resp.status_code == 200
    status = client.get("/api/auth/status").get_json()
    csrf = status.get("secure_deploy_csrf")
    assert csrf
    return module, client, csrf


def _headers(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"}


def test_secure_deploy_import_has_no_docker():
    extras = ROOT / "DockerPilotExtras"
    sys.path.insert(0, str(extras))
    sys.path.insert(0, str(ROOT / "src"))
    import backend.secure_deploy.normalizer as n
    import backend.secure_deploy.firewall_planner as f
    import backend.secure_deploy.store as s
    import backend.secure_deploy.service as svc
    import backend.secure_deploy.dozeyguard_adapter as dg

    combined = "".join(
        Path(mod.__file__).read_text(encoding="utf-8")
        for mod in (n, f, s, svc, dg)
    )
    assert "import docker\n" not in combined
    assert "import docker " not in combined
    assert not any(line.strip().startswith("from docker ") or line.strip() == "import docker" for line in combined.splitlines())
    assert "docker.from_env" not in combined
    # Only the adapter may use subprocess.
    assert "subprocess" not in Path(n.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in Path(f.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in Path(s.__file__).read_text(encoding="utf-8")
    assert "subprocess" in Path(dg.__file__).read_text(encoding="utf-8")
    assert "shell=False" in Path(dg.__file__).read_text(encoding="utf-8")


def test_auth_disabled_blocks_secure_api(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, auth=False, totp=False)
    client = module.app.test_client()
    resp = client.post("/api/secure-deploy/validate", json={"spec": {}})
    assert resp.status_code in {503, 403}
    body = resp.get_json()
    assert body["error"]["code"] == "secure_deploy_auth_required"


def test_missing_session_401(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, auth=True, totp=True)
    client = module.app.test_client()
    resp = client.post(
        "/api/secure-deploy/validate",
        json={"spec": {}},
        headers=_headers("x"),
    )
    assert resp.status_code == 401


def test_session_without_mfa_denied(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, auth=True, totp=False)
    monkeypatch.setattr(module, "_verify_password", lambda _p: True)
    client = module.app.test_client()
    login = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert login.status_code == 200
    resp = client.post(
        "/api/secure-deploy/validate",
        json={"spec": {}},
        headers=_headers("dummy"),
    )
    assert resp.status_code in {403, 503}
    code = resp.get_json()["error"]["code"]
    assert code in {"secure_deploy_mfa_required", "secure_deploy_mfa_unverified", "secure_deploy_csrf"}


def test_http_validate_and_plan_preview(authed_client):
    _module, client, csrf = authed_client
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    headers = _headers(csrf)

    draft = client.post("/api/secure-deploy/drafts", json={"spec": spec}, headers=headers)
    assert draft.status_code == 201
    draft_id = draft.get_json()["draft_id"]

    got = client.get(f"/api/secure-deploy/drafts/{draft_id}", headers=headers)
    assert got.status_code == 200

    validate = client.post("/api/secure-deploy/validate", json={"spec": spec}, headers=headers)
    assert validate.status_code == 200
    assert validate.get_json()["valid"] is True

    plan = client.post("/api/secure-deploy/plan", json={"spec": spec}, headers=headers)
    assert plan.status_code == 201
    body = plan.get_json()
    assert body["status"] == "ready"
    assert body["plan"]["approval"]["status"] == "pending"
    assert body["plan"]["approval"]["approved_by"] is None
    assert "apply" not in body
    plan_id = body["plan"]["plan_id"]

    detail = client.get(f"/api/secure-deploy/plans/{plan_id}", headers=headers)
    assert detail.status_code == 200


def test_csrf_required(authed_client):
    _module, client, _csrf = authed_client
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    resp = client.post("/api/secure-deploy/validate", json={"spec": spec})
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "secure_deploy_csrf"


def test_bad_origin_denied(authed_client):
    _module, client, csrf = authed_client
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    resp = client.post(
        "/api/secure-deploy/validate",
        json={"spec": spec},
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "secure_deploy_csrf"


def test_non_json_415(authed_client):
    _module, client, csrf = authed_client
    resp = client.post(
        "/api/secure-deploy/validate",
        data="not-json",
        headers={**_headers(csrf), "Content-Type": "text/plain"},
    )
    assert resp.status_code == 415


def test_payload_too_large(authed_client):
    _module, client, csrf = authed_client
    big = {"spec": {"x": "y" * (2 * 1024 * 1024)}}
    resp = client.post(
        "/api/secure-deploy/validate",
        data=json.dumps(big),
        headers={**_headers(csrf), "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_unknown_field_rejected(authed_client):
    _module, client, csrf = authed_client
    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    spec["totally_unknown_field"] = True
    resp = client.post("/api/secure-deploy/validate", json={"spec": spec}, headers=_headers(csrf))
    assert resp.status_code == 422


def test_normalizer_and_firewall_unit():
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.errors import ValidationFailedError

    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    compose = normalize_spec_to_compose(spec)
    assert compose["services"]["web"]["read_only"] is True
    assert "network_mode" not in compose["services"]["web"]
    assert compose["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "x-dockerpilot-secret-refs" in compose
    fw = plan_firewall_actions(spec)
    assert fw["required"] is False

    bad = json.loads(json.dumps(spec))
    bad["network"]["exposure"] = "lan_allowlist"
    bad["network"]["published_ports"] = [
        {
            "container_port": 80,
            "host_port": 8080,
            "protocol": "tcp",
            "bind_address": "0.0.0.0",
            "exposure": "lan_allowlist",
        }
    ]
    bad["network"]["allowed_sources"] = []
    with pytest.raises(ValidationFailedError):
        plan_firewall_actions(bad)

    shell = json.loads(json.dumps(spec))
    shell["runtime"]["command"] = "rm -rf /"
    with pytest.raises(ValidationFailedError):
        normalize_spec_to_compose(shell)

    wild = json.loads(json.dumps(spec))
    wild["network"]["exposure"] = "localhost"
    wild["network"]["published_ports"][0]["bind_address"] = "0.0.0.0"
    with pytest.raises(ValidationFailedError):
        normalize_spec_to_compose(wild)


def test_store_rejects_traversal_and_mutation(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.store import FileSecureDeployStore
    from backend.secure_deploy.errors import SecureDeployError, ValidationFailedError

    store = FileSecureDeployStore(tmp_path / "s")
    store.save_plan("plan_abc123456789", {"plan_id": "plan_abc123456789", "ok": True})
    with pytest.raises(SecureDeployError):
        store.save_plan("plan_abc123456789", {"plan_id": "plan_abc123456789", "ok": False})
    with pytest.raises(ValidationFailedError):
        store.get_plan("../etc/passwd____")


def test_store_rejects_symlink(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.store import FileSecureDeployStore
    from backend.secure_deploy.errors import SecureDeployError

    root = tmp_path / "s"
    store = FileSecureDeployStore(root)
    target = root / "plans" / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = root / "plans" / "plan_symlinktest01.json"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(SecureDeployError):
        store.get_plan("plan_symlinktest01")


def test_dozeyguard_exit2_not_ready(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig
    from backend.secure_deploy.service import SecureDeployService
    from backend.secure_deploy.store import FileSecureDeployStore
    import backend.secure_deploy.dozeyguard_adapter as adapter
    from backend.secure_deploy import service as service_mod

    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    svc = SecureDeployService(
        FileSecureDeployStore(tmp_path / "s"),
        dozeyguard_config=DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
    )
    original = adapter.run_dozeyguard_scan

    def wrapped(compose, config):
        compose = dict(compose)
        compose["FORCE_FAIL"] = True
        return original(compose, config)

    service_mod.run_dozeyguard_scan = wrapped
    try:
        env = svc.generate_plan(spec, "admin", request_id="req1")
        assert env["status"] == "invalid"
        assert env["plan"]["dozeyguard"]["exit_code"] == 2
    finally:
        service_mod.run_dozeyguard_scan = original


def test_dozeyguard_exit1_fail_closed(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig, run_dozeyguard_scan
    from backend.secure_deploy.errors import ScannerError
    from backend.secure_deploy.normalizer import normalize_spec_to_compose

    spec = json.loads(PASS_SPEC.read_text(encoding="utf-8"))
    compose = normalize_spec_to_compose(spec)
    compose["FORCE_ERROR"] = True
    with pytest.raises(ScannerError):
        run_dozeyguard_scan(
            compose,
            DozeyguardConfig(executable=str(FAKE_DG), policy_path=str(POLICY)),
        )


def test_dozeyguard_invalid_json_and_hash(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig, run_dozeyguard_scan
    from backend.secure_deploy.errors import ScannerError

    bad = tmp_path / "bad_dg"
    bad.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            print('not-json')
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    bad.chmod(0o755)
    with pytest.raises(ScannerError):
        run_dozeyguard_scan(
            {"services": {}},
            DozeyguardConfig(executable=str(bad), policy_path=str(POLICY)),
        )

    wrong_hash = tmp_path / "wrong_hash_dg"
    wrong_hash.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, sys
            report = {
              "contract_version": 1,
              "scanner": {"name": "dozeyguard", "version": "x"},
              "input": {"format": "compose-json", "sha256": "0"*64, "bytes": 1},
              "policy": {"sha256": "0"*64, "exceptions_applied": []},
              "summary": {"services": 0, "findings": 0, "blocking": 0, "warnings": 0, "info": 0},
              "findings": [],
              "result": {"status": "pass", "exit_code": 0, "result_sha256": "1"*64},
            }
            print(json.dumps(report))
            """
        ),
        encoding="utf-8",
    )
    wrong_hash.chmod(0o755)
    with pytest.raises(ScannerError) as exc:
        run_dozeyguard_scan(
            {"services": {}},
            DozeyguardConfig(executable=str(wrong_hash), policy_path=str(POLICY)),
        )
    assert exc.value.code in {"dozeyguard_hash_mismatch", "dozeyguard_contract"}


def test_dozeyguard_timeout(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig, run_dozeyguard_scan
    from backend.secure_deploy.errors import ScannerError

    sleeper = tmp_path / "sleep_dg"
    sleeper.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import time
            time.sleep(5)
            """
        ),
        encoding="utf-8",
    )
    sleeper.chmod(0o755)
    with pytest.raises(ScannerError) as exc:
        run_dozeyguard_scan(
            {"services": {}},
            DozeyguardConfig(
                executable=str(sleeper),
                policy_path=str(POLICY),
                timeout_seconds=1,
            ),
        )
    assert exc.value.code == "dozeyguard_timeout"


def test_dozeyguard_oversized_stdout(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig, run_dozeyguard_scan
    from backend.secure_deploy.errors import ScannerError

    fat = tmp_path / "fat_dg"
    fat.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            print('x' * 10000)
            """
        ),
        encoding="utf-8",
    )
    fat.chmod(0o755)
    with pytest.raises(ScannerError) as exc:
        run_dozeyguard_scan(
            {"services": {}},
            DozeyguardConfig(
                executable=str(fat),
                policy_path=str(POLICY),
                max_output_bytes=100,
            ),
        )
    assert exc.value.code == "dozeyguard_output_limit"


def test_dozeyguard_stderr_sentinel(tmp_path):
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    sys.path.insert(0, str(ROOT / "src"))
    from backend.secure_deploy.dozeyguard_adapter import DozeyguardConfig, run_dozeyguard_scan
    from backend.secure_deploy.errors import ScannerError

    leak = tmp_path / "leak_dg"
    leak.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            print('SECRET_DO_NOT_PRINT', file=sys.stderr)
            print('{}')
            """
        ),
        encoding="utf-8",
    )
    leak.chmod(0o755)
    with pytest.raises(ScannerError) as exc:
        run_dozeyguard_scan(
            {"services": {}},
            DozeyguardConfig(executable=str(leak), policy_path=str(POLICY)),
        )
    assert exc.value.code == "dozeyguard_secret_leak"


def test_frontend_source_has_preview_only_guards():
    page = ROOT / "DockerPilotExtras" / "frontend" / "src" / "pages" / "SecureDeploy.jsx"
    app = ROOT / "DockerPilotExtras" / "frontend" / "src" / "App.jsx"
    api = ROOT / "DockerPilotExtras" / "frontend" / "src" / "services" / "api.js"
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert "PREVIEW ONLY" in text
    for banned in (
        "/api/command/execute",
        "Deploy Now",
        "Apply Plan",
        "onApply",
        "onApprove",
        ">Approve<",
        ">Deploy<",
        ">Apply<",
        ">Execute<",
    ):
        assert banned not in text
    assert "Save draft" in text
    assert "Generate preview" in text
    assert "Secure Deploy" in app.read_text(encoding="utf-8")
    assert "/secure-deploy" in app.read_text(encoding="utf-8")
    api_text = api.read_text(encoding="utf-8")
    assert "localStorage" not in api_text or "secureDeploy" not in api_text.lower()
    assert "secureDeployAPI" in api_text
    assert "/secure-deploy/approve" not in api_text
    assert "/secure-deploy/apply" not in api_text
