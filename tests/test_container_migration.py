"""Regression tests for container migration extraction."""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_migration
from dockerpilot.backup_restore import BackupRestoreMixin
from dockerpilot.models import DeploymentConfig


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


def _config():
    return DeploymentConfig(
        image_tag="demo:v1",
        container_name="demo",
        port_mapping={},
        environment={},
        volumes={},
    )


def test_migration_without_source_is_successful():
    host = SimpleNamespace(logger=_Logger(), console=_console())

    assert container_migration.migrate_container_data(host, None, object(), _config()) is True


def test_shared_named_volume_does_not_copy_data():
    mount = {"Name": "shared-data", "Destination": "/data", "Source": None}
    source = SimpleNamespace(name="old", attrs={"Mounts": [mount]})
    target = SimpleNamespace(name="new", attrs={"Mounts": [mount]})
    copy_calls = []
    host = SimpleNamespace(
        logger=_Logger(),
        console=_console(),
        _copy_volume_data=lambda *args, **kwargs: copy_calls.append((args, kwargs)) or True,
        _copy_bind_mount_data=lambda *args, **kwargs: True,
        _copy_container_files=lambda *args, **kwargs: True,
        _get_database_config=lambda _image: None,
    )

    assert container_migration.migrate_container_data(host, source, target, _config()) is True
    assert copy_calls == []


def test_backup_restore_facade_delegates_migration(monkeypatch):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []
    source = object()
    target = object()
    config = _config()

    monkeypatch.setattr(
        "dockerpilot.backup_restore._migrate_container_data_impl",
        lambda actual_host, src, dst, cfg: calls.append(("migrate", actual_host, src, dst, cfg)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._copy_volume_data_impl",
        lambda actual_host, src, dst, container: calls.append(("volume", actual_host, src, dst, container)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._copy_bind_mount_data_impl",
        lambda actual_host, src, dst, container: calls.append(("bind", actual_host, src, dst, container)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._copy_container_files_impl",
        lambda actual_host, src, dst, path, container: calls.append(
            ("files", actual_host, src, dst, path, container)
        ) or True,
    )

    assert host._migrate_container_data(source, target, config) is True
    assert host._copy_volume_data("src-vol", "dst-vol", "demo") is True
    assert host._copy_bind_mount_data("/src", "/dst", "demo") is True
    assert host._copy_container_files(source, target, "/etc/demo", "demo") is True

    assert calls == [
        ("migrate", host, source, target, config),
        ("volume", host, "src-vol", "dst-vol", "demo"),
        ("bind", host, "/src", "/dst", "demo"),
        ("files", host, source, target, "/etc/demo", "demo"),
    ]
