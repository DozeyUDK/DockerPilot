"""Regression tests for container creation extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_creation
from dockerpilot.container_manager import ContainerManager


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


class _Containers:
    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(short_id="abc123")


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _host():
    logger = _Logger()
    containers = _Containers()
    host = SimpleNamespace(
        client=SimpleNamespace(containers=containers),
        console=Console(file=StringIO(), force_terminal=False, width=120),
        logger=logger,
    )
    host._normalize_volumes = lambda volumes: container_creation.normalize_volumes(host, volumes)
    return host, containers, logger


def test_normalize_volumes_preserves_supported_formats():
    host, _containers, _logger = _host()

    assert container_creation.normalize_volumes(host, ["a:/a"]) == ["a:/a"]
    assert container_creation.normalize_volumes(host, {
        "/srv/app": {"bind": "/app", "mode": "ro"},
        "named": "/data",
        "../relative": "/relative",
    }) == [
        "/srv/app:/app:ro",
        "named:/data",
        "../relative:/relative",
    ]


def test_run_new_container_builds_full_docker_kwargs():
    host, containers, _logger = _host()

    assert container_creation.run_new_container(
        host,
        "demo:latest",
        "demo",
        ports={"80/tcp": 8080},
        command="python app.py",
        environment={"MODE": "prod"},
        volumes={"demo-data": "/data"},
        restart_policy="always",
        network="host",
        privileged=True,
        cpu_limit="1.5",
        memory_limit="512m",
    ) is True

    assert containers.kwargs == {
        "image": "demo:latest",
        "name": "demo",
        "detach": True,
        "ports": {"80/tcp": 8080},
        "command": "python app.py",
        "environment": {"MODE": "prod"},
        "volumes": ["demo-data:/data"],
        "restart_policy": {"Name": "always"},
        "network_mode": "host",
        "privileged": True,
        "nano_cpus": 1_500_000_000,
        "mem_limit": 512 * 1024 * 1024,
    }


def test_creation_facade_delegates(monkeypatch):
    manager = ContainerManager(None, None, None, _error_handler)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.container_manager._run_new_container_impl",
        lambda host, image_name, name, **kwargs: calls.append((host, image_name, name, kwargs)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.container_manager._normalize_volumes_impl",
        lambda host, volumes: calls.append(("normalize", host, volumes)) or ["normalized"],
    )

    assert manager._normalize_volumes({"v": "/data"}) == ["normalized"]
    assert manager.run_new_container("img", "name", cpu_limit="2") is True
    assert calls[0] == ("normalize", manager, {"v": "/data"})
    assert calls[1][0:3] == (manager, "img", "name")
    assert calls[1][3]["cpu_limit"] == "2"
