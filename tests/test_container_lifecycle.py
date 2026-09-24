"""Regression tests for container lifecycle extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_lifecycle
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


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120, record=True)


def test_start_container_calls_start_and_wait_for_running():
    calls = []
    container = SimpleNamespace(status="exited", start=lambda: calls.append("start"))
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
        _error_handler=_error_handler,
        _wait_for_container_status=lambda name, status, timeout=30: calls.append((name, status, timeout)) or True,
    )

    assert container_lifecycle.start_container(host, "demo") is True
    assert calls == ["start", ("demo", "running", 30)]


def test_container_operation_dispatches_and_keeps_rename_wording():
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        _start_container=lambda *_args, **_kwargs: True,
        _stop_container=lambda *_args, **_kwargs: True,
        _restart_container=lambda *_args, **_kwargs: True,
        _remove_container=lambda *_args, **_kwargs: True,
        _pause_container=lambda *_args, **_kwargs: True,
        _unpause_container=lambda *_args, **_kwargs: True,
        _rename_container=lambda *_args, **_kwargs: True,
    )

    assert container_lifecycle.container_operation(host, "rename", "old", new_name="new") is True
    output = host.console.export_text()
    assert "renamed successfully" in output
    assert "renameed" not in output


def test_update_restart_policy_passes_expected_docker_payload():
    payloads = []
    container = SimpleNamespace(name="demo", update=lambda **kwargs: payloads.append(kwargs))
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
    )

    assert container_lifecycle.update_restart_policy(host, "demo", "always") is True
    assert payloads == [{"restart_policy": {"Name": "always"}}]


def test_stop_and_remove_preserves_order_and_timeout():
    calls = []
    container = SimpleNamespace(
        status="running",
        stop=lambda timeout=10: calls.append(("stop", timeout)),
        remove=lambda: calls.append(("remove", None)),
    )
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
        _error_handler=_error_handler,
    )

    assert container_lifecycle.stop_and_remove_container(host, "demo", timeout=17) is True
    assert calls == [("stop", 17), ("remove", None)]


def test_lifecycle_facade_delegates(monkeypatch):
    manager = ContainerManager(None, None, None, _error_handler)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.container_manager._update_restart_policy_impl",
        lambda host, name, policy: calls.append((host, name, policy)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.container_manager._wait_for_container_status_impl",
        lambda host, name, status, timeout: calls.append((host, name, status, timeout)) or True,
    )

    assert manager.update_restart_policy("demo", "always") is True
    assert manager._wait_for_container_status("demo", "running", 4) is True
    assert calls == [
        (manager, "demo", "always"),
        (manager, "demo", "running", 4),
    ]
