"""Regression tests for extracted rolling and quick deployment strategies."""

from io import StringIO
from types import SimpleNamespace

import docker
from rich.console import Console

from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.deployment_strategies import quick as quick_strategy
from dockerpilot.deployment_strategies import rolling as rolling_strategy
from dockerpilot.models import DeploymentConfig


class _Logger:
    def __init__(self):
        self.messages = []

    def _add(self, level, message):
        self.messages.append((level, str(message)))

    def info(self, message):
        self._add("info", message)

    def warning(self, message):
        self._add("warning", message)

    def error(self, message):
        self._add("error", message)

    def debug(self, message):
        self._add("debug", message)


class _NewContainer:
    def __init__(self):
        self.name = "demo_new"
        self.status = "created"
        self.renamed_to = None
        self.stopped = False
        self.removed = False

    def start(self):
        self.status = "running"

    def reload(self):
        return None

    def stop(self, **_kwargs):
        self.stopped = True
        self.status = "exited"

    def remove(self):
        self.removed = True

    def rename(self, name):
        self.renamed_to = name
        self.name = name

    def logs(self, **_kwargs):
        return b""


class _FirstDeployContainers:
    def __init__(self):
        self.created = _NewContainer()
        self.create_kwargs = None
        self.run_kwargs = None

    def get(self, _name):
        raise docker.errors.NotFound("missing")

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.created

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return _NewContainer()

    def list(self, **_kwargs):
        return []


class _Images:
    def __init__(self):
        self.build_calls = []

    def build(self, **kwargs):
        self.build_calls.append(kwargs)
        return SimpleNamespace(id="new-image"), []

    def get(self, _tag):
        return SimpleNamespace(id="new-image")

    def remove(self, *_args, **_kwargs):
        return None


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def test_facade_delegates_rolling_strategy(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    config = object()
    build_config = {"pull": False}
    seen = {}

    def fake_strategy(actual_host, actual_config, actual_build):
        seen.update(host=actual_host, config=actual_config, build=actual_build)
        return "sentinel"

    monkeypatch.setattr("dockerpilot.deployment_service._rolling_deploy_strategy", fake_strategy)
    assert host._rolling_deploy(config, build_config) == "sentinel"
    assert seen == {"host": host, "config": config, "build": build_config}


def test_facade_delegates_quick_strategy(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    seen = {}

    def fake_strategy(actual_host, **kwargs):
        seen.update(host=actual_host, kwargs=kwargs)
        return "sentinel"

    monkeypatch.setattr("dockerpilot.deployment_service._quick_deploy_strategy", fake_strategy)
    result = host.quick_deploy(
        dockerfile_path="src",
        image_tag="demo:v1",
        container_name="demo",
        port_mapping={"80": "8080"},
        environment={"MODE": "test"},
        volumes={"data": "/data"},
        yaml_config="deploy.yml",
        cleanup_old_image=False,
    )
    assert result == "sentinel"
    assert seen["host"] is host
    assert seen["kwargs"] == {
        "dockerfile_path": "src",
        "image_tag": "demo:v1",
        "container_name": "demo",
        "port_mapping": {"80": "8080"},
        "environment": {"MODE": "test"},
        "volumes": {"data": "/data"},
        "yaml_config": "deploy.yml",
        "cleanup_old_image": False,
    }


def test_rolling_first_deploy_keeps_runtime_behavior(monkeypatch):
    monkeypatch.setattr(rolling_strategy.time, "sleep", lambda _seconds: None)
    containers = _FirstDeployContainers()
    records = []
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        client=SimpleNamespace(containers=containers),
        _resolve_runtime_network=lambda network: network,
        _detect_health_check_endpoint=lambda _image: None,
        _prepare_image=lambda *_args: (True, "image ready"),
        _normalize_volumes=lambda _volumes: [],
        _get_resource_limits=lambda _config: {},
        _record_deployment=lambda *args: records.append(args),
    )
    config = DeploymentConfig(
        image_tag="alpine:latest",
        container_name="demo",
        port_mapping={},
        environment={},
        volumes={},
        health_check_endpoint=None,
    )

    assert rolling_strategy.rolling_deploy(host, config, {}) is True
    assert containers.create_kwargs["image"] == "alpine:latest"
    assert containers.create_kwargs["command"] == ["sh", "-c", "sleep 3600"]
    assert containers.created.renamed_to == "demo"
    assert records and records[0][2] == "rolling"


def test_quick_first_deploy_builds_and_runs_container(monkeypatch, tmp_path):
    monkeypatch.setattr(quick_strategy.time, "sleep", lambda _seconds: None)
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    containers = _FirstDeployContainers()
    images = _Images()
    records = []
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        client=SimpleNamespace(containers=containers, images=images),
        _record_deployment=lambda *args: records.append(args),
    )

    assert quick_strategy.quick_deploy(
        host,
        dockerfile_path=str(tmp_path),
        image_tag="demo:v1",
        container_name="demo",
        environment={"MODE": "test"},
        volumes={"data": "/data"},
    ) is True

    assert images.build_calls[0]["tag"] == "demo:v1"
    assert containers.run_kwargs["name"] == "demo"
    assert containers.run_kwargs["restart_policy"] == {"Name": "unless-stopped"}
    assert records and records[0][2] == "quick"
