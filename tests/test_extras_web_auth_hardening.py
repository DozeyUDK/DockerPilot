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
