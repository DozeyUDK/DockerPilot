"""Regression tests for low-level backup archive/runtime extraction."""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from dockerpilot import backup_archive
from dockerpilot.backup_restore import BackupRestoreMixin


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


def test_run_sudo_command_refuses_missing_password():
    host = SimpleNamespace(
        _get_sudo_password=lambda: None,
        logger=_Logger(),
    )

    with pytest.raises(RuntimeError, match="Sudo password required"):
        backup_archive.run_sudo_command(host, ["true"])


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
