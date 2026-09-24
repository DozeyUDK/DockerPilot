"""Regression tests for container backup orchestration extraction."""

from datetime import datetime
from io import StringIO
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import container_backup
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


def test_container_backup_reuses_complete_recent_backup(tmp_path):
    backup_dir = tmp_path / "backup_demo_recent"
    backup_dir.mkdir()
    archive = backup_dir / "data.tar.gz"
    archive.write_bytes(b"archive")
    (backup_dir / "backup_metadata.json").write_text(
        json.dumps(
            {
                "container_name": "demo",
                "backup_time": datetime.now().isoformat(),
                "volumes": [{"backup_file": str(archive)}],
                "total_size": archive.stat().st_size,
            }
        ),
        encoding="utf-8",
    )

    container = SimpleNamespace(attrs={"Mounts": []})
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        find_existing_backup=lambda _name, _hours: backup_dir,
        console=_console(),
        logger=_Logger(),
    )

    assert container_backup.backup_container_data(host, "demo", reuse_existing=True) is True


def test_container_backup_treats_container_without_mounts_as_success():
    container = SimpleNamespace(attrs={"Mounts": []})
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        find_existing_backup=lambda _name, _hours: None,
        _check_sudo_required_for_backup=lambda _name: (
            False,
            [],
            {"large_mounts": [], "total_size_gb": 0, "total_size_tb": 0, "mounts": []},
        ),
        console=_console(),
        logger=_Logger(),
    )

    assert container_backup.backup_container_data(host, "demo", reuse_existing=False) is True


def test_container_backup_keeps_subprocess_available_for_bind_mount_sizing():
    """The bind-mount sizing path uses run() and catches TimeoutExpired."""
    assert container_backup.subprocess is subprocess
    assert container_backup.subprocess.TimeoutExpired is subprocess.TimeoutExpired


def test_backup_restore_facade_delegates_container_backup(monkeypatch):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []

    monkeypatch.setattr(
        "dockerpilot.backup_restore._backup_container_data_impl",
        lambda actual_host, container_name, **kwargs: calls.append(
            (actual_host, container_name, kwargs)
        ) or True,
    )

    assert host.backup_container_data(
        "demo",
        "backup-demo",
        reuse_existing=False,
        max_backup_age_hours=6,
    ) is True
    assert calls == [
        (
            host,
            "demo",
            {
                "backup_path": "backup-demo",
                "reuse_existing": False,
                "max_backup_age_hours": 6,
            },
        )
    ]
