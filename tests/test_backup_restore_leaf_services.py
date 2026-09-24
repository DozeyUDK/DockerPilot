"""Focused regression tests for backup discovery and mount inspection extraction."""

from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from dockerpilot import backup_discovery, backup_mounts
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


def _write_backup(root: Path, name: str, container_name: str, backup_time: datetime, complete: bool = True):
    backup_dir = root / name
    backup_dir.mkdir()
    archive = backup_dir / "data.tar.gz"
    if complete:
        archive.write_bytes(b"archive")
    metadata = {
        "container_name": container_name,
        "backup_time": backup_time.isoformat(),
        "volumes": [{"backup_file": str(archive)}],
    }
    (backup_dir / "backup_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return backup_dir


def test_find_existing_backup_selects_newest_complete_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now().astimezone()
    older = _write_backup(tmp_path, "backup_demo_old", "demo", now - timedelta(hours=2))
    newest = _write_backup(tmp_path, "backup_demo_new", "demo", now - timedelta(minutes=10))
    _write_backup(tmp_path, "backup_demo_incomplete", "demo", now - timedelta(minutes=1), complete=False)

    host = SimpleNamespace(logger=_Logger())
    result = backup_discovery.find_existing_backup(host, "demo", max_age_hours=24)

    assert result.resolve() == newest.resolve()
    assert result.resolve() != older.resolve()


def test_mount_inspection_reports_readable_mount_without_sudo(tmp_path, monkeypatch):
    source = tmp_path / "data"
    source.mkdir()
    container = SimpleNamespace(attrs={"Mounts": [{"Source": str(source), "Destination": "/data"}]})
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        logger=_Logger(),
    )

    monkeypatch.setattr(backup_mounts.os, "access", lambda *_args: True)

    def fake_run(command, **_kwargs):
        if command[0] == "df":
            return SimpleNamespace(returncode=0, stdout="Filesystem Size Used Avail Use% Mounted on\n/dev/fake 10737418240 0 0 0% /data\n")
        if command[0] == "du":
            return SimpleNamespace(returncode=0, stdout="1048576\t/data\n")
        raise AssertionError(command)

    monkeypatch.setattr(backup_mounts.subprocess, "run", fake_run)

    requires_sudo, privileged_paths, info = backup_mounts.check_sudo_required_for_backup(host, "demo")

    assert requires_sudo is False
    assert privileged_paths == []
    assert len(info["mounts"]) == 1
    assert info["mounts"][0]["mount_point"] == "/data"
    assert info["mounts"][0]["requires_sudo"] is False
    assert info["total_size_gb"] > 0


def test_backup_restore_facade_delegates_phase1(monkeypatch):
    host = BackupRestoreMixin.__new__(BackupRestoreMixin)
    calls = []

    monkeypatch.setattr(
        "dockerpilot.backup_restore._check_sudo_required_for_backup_impl",
        lambda actual_host, container_name: calls.append(("mounts", actual_host, container_name))
        or (False, [], {"mounts": []}),
    )
    monkeypatch.setattr(
        "dockerpilot.backup_restore._find_existing_backup_impl",
        lambda actual_host, container_name, max_age_hours: calls.append(
            ("discovery", actual_host, container_name, max_age_hours)
        )
        or Path("backup_demo"),
    )

    assert host._check_sudo_required_for_backup("demo") == (False, [], {"mounts": []})
    assert host.find_existing_backup("demo", 12) == Path("backup_demo")
    assert calls == [
        ("mounts", host, "demo"),
        ("discovery", host, "demo", 12),
    ]
