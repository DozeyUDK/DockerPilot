"""Tests for the optional DockerPilot TUI helpers."""

import pytest

from dockerpilot.cli.parser import build_cli_parser
import dockerpilot.cli.tui as tui_module
from dockerpilot.cli.tui import (
    build_command_argv,
    build_command_tree,
    capture_cli_execution,
    collect_argument_specs,
    execute_cli_argv,
    format_container_targets,
    format_image_targets,
    infer_resource_selector,
    should_launch_in_external_terminal,
    should_refresh_targets_after_command,
    TuiCommandHandoff,
    selector_height,
    should_tui_require_value,
)


def _find_leaf(commands, path):
    current = commands
    node = None
    for segment in path:
        node = next(item for item in current if item.name == segment)
        current = node.children
    return node


def test_build_command_tree_exposes_nested_commands_without_tui_recursion():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    top_level = [node.name for node in commands]
    assert "tui" not in top_level
    assert "container" in top_level

    run_node = _find_leaf(commands, ["container", "run"])
    assert run_node is not None
    assert run_node.is_leaf is True
    assert any(argument.dest == "image" for argument in run_node.arguments)
    assert any(argument.dest == "port" for argument in run_node.arguments)
    assert all(argument.dest != "interactive" for argument in run_node.arguments)


def test_build_command_argv_serializes_global_flags_repeated_options_and_positionals():
    parser = build_cli_parser()
    global_arguments = collect_argument_specs(parser, exclude_dests={"version"})
    commands = build_command_tree(parser, exclude_commands={"tui"})
    quick_node = _find_leaf(commands, ["deploy", "quick"])

    argv = build_command_argv(
        quick_node,
        {
            "dockerfile_path": ".",
            "image_tag": "demo:v2",
            "container_name": "demo-app",
            "env": "FOO=bar\nBAR=baz",
            "volume": "/host/data:/data",
            "no_cleanup": True,
        },
        global_arguments=global_arguments,
        global_values={"log_level": "DEBUG"},
    )

    assert argv == [
        "--log-level",
        "DEBUG",
        "deploy",
        "quick",
        "--dockerfile-path",
        ".",
        "--image-tag",
        "demo:v2",
        "--container-name",
        "demo-app",
        "--env",
        "FOO=bar",
        "--env",
        "BAR=baz",
        "--volume",
        "/host/data:/data",
        "--no-cleanup",
    ]


def test_build_command_argv_handles_multi_positional_arguments():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})
    dashboard_node = _find_leaf(commands, ["monitor", "dashboard"])

    argv = build_command_argv(
        dashboard_node,
        {
            "containers": "web api worker",
            "duration": "120",
        },
    )

    assert argv == ["monitor", "dashboard", "web", "api", "worker", "--duration", "120"]


def test_collect_argument_specs_treats_optional_star_positionals_as_not_required():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})
    dashboard_node = _find_leaf(commands, ["monitor", "dashboard"])
    containers_argument = next(argument for argument in dashboard_node.arguments if argument.dest == "containers")

    assert containers_argument.required is False


def test_tui_promotes_required_fields_for_container_run_and_logs():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    run_node = _find_leaf(commands, ["container", "run"])
    run_image = next(argument for argument in run_node.arguments if argument.dest == "image")
    run_name = next(argument for argument in run_node.arguments if argument.dest == "name")
    assert should_tui_require_value(run_node, run_image) is True
    assert should_tui_require_value(run_node, run_name) is True

    logs_node = _find_leaf(commands, ["container", "logs"])
    logs_name = next(argument for argument in logs_node.arguments if argument.dest == "name")
    assert should_tui_require_value(logs_node, logs_name) is True


def test_build_command_argv_joins_selected_targets_for_comma_based_cli_arguments():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})
    restart_node = _find_leaf(commands, ["container", "restart"])

    argv = build_command_argv(
        restart_node,
        {
            "name": ["quizzical_goldstine", "green_api"],
            "timeout": "10",
        },
    )

    assert argv == ["container", "restart", "quizzical_goldstine,green_api", "--timeout", "10"]


