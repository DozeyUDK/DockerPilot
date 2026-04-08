"""Smoke tests for DockerPilotExtras setup checks."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path


def _load_check_web_setup_module():
    module_path = Path(__file__).resolve().parents[1] / "DockerPilotExtras" / "check_web_setup.py"
    spec = importlib.util.spec_from_file_location("dockerpilotextras_check_web_setup", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@dataclass
class _RunResult:
    returncode: int
    stdout: str = ""


def test_check_node_success(monkeypatch):
    module = _load_check_web_setup_module()

    def fake_run(command, **_kwargs):
        if command[0] == "node":
            return _RunResult(returncode=0, stdout="v20.11.0\n")
        if command[0] == "npm":
            return _RunResult(returncode=0, stdout="10.9.0\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.check_node() is True


def test_check_node_missing_binary(monkeypatch):
    module = _load_check_web_setup_module()

    def fake_run(_command, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.check_node() is False


def test_check_dockerpilot_timeout(monkeypatch):
    module = _load_check_web_setup_module()

    def fake_run(_command, **_kwargs):
        raise module.subprocess.TimeoutExpired(cmd="dockerpilot", timeout=1)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.check_dockerpilot() is False


def test_main_returns_zero_when_required_checks_pass_and_optional_warns(monkeypatch):
    module = _load_check_web_setup_module()

    monkeypatch.setattr(module, "check_python_version", lambda: True)
    monkeypatch.setattr(module, "check_python_dependencies", lambda: True)
    monkeypatch.setattr(module, "check_node", lambda: True)
    monkeypatch.setattr(module, "check_frontend_dependencies", lambda: False)  # warning-only
    monkeypatch.setattr(module, "check_docker", lambda: True)
    monkeypatch.setattr(module, "check_dockerpilot", lambda: True)

    assert module.main() == 0


def test_main_returns_nonzero_when_required_check_fails(monkeypatch):
    module = _load_check_web_setup_module()

    monkeypatch.setattr(module, "check_python_version", lambda: True)
    monkeypatch.setattr(module, "check_python_dependencies", lambda: True)
    monkeypatch.setattr(module, "check_node", lambda: True)
    monkeypatch.setattr(module, "check_frontend_dependencies", lambda: True)
    monkeypatch.setattr(module, "check_docker", lambda: False)
    monkeypatch.setattr(module, "check_dockerpilot", lambda: True)

    assert module.main() == 1
