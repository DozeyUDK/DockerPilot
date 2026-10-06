"""Tests for DockerPilot CLI bootstrap behavior."""

import runpy

import pytest

import dockerpilot.main as main_module
import dockerpilot.pilot as pilot_module


def test_help_does_not_initialize_dockerpilot(monkeypatch):
    calls = {"parser": 0, "pilot": 0}

    class DummyParser:
        def parse_args(self, argv):
            calls["parser"] += 1
            assert argv == ["--help"]

    def fake_build_cli_parser():
        return DummyParser()

    class DummyPilot:
        def __init__(self, *args, **kwargs):
            calls["pilot"] += 1

    monkeypatch.setattr(main_module, "build_cli_parser", fake_build_cli_parser)
    monkeypatch.setattr(main_module, "DockerPilotEnhanced", DummyPilot)

    main_module.main(["--help"])

    assert calls == {"parser": 1, "pilot": 0}



def test_permission_error_is_rendered_without_traceback(monkeypatch, capsys):
    log_path = "/home/dozey/homeassistant/docker_pilot.log"

    class FailingPilot:
        def __init__(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied", log_path)

    monkeypatch.setattr(main_module, "DockerPilotEnhanced", FailingPilot)

    result = main_module.main(["--log-level", "INFO"])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert "DockerPilot could not access a required path." in captured.err
    assert f"Path: {log_path}" in captured.err
    assert "Reason: Permission denied (errno 13)" in captured.err
    assert "owner/group and read/write permissions" in captured.err
    assert "Traceback" not in captured.err


def test_permission_error_from_command_execution_is_also_clean(monkeypatch, capsys):
    path = "/srv/dockerpilot/config.yml"

    class FailingPilot:
        def __init__(self, *args, **kwargs):
            pass

        def run_cli(self):
            raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(main_module, "DockerPilotEnhanced", FailingPilot)

    result = main_module.main([])
    captured = capsys.readouterr()

    assert result == 1
    assert f"Path: {path}" in captured.err
    assert "Permission denied" in captured.err
    assert "Traceback" not in captured.err



def test_python_m_dockerpilot_main_propagates_permission_exit(monkeypatch):
    class FailingPilot:
        def __init__(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied", "/tmp/docker_pilot.log")

    monkeypatch.setattr(pilot_module, "DockerPilotEnhanced", FailingPilot)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("dockerpilot.main", run_name="__main__")

    assert exc_info.value.code == 1


def test_python_m_dockerpilot_propagates_main_return(monkeypatch):
    monkeypatch.setattr(main_module, "main", lambda argv=None: 1)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("dockerpilot", run_name="__main__")

    assert exc_info.value.code == 1