def test_infer_resource_selector_marks_container_and_image_target_commands():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    stop_node = _find_leaf(commands, ["container", "stop"])
    stop_name = next(argument for argument in stop_node.arguments if argument.dest == "name")
    stop_selector = infer_resource_selector(stop_node, stop_name)
    assert stop_selector.resource_type == "container"
    assert stop_selector.mode == "multi"

    remove_image_node = _find_leaf(commands, ["container", "remove-image"])
    remove_image_name = next(argument for argument in remove_image_node.arguments if argument.dest == "name")
    remove_image_selector = infer_resource_selector(remove_image_node, remove_image_name)
    assert remove_image_selector.resource_type == "image"
    assert remove_image_selector.mode == "multi"

    rename_node = _find_leaf(commands, ["container", "rename"])
    rename_name = next(argument for argument in rename_node.arguments if argument.dest == "name")
    rename_selector = infer_resource_selector(rename_node, rename_name)
    assert rename_selector.resource_type == "container"
    assert rename_selector.mode == "single"


@pytest.mark.parametrize(
    "path,dest,resource_type,mode",
    [
        (("container", "remove-image"), "name", "image", "multi"),
        (("container", "start"), "name", "container", "multi"),
        (("container", "stop"), "name", "container", "multi"),
        (("container", "restart"), "name", "container", "multi"),
        (("container", "remove"), "name", "container", "multi"),
        (("container", "pause"), "name", "container", "multi"),
        (("container", "unpause"), "name", "container", "multi"),
        (("container", "stop-remove"), "name", "container", "multi"),
        (("container", "exec"), "name", "container", "multi"),
        (("container", "logs"), "name", "container", "multi"),
        (("container", "rename"), "name", "container", "single"),
        (("container", "exec-simple"), "name", "container", "single"),
        (("monitor", "dashboard"), "containers", "container", "multi"),
        (("monitor", "live"), "container", "container", "single"),
        (("monitor", "stats"), "container", "container", "single"),
        (("backup", "container-data"), "container", "container", "single"),
        (("backup", "restore-data"), "container", "container", "single"),
    ],
)
def test_resource_selector_audit_covers_all_live_docker_target_fields(
    path, dest, resource_type, mode
):
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})
    node = _find_leaf(commands, list(path))
    argument = next(argument for argument in node.arguments if argument.dest == dest)

    selector = infer_resource_selector(node, argument)

    assert selector is not None
    assert selector.resource_type == resource_type
    assert selector.mode == mode


def test_resource_selector_audit_does_not_capture_new_resource_names():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    run_node = _find_leaf(commands, ["container", "run"])
    run_name = next(argument for argument in run_node.arguments if argument.dest == "name")
    run_image = next(argument for argument in run_node.arguments if argument.dest == "image")
    assert infer_resource_selector(run_node, run_name) is None
    assert infer_resource_selector(run_node, run_image) is None

    quick_node = _find_leaf(commands, ["deploy", "quick"])
    quick_name = next(argument for argument in quick_node.arguments if argument.dest == "container_name")
    assert infer_resource_selector(quick_node, quick_name) is None


def test_target_formatters_include_state_and_size_metadata():
    containers = format_container_targets(
        [
            {"name": "web", "state": "running", "image": "nginx:alpine"},
            {"name": "db", "state": "exited", "image": "postgres:16"},
        ]
    )
    images = format_image_targets(
        [
            {"tags": ["nginx:alpine", "nginx:latest"], "size": "24MB"},
            {"tags": ["postgres:16"], "size": "300MB"},
        ]
    )

    assert containers == [
        ("web [running] nginx:alpine", "web"),
        ("db [exited] postgres:16", "db"),
    ]
    assert images == [
        ("nginx:alpine [24MB]", "nginx:alpine"),
        ("nginx:latest [24MB]", "nginx:latest"),
        ("postgres:16 [300MB]", "postgres:16"),
    ]


def test_selector_height_gives_multi_option_lists_visible_viewport():
    assert selector_height(1, single=False) == 4
    assert selector_height(3, single=False) == 4
    assert selector_height(8, single=False) == 9
    assert selector_height(20, single=False) == 10
    assert selector_height(1, single=True) == 3
    assert selector_height(8, single=True) == 6


