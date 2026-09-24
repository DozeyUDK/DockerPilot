"""Focused regression tests for deployment leaf-service extraction."""

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import deployment_config_io
from dockerpilot.deployment_service import DeploymentServiceMixin


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120)


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def debug(self, message):
        self.messages.append(("debug", str(message)))


def test_create_dockerfile_template_leaf_writes_selected_template(tmp_path):
    logger = _Logger()
    host = SimpleNamespace(
        _DOCKERFILE_TEMPLATE_BODIES={"python": "FROM python:3.12-slim\n"},
        console=_console(),
        logger=logger,
        _is_dockerfile_candidate=lambda _path: False,
    )

    assert deployment_config_io.create_dockerfile_template(host, str(tmp_path), "python") is True
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "FROM python:3.12-slim\n"
    assert any("Created Dockerfile template" in message for _level, message in logger.messages)


def test_create_deployment_config_leaf_uses_packaged_template(tmp_path):
    output = tmp_path / "deployment.yml"
    host = SimpleNamespace(console=_console(), logger=_Logger())

    assert deployment_config_io.create_deployment_config(host, str(output)) is True
    text = output.read_text(encoding="utf-8")
    assert "deployment:" in text


def test_build_and_history_facades_delegate(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    host.console = object()
    host.logger = object()
    calls = []

    monkeypatch.setattr(
        "dockerpilot.deployment_service._build_image_enhanced_impl",
        lambda actual_host, image_tag, build_config: calls.append(
            ("enhanced", actual_host, image_tag, build_config)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.deployment_service._build_image_standalone_impl",
        lambda actual_host, dockerfile_path, tag, **kwargs: calls.append(
            ("standalone", actual_host, dockerfile_path, tag, kwargs)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.deployment_service._show_deployment_history_impl",
        lambda console, logger, limit: calls.append(("history", console, logger, limit)) or "shown",
    )

    assert host._build_image_enhanced("demo:v1", {"pull": False}) is True
    assert host.build_image_standalone(
        ".",
        "demo:v1",
        no_cache=True,
        pull=False,
        pull_if_missing=True,
        generate_template="python",
    ) is True
    assert host.show_deployment_history(7) == "shown"

    assert calls[0] == ("enhanced", host, "demo:v1", {"pull": False})
    assert calls[1][0:4] == ("standalone", host, ".", "demo:v1")
    assert calls[1][4] == {
        "no_cache": True,
        "pull": False,
        "pull_if_missing": True,
        "generate_template": "python",
    }
    assert calls[2] == ("history", host.console, host.logger, 7)
