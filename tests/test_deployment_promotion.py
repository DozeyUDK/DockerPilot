"""Regression tests for extracted environment-promotion workflows."""

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import deployment_promotion
from dockerpilot.deployment_service import DeploymentServiceMixin


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def test_facade_delegates_environment_promotion(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    seen = {}

    def fake_impl(actual_host, source_env, target_env, *, config_path=None, skip_backup=False):
        seen.update(
            host=actual_host,
            source=source_env,
            target=target_env,
            config_path=config_path,
            skip_backup=skip_backup,
        )
        return "sentinel"

    monkeypatch.setattr("dockerpilot.deployment_service._environment_promotion_impl", fake_impl)
    assert host.environment_promotion(
        "dev",
        "prod",
        config_path="deployment.yml",
        skip_backup=True,
    ) == "sentinel"
    assert seen == {
        "host": host,
        "source": "dev",
        "target": "prod",
        "config_path": "deployment.yml",
        "skip_backup": True,
    }


def test_facade_delegates_pre_and_post_promotion_helpers(monkeypatch):
    host = DeploymentServiceMixin.__new__(DeploymentServiceMixin)
    config = object()
    calls = []

    monkeypatch.setattr(
        "dockerpilot.deployment_service._run_pre_promotion_checks_impl",
        lambda actual_host, source, target: calls.append(("pre", actual_host, source, target)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.deployment_service._run_post_promotion_validation_impl",
        lambda actual_host, env, actual_config: calls.append(("post", actual_host, env, actual_config)) or True,
    )

    assert host._run_pre_promotion_checks("dev", "staging") is True
    assert host._run_post_promotion_validation("staging", config) is True
    assert calls == [
        ("pre", host, "dev", "staging"),
        ("post", host, "staging", config),
    ]


def test_invalid_environment_stops_before_config_or_docker_io():
    host = SimpleNamespace(console=_console())
    assert deployment_promotion.environment_promotion(
        host,
        "unknown",
        "prod",
        config_path="does-not-matter.yml",
    ) is False
