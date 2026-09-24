"""Regression tests for container inspection extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_inspection
from dockerpilot.container_manager import ContainerManager


class _Logger:
    def error(self, _message):
        pass


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120, record=True)


def test_named_log_view_splits_multiple_containers_and_passes_tail():
    calls = []

    def get(name):
        return SimpleNamespace(logs=lambda tail: calls.append((name, tail)) or f"log-{name}".encode())

    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=get)),
        console=_console(),
    )

    container_inspection.view_container_logs(host, "one, two", tail=17)
    output = host.console.export_text()

    assert calls == [("one", 17), ("two", 17)]
    assert "Container: one" in output
    assert "Container: two" in output
    assert "log-one" in output
    assert "log-two" in output


def test_json_inspection_renders_container_attrs():
    container = SimpleNamespace(attrs={"Name": "demo", "State": {"Status": "running"}})
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
    )

    container_inspection.view_container_json(host, "demo")
    output = host.console.export_text()

    assert "Container JSON: demo" in output
    assert '"Status": "running"' in output


def test_inspection_facade_delegates(monkeypatch):
    manager = ContainerManager(None, None, _Logger(), _error_handler)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.container_manager._view_container_logs_impl",
        lambda host, names, tail: calls.append(("logs", host, names, tail)) or "logs",
    )
    monkeypatch.setattr(
        "dockerpilot.container_manager._view_container_json_impl",
        lambda host, name: calls.append(("json", host, name)) or "json",
    )

    assert manager.view_container_logs("a,b", 12) == "logs"
    assert manager.view_container_json("demo") == "json"
    assert calls == [
        ("logs", manager, "a,b", 12),
        ("json", manager, "demo"),
    ]
