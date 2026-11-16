"""Copy plan and finalization contracts for the shared migration executor."""
import logging
import shlex
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "DockerPilotExtras"))
from backend.resources.migration import create_migration_resource

SOURCE = "a" * 64
DESTINATION = "b" * 64
IMAGE = "sha256:" + "c" * 64
TARGET = {"id": "node", "name": "Node"}
PAYLOAD = {
    "source_server_id": "local", "target_server_id": "node", "source_container_id": SOURCE[:12],
    "source_engine_id": "source-engine", "container_name": "demo", "copy_only": True,
}


@pytest.fixture
def copy_host():
    commands = []
    responses = {
        "info": "target-engine", "ps": "", "version": "linux/amd64", "image": IMAGE,
        "create": DESTINATION, "inspect": "created", "start": DESTINATION,
    }

    def docker_command(target, command, **kwargs):
        assert target == TARGET
        args = shlex.split(command)
        commands.append(args)
        return responses[args[0]]

    source = Mock(name="source")
    source.name = "demo"
    source.id = SOURCE
    source.status = "running"
    source.attrs = {
        "Config": {"Image": "example:test", "Env": ["TOKEN=secret"], "User": "1000", "WorkingDir": "/app",
                   "Labels": {"com.docker.compose.project": "test-stack"}},
        "HostConfig": {"NetworkMode": "bridge", "PortBindings": {"53/udp": [{"HostIp": "127.0.0.1", "HostPort": "1053"}]},
                       "RestartPolicy": {"Name": "always"}},
        "Mounts": [{"Type": "bind", "Source": "/tmp/data", "Destination": "/data"}],
    }
    source.image.attrs = {"Os": "linux", "Architecture": "amd64"}
    client = SimpleNamespace(info=lambda: {"ID": "source-engine"}, containers=SimpleNamespace(get=lambda _: source))
    resource = create_migration_resource(
        Resource=object, app=SimpleNamespace(logger=logging.getLogger(__name__)), request=None,
        datetime_cls=datetime, migration_progress={}, migration_cancel_flags={},
        load_servers_config=lambda: {"servers": [TARGET]}, get_dockerpilot=lambda: SimpleNamespace(client=client),
        execute_command_via_ssh=Mock(), execute_docker_command_via_ssh=docker_command,
        save_deployment_config=Mock(), infer_port_mapping_for_host_network=Mock(),
    )()
    resource._check_port_availability = Mock(return_value=[])
    return resource, source, commands, responses


def test_plan_and_copy_only_create_then_explicitly_start(copy_host):
    resource, source, commands, _ = copy_host
    plan = resource.copy_plan(PAYLOAD)
    assert plan["ports"] == ["127.0.0.1:1053:53/udp"]
    assert plan["mounts"] == [{"destination": "/data", "type": "bind", "copy": False}]
    assert plan["compose_project"] == "test-stack"
    assert "secret" not in repr(plan)
    assert {cmd[0] for cmd in commands} == {"info", "ps", "version"}

    config = resource._copy_configuration(source, TARGET, False)
    progress = Mock()
    result = resource._create_stopped_copy(source, TARGET, config, False, IMAGE, "target-engine",
                                           Mock(), Mock(), progress)
    progress.begin_finalization.assert_called_once_with()
    create = commands[-1]
    assert create[0] == "create"
    assert "--restart" not in create and "-v" not in create and "-d" not in create
    assert ["-p", "127.0.0.1:1053:53/udp"] == create[create.index("-p"):][:2]
    assert create[create.index("--user") + 1] == "1000"
    assert create[-1] == IMAGE
    assert not {"stop", "rm", "run", "start"} & {cmd[0] for cmd in commands}
    source.stop.assert_not_called()
    resource.start_copy({**result, "target_server_id": "node"})
    assert commands[-1] == ["start", DESTINATION]


