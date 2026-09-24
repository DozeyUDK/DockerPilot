"""Regression tests for the extracted canary deployment strategy."""

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.deployment_strategies import canary as canary_strategy
from dockerpilot.models import DeploymentConfig


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120)


class _Logger:
    def info(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass

    def debug(self, _message):
        pass


def test_facade_delegates_canary_strategy(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    config = object()
    build_config = {"pull": False}
    seen = {}

    def fake_strategy(actual_host, actual_config, actual_build):
        seen.update(host=actual_host, config=actual_config, build=actual_build)
        return "sentinel"

    monkeypatch.setattr(
        "dockerpilot.deployment_service._canary_deploy_strategy",
        fake_strategy,
    )
    assert host._canary_deploy(config, build_config) == "sentinel"
    assert seen == {"host": host, "config": config, "build": build_config}


def test_canary_prepare_failure_stops_before_container_changes():
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        _prepare_image=lambda *_args: (False, "image unavailable"),
    )
    config = DeploymentConfig(
        image_tag="demo:v1",
        container_name="demo",
        port_mapping={"80": "8080"},
        environment={},
        volumes={},
    )

    assert canary_strategy.canary_deploy(host, config, {}) is False