def test_terminal_execution_is_used_for_shell_and_live_monitoring():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    exec_node = _find_leaf(commands, ["container", "exec"])
    dashboard_node = _find_leaf(commands, ["monitor", "dashboard"])
    live_node = _find_leaf(commands, ["monitor", "live"])
    logs_node = _find_leaf(commands, ["container", "logs"])

    assert should_launch_in_external_terminal(exec_node) is True
    assert should_launch_in_external_terminal(dashboard_node) is True
    assert should_launch_in_external_terminal(live_node) is True
    assert should_launch_in_external_terminal(logs_node) is False


def test_only_resource_mutations_request_automatic_target_refresh():
    parser = build_cli_parser()
    commands = build_command_tree(parser, exclude_commands={"tui"})

    for path in [
        ["container", "run"],
        ["container", "stop"],
        ["container", "remove-image"],
        ["deploy", "quick"],
        ["promote"],
        ["build"],
    ]:
        assert should_refresh_targets_after_command(_find_leaf(commands, path)) is True

    for path in [
        ["container", "list"],
        ["container", "logs"],
        ["monitor", "stats"],
        ["deploy", "history"],
        ["backup", "restore"],
        ["backup", "restore-data"],
        ["validate"],
    ]:
        assert should_refresh_targets_after_command(_find_leaf(commands, path)) is False

    prune_node = _find_leaf(commands, ["container", "prune-images"])
    assert should_refresh_targets_after_command(prune_node, {"dry_run": False}) is True
    assert should_refresh_targets_after_command(prune_node, {"dry_run": True}) is False


def test_execute_cli_argv_reuses_the_regular_cli_dispatcher():
    parser = build_cli_parser()

    class FakePilot:
        def __init__(self):
            self.calls = []

        def list_containers(self, show_all=True, format_output="table"):
            self.calls.append(("list_containers", show_all, format_output))

    pilot = FakePilot()
    return_code = execute_cli_argv(pilot, parser, ["container", "list", "--all", "--format", "json"])

    assert return_code == 0
    assert pilot.calls == [("list_containers", True, "json")]


def test_capture_cli_execution_collects_output_from_the_regular_cli_dispatcher():
    parser = build_cli_parser()

    class FakeConsole:
        width = 120

        def __init__(self):
            self.lines = []

        def print(self, *parts, **_kwargs):
            self.lines.append(" ".join(str(part) for part in parts))

    class FakePilot:
        def __init__(self):
            self.console = FakeConsole()
            self.logger = type("Logger", (), {"handlers": []})()

        def list_containers(self, show_all=True, format_output="table"):
            self.console.print(f"listed containers all={show_all} format={format_output}")

    pilot = FakePilot()
    return_code, output = capture_cli_execution(pilot, parser, ["container", "list", "--all", "--format", "json"])

    assert return_code == 0
    assert "listed containers all=True format=json" in output


def test_run_tui_missing_textual_shows_literal_install_commands(monkeypatch):
    class FakeConsole:
        def __init__(self):
            self.messages = []

        def print(self, *parts, **kwargs):
            self.messages.append((" ".join(str(part) for part in parts), kwargs))

    class FakePilot:
        def __init__(self):
            self.console = FakeConsole()

    monkeypatch.setattr(tui_module, "TEXTUAL_AVAILABLE", False)
    pilot = FakePilot()

    with pytest.raises(SystemExit) as exc:
        tui_module.run_tui(pilot)

    assert exc.value.code == 1
    messages = [message for message, _kwargs in pilot.console.messages]
    assert any('python -m pip install -e ".[tui]"' in message for message in messages)
    assert any('python -m pip install "dockerpilot[tui]"' in message for message in messages)


