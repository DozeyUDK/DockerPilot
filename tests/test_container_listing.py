"""Regression tests for container listing extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_listing
from dockerpilot.container_manager import ContainerManager


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


class _Logger:
    def error(self, _message):
        pass


class _Containers:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def list(self, all=True):
        self.calls.append(all)
        return self.items


def test_json_listing_preserves_shape_without_image_inspect():
    container = SimpleNamespace(
        short_id="abc123",
        name="demo",
        status="running",
        ports={"80/tcp": [{"HostPort": "8080"}]},
        attrs={
            "Config": {"Image": "demo:latest"},
            "Image": "sha256:" + "a" * 64,
            "State": {"Status": "running"},
            "Created": "2026-09-24T12:00:00+00:00",
        },
    )
    containers = _Containers([container])
    host = SimpleNamespace(
        client=SimpleNamespace(containers=containers),
        console=Console(file=StringIO(), force_terminal=False, width=120),
        logger=_Logger(),
        _error_handler=_error_handler,
    )

    result = container_listing.list_containers(host, show_all=False, format_output="json")

    assert containers.calls == [False]
    assert result == [{
        "id": "abc123",
        "name": "demo",
        "status": "running",
        "state": "running",
        "image": "demo:latest",
        "ports": {"80/tcp": [{"HostPort": "8080"}]},
        "created": "2026-09-24T12:00:00+00:00",
        "size": "N/A",
    }]


def test_listing_facade_delegates(monkeypatch):
    manager = ContainerManager(None, None, None, _error_handler)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.container_manager._list_containers_impl",
        lambda host, **kwargs: calls.append((host, kwargs)) or ["ok"],
    )

    assert manager.list_containers(False, "json") == ["ok"]
    assert calls == [(manager, {"show_all": False, "format_output": "json"})]


def test_image_label_keeps_config_reference_and_digest_fallback():
    assert container_listing.container_image_label({"Config": {"Image": "demo:v1"}}) == "demo:v1"
    assert container_listing.container_image_label({
        "Config": {},
        "Image": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
    }) == "abcdef012345…"
