"""Regression tests for container restore extraction."""

from io import StringIO
from pathlib import Path
import tarfile
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_restore
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


def test_restore_container_data_rejects_missing_backup_directory(tmp_path):
    host = SimpleNamespace(console=_console(), logger=_Logger())

    assert container_restore.restore_container_data(host, "demo", str(tmp_path / "missing")) is False


def test_restore_from_tar_round_trip(tmp_path):
    source_parent = tmp_path / "source"
    source = source_parent / "payload"
    source.mkdir(parents=True)
    (source / "hello.txt").write_text("hello", encoding="utf-8")
    archive = tmp_path / "payload.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="payload")

    destination_parent = tmp_path / "restore"
    destination = destination_parent / "payload"
    host = SimpleNamespace(logger=_Logger())

    assert container_restore.restore_from_tar(host, archive, str(destination)) is True
    assert (destination / "hello.txt").read_text(encoding="utf-8") == "hello"


def test_backup_restore_facade_delegates_restore(monkeypatch, tmp_path):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []
    archive = tmp_path / "backup.tar.gz"

    monkeypatch.setattr(
        "dockerpilot.backup_restore._restore_container_data_impl",
        lambda actual_host, container_name, backup_path: calls.append(
            ("restore", actual_host, container_name, backup_path)
        ) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._restore_from_tar_impl",
        lambda actual_host, tar_file, destination: calls.append(
            ("tar", actual_host, tar_file, destination)
        ) or True,
    )

    assert host.restore_container_data("demo", "/backup/demo") is True
    assert host._restore_from_tar(archive, "/srv/demo") is True
    assert calls == [
        ("restore", host, "demo", "/backup/demo"),
        ("tar", host, archive, "/srv/demo"),
    ]