def test_collision_and_engine_mismatch_block_copy(copy_host):
    resource, source, commands, responses = copy_host
    responses["ps"] = "demo\nanother\n"
    with pytest.raises(ValueError, match="already exists"):
        resource.copy_plan(PAYLOAD)
    with pytest.raises(ValueError, match="already exists"):
        resource._create_stopped_copy(source, TARGET, {}, False, IMAGE, "target-engine", Mock(), Mock(), Mock())
    with pytest.raises(ValueError, match="distinct hosts"):
        resource.copy_plan({**PAYLOAD, "source_engine_id": "other-engine"})
    assert {cmd[0] for cmd in commands} <= {"info", "ps"}


def test_copy_preserves_source_security_restrictions(copy_host):
    resource, source, commands, _ = copy_host
    source.attrs["HostConfig"].update(CapDrop=["ALL", "NET_RAW"], ReadonlyRootfs=True)
    config = resource._copy_configuration(source, TARGET, False)
    resource._create_stopped_copy(source, TARGET, config, False, IMAGE, "target-engine", Mock(), Mock(), Mock())

    create = commands[-1]
    assert "--read-only" in create
    assert [create[index + 1] for index, arg in enumerate(create) if arg == "--cap-drop"] == ["ALL", "NET_RAW"]


@pytest.mark.parametrize(("mount_options", "suffix"), [
    ({"Mode": "", "RW": False}, ":ro"),
    ({"Mode": "", "RW": True}, ""),
    ({"Mode": "ro"}, ":ro"),
])
def test_copy_named_volumes_use_fresh_destination_names(copy_host, mount_options, suffix):
    resource, source, commands, _ = copy_host
    source.status = "exited"
    source.attrs["Mounts"] = [{"Type": "volume", "Name": "source-data", "Source": "/data", "Destination": "/data", **mount_options}]
    config = resource._copy_configuration(source, TARGET, True)
    transfer = resource._migrate_mount_data_between_servers = Mock(return_value={"total_migrated": 1})
    resource._create_stopped_copy(source, TARGET, config, True, IMAGE, "target-engine", Mock(), Mock(), Mock())
    mount = transfer.call_args.args[1]["mounts"][0]
    assert mount["name"] == "source-data"
    assert mount["copy_target_volume"].startswith("dockerpilot-copy-")
    assert commands[-1][commands[-1].index("-v") + 1] == mount["copy_target_volume"] + ":/data" + suffix


@pytest.mark.parametrize("include_data", [False, True])
def test_copy_recreates_tmpfs_with_original_options(copy_host, include_data):
    resource, source, commands, _ = copy_host
    source.attrs["HostConfig"].update(
        ReadonlyRootfs=True, Tmpfs={"/tmp": "rw,noexec,nosuid,size=64m", "/run": ""}
    )
    source.attrs["Mounts"].append({"Type": "tmpfs", "Destination": "/tmp"})
    payload = {**PAYLOAD, "include_data": include_data}
    plan = resource.copy_plan(payload)
    assert [mount["destination"] for mount in plan["mounts"] if mount["type"] == "tmpfs"] == ["/tmp", "/run"]

    config = resource._copy_configuration(source, TARGET, include_data)
    resource._migrate_mount_data_between_servers = transfer = Mock()
    resource._create_stopped_copy(
        source, TARGET, config, include_data, IMAGE, "target-engine", Mock(), Mock(), Mock()
    )
    create = commands[-1]
    assert "--read-only" in create
    assert [create[i + 1] for i, arg in enumerate(create) if arg == "--tmpfs"] == [
        "/tmp:rw,noexec,nosuid,size=64m", "/run"
    ]
    transfer.assert_not_called()


@pytest.mark.parametrize("inspect_field", ["HostConfig", "Mounts"])
def test_copy_refuses_tmpfs_without_replayable_configuration(copy_host, inspect_field):
    resource, source, commands, _ = copy_host
    mount = {"Type": "tmpfs", "Target": "/tmp", "Destination": "/tmp"}
    if inspect_field == "HostConfig":
        source.attrs["HostConfig"]["Mounts"] = [mount]
    else:
        source.attrs["Mounts"].append(mount)
    with pytest.raises(ValueError, match="require manual deployment"):
        resource.copy_plan(PAYLOAD)
    assert not any(cmd[0] == "create" for cmd in commands)
