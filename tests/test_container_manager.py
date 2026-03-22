"""Tests for container manager result handling."""

from contextlib import contextmanager

from rich.console import Console

from dockerpilot.container_manager import ContainerManager


class DummyLogger:
    """Minimal logger for tests."""

    def __init__(self) -> None:
        self.errors = []

    def error(self, message) -> None:
        self.errors.append(str(message))


@contextmanager
def noop_error_handler(_operation):
    """A no-op error handler context manager."""
    yield


def test_container_operation_does_not_report_success_when_handler_returns_false():
    console = Console(record=True, force_terminal=False, width=120)
    logger = DummyLogger()
    manager = ContainerManager(client=None, console=console, logger=logger, error_handler=noop_error_handler)
    manager._restart_container = lambda *_args, **_kwargs: False

    result = manager.container_operation("restart", "missing-container", timeout=10)
    output = console.export_text()

    assert result is False
    assert "Failed to restart container missing-container" in output
    assert "restarted successfully" not in output
