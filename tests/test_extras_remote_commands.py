from pathlib import Path
import shlex
import sys

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.remote_commands import (
    build_dockerpilot_deploy_command,
    build_remote_file_write_command,
)


def test_remote_file_write_command_keeps_path_inside_python_argument():
    hostile_path = "/tmp/config'; touch /tmp/pwn; #.yml"
    command = build_remote_file_write_command(hostile_path, "hello")
    argv = shlex.split(command)
    assert argv[:2] == ["python3", "-c"]
    assert len(argv) == 3
    assert hostile_path in argv[2]
    assert "touch /tmp/pwn" in argv[2]


def test_deploy_command_quotes_hostile_path_as_one_argument():
    hostile_path = "/tmp/x; touch /tmp/pwn.yml"
    command = build_dockerpilot_deploy_command(hostile_path, "rolling", skip_backup=True)
    assert shlex.split(command) == [
        "dockerpilot",
        "deploy",
        "config",
        hostile_path,
        "--type",
        "rolling",
        "--skip-backup",
    ]
