"""Regression tests for low-level backup archive/runtime extraction."""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from dockerpilot import backup_archive
from dockerpilot.backup_restore import BackupRestoreMixin
from dockerpilot.execution_context import privileged_backup_authorization


class _Logger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", str(message)))

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


def _console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def test_backup_directory_non_sudo_path_is_progress_import_safe(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

        def communicate(self, *args, **kwargs):
            return "", ""

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(backup_archive.os, "access", lambda *_args: True)
    monkeypatch.setattr(backup_archive.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    host = SimpleNamespace(console=_console(), logger=_Logger())

    assert backup_archive.backup_directory(host, str(source), backup_file) is True


def test_cleanup_backup_containers_removes_exited_alpine_container():
    removed = []

    class FakeContainer:
        id = "1234567890abcdef"
        status = "exited"
        attrs = {}

        def reload(self):
            pass

        def remove(self):
            removed.append(self.id)

    host = SimpleNamespace(
        client=SimpleNamespace(
            containers=SimpleNamespace(list=lambda **_kwargs: [FakeContainer()])
        ),
        logger=_Logger(),
    )

    backup_archive.cleanup_backup_containers(host)

    assert removed == ["1234567890abcdef"]



def test_privileged_bind_mount_requires_explicit_extras_authorization(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"
    popen_calls = []

    monkeypatch.setattr(
        backup_archive,
        "path_requires_privileged_access",
        lambda _path: True,
    )
    monkeypatch.setattr(
        backup_archive.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    host = SimpleNamespace(logger=_Logger())

    with privileged_backup_authorization(False):
        assert backup_archive.backup_bind_mount_using_docker(
            host,
            str(source),
            backup_file,
        ) is False
    assert popen_calls == []
    assert any(
        "authorization required" in message.lower()
        for _level, message in host.logger.messages
    )


def test_privileged_direct_tar_requires_explicit_extras_authorization(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"

    monkeypatch.setattr(
        backup_archive,
        "path_requires_privileged_access",
        lambda _path: True,
    )

    host = SimpleNamespace(
        logger=_Logger(),
        console=_console(),
    )

    with privileged_backup_authorization(False):
        assert backup_archive.backup_directory(host, str(source), backup_file) is False
    assert any(
        "authorization required" in message.lower()
        for _level, message in host.logger.messages
    )


def test_privileged_docker_failure_keeps_trusted_cli_sudo_fallback(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"
    fallback_calls = []

    class FakeProcess:
        returncode = 1

        def poll(self):
            return 1

        def communicate(self):
            return "", "helper unavailable"

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr(
        backup_archive,
        "path_requires_privileged_access",
        lambda _path: True,
    )
    monkeypatch.setattr(
        backup_archive.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    host = SimpleNamespace(
        logger=_Logger(),
        _get_sudo_password=lambda: None,
        _backup_directory=lambda *args: fallback_calls.append(args) or True,
    )

    # No Extras authorization scope: trusted local CLI may fall back to sudo/tar.
    assert backup_archive.backup_bind_mount_using_docker(
        host,
        str(source),
        backup_file,
    ) is True
    assert len(fallback_calls) == 1
    assert fallback_calls[0][0] == str(source)


def test_extras_authorized_privileged_docker_failure_refuses_sudo_fallback(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"
    fallback_calls = []

    class FakeProcess:
        returncode = 1

        def poll(self):
            return 1

        def communicate(self):
            return "", "helper unavailable"

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr(
        backup_archive,
        "path_requires_privileged_access",
        lambda _path: True,
    )
    monkeypatch.setattr(
        backup_archive.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    host = SimpleNamespace(
        logger=_Logger(),
        _get_sudo_password=lambda: None,
        _backup_directory=lambda *args: fallback_calls.append(args) or True,
    )

    with privileged_backup_authorization(True):
        assert backup_archive.backup_bind_mount_using_docker(
            host,
            str(source),
            backup_file,
        ) is False

    assert fallback_calls == []
    assert any(
        "refusing direct sudo fallback" in message.lower()
        for _level, message in host.logger.messages
    )


def test_extras_bind_tar_failure_is_not_masked_by_existing_archive(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    backup_file = tmp_path / "backup.tar.gz"
    backup_file.write_bytes(b"partial")
    seen = {}

    class FakeProcess:
        returncode = 1

        def __init__(self, argv):
            seen["argv"] = argv

        def poll(self):
            return 1

        def communicate(self):
            return "", "tar: read error"

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr(
        backup_archive.subprocess,
        "Popen",
        lambda argv, **_kwargs: FakeProcess(argv),
    )
    monkeypatch.setattr(
        backup_archive,
        "path_requires_privileged_access",
        lambda _path: True,
    )

    host = SimpleNamespace(
        logger=_Logger(),
        _get_sudo_password=lambda: None,
        _backup_directory=lambda *_args: True,
    )

    with privileged_backup_authorization(True):
        assert backup_archive.backup_bind_mount_using_docker(
            host,
            str(source),
            backup_file,
        ) is False

    shell_command = seen["argv"][-1]
    assert "|| true" not in shell_command
    assert "tar -czf" in shell_command

def test_run_sudo_command_refuses_missing_password():
    host = SimpleNamespace(
        _get_sudo_password=lambda: None,
        logger=_Logger(),
    )

    with pytest.raises(RuntimeError, match="Sudo password required"):
        backup_archive.run_sudo_command(host, ["true"])


def test_run_sudo_command_places_stdin_option_before_command_and_keeps_password_out_of_argv(monkeypatch):
    seen = {}

    class FakeProcess:
        returncode = 0

        def __init__(self, argv, **_kwargs):
            seen["argv"] = argv

        def communicate(self, input=None, timeout=None):
            seen["input"] = input
            seen["timeout"] = timeout
            return b"", b""

        def kill(self):
            seen["killed"] = True

    monkeypatch.setattr(backup_archive.subprocess, "Popen", FakeProcess)

    host = SimpleNamespace(
        _get_sudo_password=lambda: "super-secret",
        logger=_Logger(),
    )

    result = backup_archive.run_sudo_command(
        host,
        ["chown", "1000:1000", "/tmp/archive.tar.gz"],
        timeout=7,
    )

    assert seen["argv"] == [
        "sudo",
        "-S",
        "--",
        "chown",
        "1000:1000",
        "/tmp/archive.tar.gz",
    ]
    assert seen["input"] == b"super-secret\n"
    assert seen["timeout"] == 7
    assert all("super-secret" not in str(arg) for arg in seen["argv"])
    assert result.args == seen["argv"]
    assert result.returncode == 0


def test_backup_archive_uses_shared_sudo_argv_builder_for_both_sudo_paths():
    source = Path(backup_archive.__file__).read_text(encoding="utf-8")

    assert source.count("_build_sudo_stdin_command(") >= 3
    assert "sudo_cmd + ['-S']" not in source
    assert "sudo_cmd = ['sudo', '-S'] + tar_cmd" not in source


def test_backup_restore_facade_delegates_archive_runtime(monkeypatch, tmp_path):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []
    backup_file = tmp_path / "backup.tar.gz"

    monkeypatch.setattr(
        "dockerpilot.backup_restore._backup_volume_using_docker_impl",
        lambda actual_host, volume, path, container: calls.append(
            ("volume", actual_host, volume, path, container)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._cleanup_backup_containers_impl",
        lambda actual_host: calls.append(("cleanup", actual_host)),
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._backup_bind_mount_using_docker_impl",
        lambda actual_host, source, path, container: calls.append(
            ("bind", actual_host, source, path, container)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._backup_directory_impl",
        lambda actual_host, source, path, container: calls.append(
            ("directory", actual_host, source, path, container)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._run_sudo_command_impl",
        lambda actual_host, args, timeout=10, check=False: calls.append(
            ("sudo", actual_host, args, timeout, check)
        ) or "done",
    )

    assert host._backup_volume_using_docker("vol", backup_file, "demo") is True
    assert host._cleanup_backup_containers() is None
    assert host._backup_bind_mount_using_docker("/srv/demo", backup_file, "demo") is True
    assert host._backup_directory("/srv/demo", backup_file, "demo") is True
    assert host._run_sudo_command(["true"], timeout=7, check=True) == "done"

    assert calls == [
        ("volume", host, "vol", backup_file, "demo"),
        ("cleanup", host),
        ("bind", host, "/srv/demo", backup_file, "demo"),
        ("directory", host, "/srv/demo", backup_file, "demo"),
        ("sudo", host, ["true"], 7, True),
    ]
