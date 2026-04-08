"""Regression tests for critical CLI handler flows."""

from argparse import Namespace

import pytest

from dockerpilot.cli.handlers import dispatch_cli_args, handle_container_cli, handle_deploy_cli


class DummyConsole:
    def __init__(self) -> None:
        self.lines = []

    def print(self, *parts, **_kwargs) -> None:
        self.lines.append(" ".join(str(part) for part in parts))


class DummyParser:
    def __init__(self) -> None:
        self.help_called = False

    def print_help(self) -> None:
        self.help_called = True


class DummyLogger:
    def __init__(self) -> None:
        self.errors = []

    def error(self, message) -> None:
        self.errors.append(str(message))


class DummyPilot:
    def __init__(self) -> None:
        self.console = DummyConsole()
        self.logger = DummyLogger()
        self.run_calls = []
        self.exec_calls = []
        self.quick_calls = []
        self.promote_calls = []
        self.exec_results = {}

    def _parse_multi_target(self, target_string):
        return [item.strip() for item in str(target_string).split(",") if item.strip()]

    def _run_container_interactive(self, args):
        self.run_calls.append(("interactive", args))
        return True

    def run_new_container(self, **kwargs):
        self.run_calls.append(kwargs)
        return True

    def exec_container(self, container, command):
        self.exec_calls.append((container, command))
        return self.exec_results.get(container, True)

    def quick_deploy(self, **kwargs):
        self.quick_calls.append(kwargs)
        return True

    def environment_promotion(self, source, target, config_path, skip_backup):
        self.promote_calls.append((source, target, config_path, skip_backup))
        return True


def test_handle_container_run_parses_ports_env_and_volumes():
    pilot = DummyPilot()
    args = Namespace(
        container_action="run",
        interactive=False,
        image="nginx:latest",
        name="web",
        port=["80:8080", "443:8443"],
        env=["FOO=bar", "BAR=baz"],
        volume=["/host/data:/data", "/host/logs:/logs:ro"],
        command="sleep 5",
        restart="unless-stopped",
        network="bridge",
        privileged=True,
        cpu_limit="1.0",
        memory_limit="512m",
    )

    handle_container_cli(pilot, args)

    assert len(pilot.run_calls) == 1
    call = pilot.run_calls[0]
    assert call["image_name"] == "nginx:latest"
    assert call["name"] == "web"
    assert call["ports"] == {"80": "8080", "443": "8443"}
    assert call["environment"] == {"FOO": "bar", "BAR": "baz"}
    assert call["volumes"] == {
        "/host/data": "/data",
        "/host/logs": {"bind": "/logs", "mode": "ro"},
    }
    assert call["restart_policy"] == "unless-stopped"
    assert call["network"] == "bridge"
    assert call["privileged"] is True
    assert call["cpu_limit"] == "1.0"
    assert call["memory_limit"] == "512m"


def test_handle_container_exec_runs_all_targets_and_continues_on_failure():
    pilot = DummyPilot()
    pilot.exec_results = {"mongo": False, "api": True}
    args = Namespace(container_action="exec", name="mongo,api", command="/bin/bash")

    handle_container_cli(pilot, args)

    assert pilot.exec_calls == [("mongo", "/bin/bash"), ("api", "/bin/bash")]
    assert any("Failed to exec in mongo" in line for line in pilot.console.lines)


def test_handle_deploy_quick_parses_and_calls_quick_deploy():
    pilot = DummyPilot()
    args = Namespace(
        deploy_action="quick",
        dockerfile_path=".",
        image_tag="app:v2",
        container_name="app",
        port="80:8080",
        env=["FOO=bar"],
        volume=["/host/data:/data"],
        yaml_config=None,
        no_cleanup=True,
    )

    handle_deploy_cli(pilot, args)

    assert len(pilot.quick_calls) == 1
    call = pilot.quick_calls[0]
    assert call["dockerfile_path"] == "."
    assert call["image_tag"] == "app:v2"
    assert call["container_name"] == "app"
    assert call["port_mapping"] == {"80": "8080"}
    assert call["environment"] == {"FOO": "bar"}
    assert call["volumes"] == {"/host/data": {"bind": "/data", "mode": "rw"}}
    assert call["cleanup_old_image"] is False


def test_handle_deploy_quick_rejects_invalid_env_format():
    pilot = DummyPilot()
    args = Namespace(
        deploy_action="quick",
        dockerfile_path=".",
        image_tag="app:v2",
        container_name="app",
        port=None,
        env=["BROKEN"],
        volume=None,
        yaml_config=None,
        no_cleanup=False,
    )

    with pytest.raises(SystemExit) as exc:
        handle_deploy_cli(pilot, args)

    assert exc.value.code == 1
    assert pilot.quick_calls == []


def test_dispatch_promote_passes_default_skip_backup_false():
    pilot = DummyPilot()
    parser = DummyParser()
    args = Namespace(command="promote", source="dev", target="staging", config="promote.yml")

    dispatch_cli_args(pilot, args, parser)

    assert pilot.promote_calls == [("dev", "staging", "promote.yml", False)]


def test_dispatch_promote_exits_on_failure():
    pilot = DummyPilot()
    parser = DummyParser()
    args = Namespace(command="promote", source="dev", target="prod", config=None)

    def fail_promotion(source, target, config_path, skip_backup):
        pilot.promote_calls.append((source, target, config_path, skip_backup))
        return False

    pilot.environment_promotion = fail_promotion

    with pytest.raises(SystemExit) as exc:
        dispatch_cli_args(pilot, args, parser)

    assert exc.value.code == 1
    assert pilot.promote_calls == [("dev", "prod", None, False)]
