from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services import ssh_execution


def test_local_execution_never_uses_shell(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ssh_execution.subprocess, "run", fake_run)
    output = ssh_execution.execute_command(
        {"id": "local"},
        "docker ps --format '{{.Names}}'",
        ssh_available=False,
        known_hosts_path=tmp_path / "known_hosts",
    )

    assert output == "ok\n"
    assert observed["argv"] == ["docker", "ps", "--format", "{{.Names}}"]
    assert observed["shell"] is False


def test_explicit_script_uses_bash_argv_but_still_not_shell_true(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ssh_execution.subprocess, "run", fake_run)
    script = "printf '%s\\n' safe | cat"
    output = ssh_execution.execute_script(
        {"id": "local"},
        script,
        ssh_available=False,
        known_hosts_path=tmp_path / "known_hosts",
    )

    assert output == "ok\n"
    assert observed["argv"] == ["bash", "-lc", script]
    assert observed["shell"] is False


def test_docker_builder_quotes_shell_metacharacters():
    command = ssh_execution.build_docker_command("ps; touch /tmp/pwn", use_sudo=False)
    assert shlex.split(command) == ["docker", "ps;", "touch", "/tmp/pwn"]
    assert command.startswith("docker ")


def test_docker_builder_preserves_format_argument():
    command = ssh_execution.build_docker_command("ps -a --format '{{.Names}}\\t{{.Image}}'")
    assert shlex.split(command) == ["docker", "ps", "-a", "--format", "{{.Names}}\\t{{.Image}}"]
