"""Regression tests for deployment-state backup/restore extraction."""

from io import StringIO
import json
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import deployment_state_backup
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


def test_backup_deployment_state_writes_empty_snapshot(tmp_path):
    client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda **_kwargs: []),
        images=SimpleNamespace(list=lambda: []),
        networks=SimpleNamespace(list=lambda: []),
        volumes=SimpleNamespace(list=lambda: []),
        version=lambda: {"Version": "test-docker"},
    )
    host = SimpleNamespace(client=client, console=_console(), logger=_Logger())
    backup_dir = tmp_path / "state"

    assert deployment_state_backup.backup_deployment_state(host, str(backup_dir)) is True
    summary = json.loads((backup_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["containers_count"] == 0
    assert summary["images_count"] == 0
    assert summary["networks_count"] == 0
    assert summary["volumes_count"] == 0
    assert summary["docker_version"] == "test-docker"


def test_restore_deployment_state_rejects_missing_directory(tmp_path):
    host = SimpleNamespace(console=_console(), logger=_Logger())

    assert deployment_state_backup.restore_deployment_state(host, str(tmp_path / "missing")) is False


def test_backup_restore_facade_delegates_deployment_state(monkeypatch):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []

    monkeypatch.setattr(
        "dockerpilot.backup_restore._backup_deployment_state_impl",
        lambda actual_host, backup_path=None: calls.append(("backup", actual_host, backup_path)) or True,
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._restore_deployment_state_impl",
        lambda actual_host, backup_path: calls.append(("restore", actual_host, backup_path)) or True,
    )

    assert host.backup_deployment_state("state-dir") is True
    assert host.restore_deployment_state("state-dir") is True
    assert calls == [
        ("backup", host, "state-dir"),
        ("restore", host, "state-dir"),
    ]
