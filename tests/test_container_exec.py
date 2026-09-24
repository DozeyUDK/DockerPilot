"""Regression tests for container exec extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_exec
from dockerpilot.container_manager import ContainerManager


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120, record=True)


def test_interactive_exec_invokes_docker_cli(monkeypatch):
    calls = []
    container = SimpleNamespace(status="running")
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
        _error_handler=_error_handler,
    )
    monkeypatch.setattr(
        container_exec.subprocess,
        "run",
        lambda args, check=False: calls.append((args, check)) or SimpleNamespace(returncode=0),
    )

    assert container_exec.exec_container(host, "demo", "/bin/sh") is True
    assert calls == [(["docker", "exec", "-it", "demo", "/bin/sh"], False)]


def test_interactive_exec_rejects_stopped_container_before_subprocess(monkeypatch):
    container = SimpleNamespace(status="exited")
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
        _error_handler=_error_handler,
    )
    monkeypatch.setattr(
        container_exec.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("subprocess must not run")),
    )

    assert container_exec.exec_container(host, "demo") is False


def test_non_interactive_exec_returns_success_and_prints_output():
    calls = []
    container = SimpleNamespace(
        status="running",
        exec_run=lambda command: calls.append(command) or SimpleNamespace(exit_code=0, output=b"hello\n"),
    )
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=_console(),
        logger=_Logger(),
        _error_handler=_error_handler,
    )

    assert container_exec.exec_command_non_interactive(host, "demo", "echo hello") is True
    assert calls == ["echo hello"]
    assert "hello" in host.console.export_text()


def test_exec_facade_delegates(monkeypatch):
    manager = ContainerManager(None, None, _Logger(), _error_handler)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.container_manager._exec_container_impl",
        lambda host, name, command: calls.append(("interactive", host, name, command)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.container_manager._exec_command_non_interactive_impl",
        lambda host, name, command: calls.append(("noninteractive", host, name, command)) or False,
    )

    assert manager.exec_container("demo", "/bin/sh") is True
    assert manager.exec_command_non_interactive("demo", "false") is False
    assert calls == [
        ("interactive", manager, "demo", "/bin/sh"),
        ("noninteractive", manager, "demo", "false"),
    ]
