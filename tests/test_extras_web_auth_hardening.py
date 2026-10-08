from pathlib import Path
import importlib
import os
import sys

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_restful")
pytest.importorskip("cryptography")


def _load_app(monkeypatch, tmp_path: Path, *, max_failures: int = 5):
    root = Path(__file__).resolve().parents[1]
    extras = root / "DockerPilotExtras"
    src = root / "src"
    for path in (extras, src):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("WEB_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WEB_AUTH_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_AUTH_PASSWORD_HASH", "")
    monkeypatch.setenv("WEB_AUTH_TOTP_SECRET", "")
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES", str(max_failures))
    monkeypatch.setenv("AUTH_LOGIN_WINDOW_SECONDS", "60")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-extras-hardening")
    monkeypatch.setenv("FLASK_ENV", "development")

    for name in list(sys.modules):
        if name == "backend.app":
            sys.modules.pop(name, None)
    return importlib.import_module("backend.app")



def _load_unauth_app(monkeypatch, tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    extras = root / "DockerPilotExtras"
    src = root / "src"
    for path in (extras, src):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    monkeypatch.setenv("SECRET_KEY", "unauth-loopback-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("FLASK_RUN_FROM_CLI", raising=False)

    sys.modules.pop("backend.app", None)
    return importlib.import_module("backend.app")


def test_global_csrf_protects_mutating_api(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path)
    client = module.app.test_client()

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-password"},
    )
    assert login.status_code == 200
    payload = login.get_json()
    token = payload["csrf_token"]
    assert token
    assert token == payload["secure_deploy_csrf"]

    blocked = client.post("/api/servers/select", json={"server_id": "local"})
    assert blocked.status_code == 403
    assert blocked.get_json()["csrf_required"] is True

    allowed = client.post(
        "/api/servers/select",
        json={"server_id": "local"},
        headers={"X-CSRF-Token": token},
    )
    assert allowed.status_code == 200
    assert allowed.get_json()["success"] is True

    blocked_logout = client.post("/api/auth/logout")
    assert blocked_logout.status_code == 403
    ok_logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
    assert ok_logout.status_code == 200


def test_login_rate_limit_blocks_repeated_failures(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path, max_failures=2)
    client = module.app.test_client()
    body = {"username": "admin", "password": "wrong"}

    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 401
    limited = client.post("/api/auth/login", json=body)
    assert limited.status_code == 429
    assert limited.get_json()["retry_after_seconds"] > 0


def test_non_loopback_backend_bind_requires_web_auth(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    extras = root / "DockerPilotExtras"
    src = root / "src"
    for path in (extras, src):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    monkeypatch.setenv("SECRET_KEY", "bind-security-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    sys.modules.pop("backend.app", None)
    try:
        with pytest.raises(RuntimeError, match="non-loopback.*WEB_AUTH_ENABLED=true"):
            importlib.import_module("backend.app")
    finally:
        sys.modules.pop("backend.app", None)


def test_documented_runner_style_import_is_allowed_without_web_auth(monkeypatch, tmp_path):
    module = _load_unauth_app(monkeypatch, tmp_path)

    assert module.EXTRAS_HOST == "127.0.0.1"
    assert module.WEB_AUTH_ENABLED is False


def test_flask_cli_is_rejected_without_web_auth(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    extras = root / "DockerPilotExtras"
    src = root / "src"
    for path in (extras, src):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    monkeypatch.setenv("SECRET_KEY", "flask-cli-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    sys.modules.pop("backend.app", None)
    try:
        with pytest.raises(RuntimeError, match="Flask CLI"):
            importlib.import_module("backend.app")
    finally:
        sys.modules.pop("backend.app", None)


def test_unauthenticated_mode_rejects_non_loopback_client(monkeypatch, tmp_path):
    module = _load_unauth_app(monkeypatch, tmp_path)
    client = module.app.test_client()

    response = client.get(
        "/api/status",
        environ_base={"REMOTE_ADDR": "203.0.113.20"},
    )

    assert response.status_code == 403
    assert "non-loopback" in response.get_json()["error"]


def test_unauthenticated_mode_rejects_proxied_access(monkeypatch, tmp_path):
    module = _load_unauth_app(monkeypatch, tmp_path)
    client = module.app.test_client()

    response = client.get(
        "/api/status",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "203.0.113.20"},
    )

    assert response.status_code == 403
    assert "proxied access" in response.get_json()["error"]
