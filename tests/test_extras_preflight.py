"""Unit tests for DockerPilotExtras preflight module."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_preflight_module():
    module_path = Path(__file__).resolve().parents[1] / "DockerPilotExtras" / "backend" / "preflight.py"
    spec = importlib.util.spec_from_file_location("dockerpilotextras_preflight", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@dataclass
class _RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def test_check_python_version_ok(monkeypatch):
    module = _load_preflight_module()
    monkeypatch.setattr(module.sys, "version_info", SimpleNamespace(major=3, minor=11, micro=2))

    ok, version = module.check_python_version()

    assert ok is True
    assert version == "3.11.2"


def test_check_python_version_too_low(monkeypatch):
    module = _load_preflight_module()
    monkeypatch.setattr(module.sys, "version_info", SimpleNamespace(major=3, minor=8, micro=18))

    ok, version = module.check_python_version()

    assert ok is False
    assert version == "3.8.18"


def test_check_command_version_not_found(monkeypatch):
    module = _load_preflight_module()

    def fake_run(_command, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    ok, details = module.check_command_version(["node", "--version"])

    assert ok is False
    assert details == "not found"


def test_run_preflight_checks_marks_optional_warning_only(tmp_path, monkeypatch):
    module = _load_preflight_module()

    monkeypatch.setattr(module, "check_python_version", lambda: (True, "3.11.0"))
    monkeypatch.setattr(module, "check_python_imports", lambda _mods: (True, []))
    def fake_command(command):
        if command[:2] == ["node", "--version"]:
            return True, "v20.12.2"
        if command[:2] == ["npm", "--version"]:
            return True, "10.8.1"
        return True, "ok"
    monkeypatch.setattr(module, "check_command_version", fake_command)
    monkeypatch.setattr(module, "check_frontend_dependencies", lambda _base: (False, "missing node_modules"))

    result = module.run_preflight_checks(tmp_path)

    assert result["success"] is True
    assert result["required_failed"] == []
    assert result["warnings"] == ["frontend_dependencies"]
    assert result["checks"]["frontend_dependencies"]["ok"] is False


def test_run_preflight_checks_fails_when_required_missing(tmp_path, monkeypatch):
    module = _load_preflight_module()

    monkeypatch.setattr(module, "check_python_version", lambda: (True, "3.11.0"))
    monkeypatch.setattr(module, "check_python_imports", lambda _mods: (True, []))

    def fake_command(command):
        if command[:2] == ["docker", "--version"]:
            return False, "docker missing"
        return True, "ok"

    monkeypatch.setattr(module, "check_command_version", fake_command)
    monkeypatch.setattr(module, "check_frontend_dependencies", lambda _base: (True, "installed"))

    result = module.run_preflight_checks(tmp_path)

    assert result["success"] is False
    assert "docker" in result["required_failed"]
