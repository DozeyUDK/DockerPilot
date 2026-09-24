"""Regression tests for the extracted blue/green deployment strategy."""

from io import StringIO
from types import SimpleNamespace

import docker
from rich.console import Console

from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.deployment_strategies import blue_green as blue_green_strategy
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


class _MissingContainers:
    def get(self, _name):
        raise docker.errors.NotFound("missing")


def test_facade_delegates_blue_green_strategy(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    config = object()
    build_config = {"pull": False}
    seen = {}

    def fake_strategy(actual_host, actual_config, actual_build, *, skip_backup=False):
        seen.update(
            host=actual_host,
            config=actual_config,
            build=actual_build,
            skip_backup=skip_backup,
        )
        return "sentinel"

    monkeypatch.setattr(
        "dockerpilot.deployment_service._blue_green_deploy_strategy",
        fake_strategy,
    )
    assert host._blue_green_deploy_enhanced(config, build_config, skip_backup=True) == "sentinel"
    assert seen == {
        "host": host,
        "config": config,
        "build": build_config,
        "skip_backup": True,
    }


def test_blue_green_early_cancel_clears_tracking_and_backup_helpers():
    cleanup_calls = []
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        client=SimpleNamespace(containers=_MissingContainers()),
        _current_deployment_container=None,
        _detect_health_check_endpoint=lambda _image: None,
        _resolve_runtime_network=lambda network: network,
        _check_cancel_flag=lambda: True,
        _cleanup_backup_containers=lambda: cleanup_calls.append(True),
    )
    config = DeploymentConfig(
        image_tag="demo:v1",
        container_name="demo",
        port_mapping={},
        environment={},
        volumes={},
        health_check_endpoint=None,
    )

    assert blue_green_strategy.blue_green_deploy(host, config, {}) is False
    assert host._current_deployment_container is None
    assert cleanup_calls == [True]
