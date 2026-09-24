"""API tests for DockerPilotExtras status endpoint."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import importlib
import signal
import sys
import threading

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_restful")


def _load_backend_app_module(monkeypatch=None):
    extras_dir = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
    extras_dir_str = str(extras_dir)
    if extras_dir_str not in sys.path:
        sys.path.insert(0, extras_dir_str)
    # Legacy API tests expect WEB_AUTH_ENABLED=false (default product config).
    if monkeypatch is not None:
        monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    else:
        import os

        os.environ["WEB_AUTH_ENABLED"] = "false"
    for name in list(sys.modules):
        if name == "backend.app" or name.startswith("backend.secure_deploy") or name == "backend.api":
            sys.modules.pop(name, None)
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
    backend_app_module = _load_backend_app_module(monkeypatch)
    client = backend_app_module.app.test_client()

    monkeypatch.setattr(backend_app_module, "get_selected_server_config", lambda: None)
    # Resources capture get_dockerpilot during registration; seed its cache so
    # the test never constructs a real client or probes the Docker daemon.
    monkeypatch.setattr(backend_app_module, "_dockerpilot_instances", {"local": _FakePilot()})

    response = client.get("/api/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["context"]["mode"] == "local"
    assert payload["context"]["server_name"] == "Local"
    assert payload["docker"]["available"] is True
    assert payload["dockerpilot"]["available"] is True


def test_status_endpoint_remote_context_and_versions(monkeypatch):
    backend_app_module = _load_backend_app_module(monkeypatch)
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
    backend_app_module = _load_backend_app_module(monkeypatch)
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


def test_get_dockerpilot_constructs_cached_instance_once_without_patching_signals(monkeypatch):
    backend_app_module = _load_backend_app_module(monkeypatch)
    monkeypatch.setattr(backend_app_module, "_dockerpilot_instances", {})
    constructor_entered = threading.Event()
    release_constructor = threading.Event()
    created = []

    def signal_sentinel(*_args, **_kwargs):
        return None

    monkeypatch.setattr(signal, "signal", signal_sentinel)

    class _ConstructedPilot:
        def __init__(self, **kwargs):
            created.append((kwargs, signal.signal))
            constructor_entered.set()
            assert release_constructor.wait(timeout=2)

    monkeypatch.setattr(backend_app_module, "DockerPilotEnhanced", _ConstructedPilot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(backend_app_module.get_dockerpilot, "local")
        assert constructor_entered.wait(timeout=2)
        second = pool.submit(backend_app_module.get_dockerpilot, "local")
        release_constructor.set()
        first_instance = first.result(timeout=2)
        second_instance = second.result(timeout=2)

    assert first_instance is second_instance
    assert len(created) == 1
    assert created[0][0]["register_signal_handlers"] is False
    assert created[0][1] is signal_sentinel
    assert signal.signal is signal_sentinel


def test_get_dockerpilot_does_not_cache_failed_construction_and_can_retry(monkeypatch):
    backend_app_module = _load_backend_app_module(monkeypatch)
    instances = {}
    monkeypatch.setattr(backend_app_module, "_dockerpilot_instances", instances)
    attempts = []

    class _RetryingPilot:
        def __init__(self, **_kwargs):
            attempts.append(object())
            if len(attempts) == 1:
                raise RuntimeError("constructor failed")

    monkeypatch.setattr(backend_app_module, "DockerPilotEnhanced", _RetryingPilot)

    with pytest.raises(RuntimeError, match="constructor failed"):
        backend_app_module.get_dockerpilot("local")

    assert instances == {}
    pilot = backend_app_module.get_dockerpilot("local")
    assert isinstance(pilot, _RetryingPilot)
    assert len(attempts) == 2