def test_run_tui_relaunches_after_terminal_handoff(monkeypatch):
    parser = build_cli_parser()

    class FakeApp:
        run_calls = 0

        def __init__(self, active_parser, pilot, *, global_values=None):
            self.active_parser = active_parser
            self.pilot = pilot

        def run(self):
            FakeApp.run_calls += 1
            if FakeApp.run_calls == 1:
                return TuiCommandHandoff(argv=["container", "exec", "web", "--command", "/bin/bash"])
            return None

    executed = []

    def fake_execute_cli_argv(pilot, active_parser, argv):
        executed.append((pilot, active_parser, argv))
        return 0

    class FakeConsole:
        def __init__(self):
            self.lines = []

        def print(self, *parts, **_kwargs):
            self.lines.append(" ".join(str(part) for part in parts))

    class FakePilot:
        def __init__(self):
            self.console = FakeConsole()

    monkeypatch.setattr(tui_module, "TEXTUAL_AVAILABLE", True)
    monkeypatch.setattr(tui_module, "DockerPilotTUI", FakeApp, raising=False)
    monkeypatch.setattr(tui_module, "execute_cli_argv", fake_execute_cli_argv)

    pilot = FakePilot()
    tui_module.run_tui(pilot, parser)

    assert FakeApp.run_calls == 2
    assert executed == [(pilot, parser, ["container", "exec", "web", "--command", "/bin/bash"])]


def test_build_pull_can_be_disabled_and_untagged_images_are_selectable():
    parser = build_cli_parser()
    build = _find_leaf(build_command_tree(parser), ["build"])
    args = parser.parse_args(build_command_argv(build, {
        "dockerfile_path": ".", "tag": "demo:v1", "pull": False,
    }))
    assert args.pull is False
    assert parser.parse_args(["build", ".", "demo:v1"]).pull is True
    assert format_image_targets([{"id": "abc123", "tags": [], "size": "2MB"}]) == [
        ("abc123 [2MB]", "abc123"),
    ]


def test_every_tui_command_reaches_its_real_cli_handler():
    from unittest.mock import Mock

    routes = {
        ("container", "list"): "list_containers",
        ("container", "list-images"): "list_images",
        ("container", "remove-image"): "remove_image",
        ("container", "prune-images"): "prune_dangling_images",
        ("container", "start"): "container_operation",
        ("container", "stop"): "container_operation",
        ("container", "restart"): "container_operation",
        ("container", "remove"): "container_operation",
        ("container", "pause"): "container_operation",
        ("container", "unpause"): "container_operation",
        ("container", "rename"): "rename_container",
        ("container", "stop-remove"): "stop_and_remove_container",
        ("container", "run"): "run_new_container",
        ("container", "exec-simple"): "exec_command_non_interactive",
        ("container", "exec"): "exec_container",
        ("container", "logs"): "view_container_logs",
        ("monitor", "dashboard"): "monitor_containers_dashboard",
        ("monitor", "live"): "monitor_container_live",
        ("monitor", "stats"): "get_container_stats_once",
        ("monitor", "health"): "health_check_standalone",
        ("deploy", "config"): "deploy_from_config",
        ("deploy", "init"): "create_deployment_config",
        ("deploy", "history"): "show_deployment_history",
        ("deploy", "quick"): "quick_deploy",
        ("validate",): "validate_system_requirements",
        ("backup", "create"): "backup_deployment_state",
        ("backup", "restore"): "restore_deployment_state",
        ("backup", "container-data"): "backup_container_data",
        ("backup", "restore-data"): "restore_container_data",
        ("config", "export"): "export_configuration",
        ("config", "import"): "import_configuration",
        ("pipeline", "create"): "create_pipeline_config",
        ("test",): "run_integration_tests",
        ("promote",): "environment_promotion",
        ("alerts",): "setup_monitoring_alerts",
        ("docs",): "generate_documentation",
        ("build",): "build_image_standalone",
        ("checklist",): "create_production_checklist",
    }

    parser = build_cli_parser()
    groups = build_command_tree(parser, exclude_commands={"tui"})
    commands = {node.path: node for group in groups for node in (group.children or [group])}
    assert routes.keys() == commands.keys()

    for path, command in commands.items():
        backend = Mock()
        backend._parse_multi_target.side_effect = lambda text: text.split(",")
        backend.prune_dangling_images.return_value = {"images_deleted": 0}
        values = {
            spec.dest: "8080" if spec.dest == "port" else spec.choices[0] if spec.choices else "demo"
            for spec in command.arguments if should_tui_require_value(command, spec)
        }
        argv = build_command_argv(command, values)
        assert execute_cli_argv(backend, parser, argv, interactive=False) == 0, path
        assert getattr(backend, routes[path]).call_count == 1, path
        if path == ("container", "remove"):
            backend.container_operation.assert_called_once_with("remove", "demo", force=False, interactive=False)
