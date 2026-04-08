"""API tests for DockerPilotExtras preflight endpoint."""

from pathlib import Path
import importlib
import sys

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_restful")


def _load_backend_app_module():
    extras_dir = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
    extras_dir_str = str(extras_dir)
    if extras_dir_str not in sys.path:
        sys.path.insert(0, extras_dir_str)
    return importlib.import_module("backend.app")


def test_preflight_endpoint_returns_200_when_success(monkeypatch):
    backend_app_module = _load_backend_app_module()
    client = backend_app_module.app.test_client()

    monkeypatch.setattr(
        backend_app_module,
        "run_preflight_checks",
        lambda _base: {"success": True, "checks": {}, "required_failed": [], "warnings": []},
    )

    response = client.get("/api/preflight")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True


def test_preflight_endpoint_returns_503_when_required_fail(monkeypatch):
    backend_app_module = _load_backend_app_module()
    client = backend_app_module.app.test_client()

    monkeypatch.setattr(
        backend_app_module,
        "run_preflight_checks",
        lambda _base: {"success": False, "checks": {}, "required_failed": ["docker"], "warnings": []},
    )

    response = client.get("/api/preflight")
    payload = response.get_json()

    assert response.status_code == 503
    assert payload["success"] is False
    assert payload["required_failed"] == ["docker"]
