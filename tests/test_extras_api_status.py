"""API tests for DockerPilotExtras status endpoint."""

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


class _FakeClient:
    def ping(self):
        return True

    def version(self):
        return {"Version": "27.0.1"}


class _FakePilot:
    def __init__(self):
        self.client = _FakeClient()

    def list_containers(self, **_kwargs):
        return []


def test_status_endpoint_local_context(monkeypatch):
    backend_app_module = _load_backend_app_module()
    client = backend_app_module.app.test_client()

    monkeypatch.setattr(backend_app_module, "get_selected_server_config", lambda: None)
    monkeypatch.setattr(backend_app_module, "get_dockerpilot", lambda: _FakePilot())

    response = client.get("/api/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["context"]["mode"] == "local"
    assert payload["context"]["server_name"] == "Local"
    assert payload["docker"]["available"] is True
    assert payload["dockerpilot"]["available"] is True


def test_status_endpoint_remote_context_and_versions(monkeypatch):
    backend_app_module = _load_backend_app_module()
    client = backend_app_module.app.test_client()

    server = {"id": "prod-1", "name": "Prod Node", "hostname": "prod.example.internal"}

    monkeypatch.setattr(backend_app_module, "get_selected_server_config", lambda: server)

    def fake_probe(_server_config, command, attempts=2):  # noqa: ARG001
        if "MISSING_DOCKERPILOT" in command:
            return "DockerPilot 0.1.0", None
        if "MISSING_DOCKER" in command:
            return "Docker version 27.0.1, build test", None
        return "", "unexpected probe command"

    monkeypatch.setattr(backend_app_module, "_run_remote_probe", fake_probe)

    response = client.get("/api/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["context"]["mode"] == "remote"
    assert payload["context"]["server_id"] == "prod-1"
    assert payload["context"]["hostname"] == "prod.example.internal"
    assert payload["docker"]["available"] is True
    assert payload["docker"]["version"].startswith("Docker version")
    assert payload["dockerpilot"]["available"] is True
    assert payload["dockerpilot"]["version"] == "DockerPilot 0.1.0"


def test_status_endpoint_remote_probe_errors(monkeypatch):
    backend_app_module = _load_backend_app_module()
    client = backend_app_module.app.test_client()

    server = {"id": "stage-1", "name": "Stage Node", "hostname": "stage.example.internal"}

    monkeypatch.setattr(backend_app_module, "get_selected_server_config", lambda: server)
    monkeypatch.setattr(
        backend_app_module,
        "_run_remote_probe",
        lambda _server_config, _command, attempts=2: ("", "ssh timeout"),  # noqa: ARG001
    )

    response = client.get("/api/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["docker"]["available"] is False
    assert payload["dockerpilot"]["available"] is False
    assert "Stage Node" in payload["docker"]["error"]
    assert "ssh timeout" in payload["docker"]["error"]
    assert "Stage Node" in payload["dockerpilot"]["error"]
    assert "ssh timeout" in payload["dockerpilot"]["error"]
