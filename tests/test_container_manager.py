"""Tests for container manager result handling."""

from contextlib import contextmanager

from rich.console import Console

from dockerpilot.container_manager import ContainerManager, _container_image_label


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


def test_container_image_label_uses_config_image():
    attrs = {"Config": {"Image": "myapp:latest"}, "Image": "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"}
    assert _container_image_label(attrs) == "myapp:latest"


def test_container_image_label_falls_back_to_short_digest():
    attrs = {"Config": {}, "Image": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"}
    assert _container_image_label(attrs) == "abcdef012345…"


def test_container_operation_rename_reports_renamed_in_success_message():
    console = Console(record=True, force_terminal=False, width=120)
    logger = DummyLogger()
    manager = ContainerManager(client=None, console=console, logger=logger, error_handler=noop_error_handler)
    manager._rename_container = lambda *_args, **_kwargs: True

    result = manager.container_operation("rename", "old-name", new_name="new-name")
    output = console.export_text()

    assert result is True
    assert "Container old-name renamed successfully" in output
    assert "renameed successfully" not in output
