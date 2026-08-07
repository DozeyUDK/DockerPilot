from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))

import broker_install_helpers as bih


def test_backup_manifest_marker_symlink_is_rejected_without_leaking_target(tmp_path):
    manifest_hash = "a" * 64
    backup = tmp_path / "backup"
    backup.mkdir()
    private_file = tmp_path / "private.txt"
    private_file.write_text("SENTINEL_PRIVATE_CONTENT\n", encoding="utf-8")
    marker = backup / bih.BACKUP_MANIFEST_MARKER
    marker.symlink_to(private_file)

    with pytest.raises(ValueError) as excinfo:
        bih.assert_backup_dir_compatible(backup, manifest_hash)

    message = str(excinfo.value)
    assert "SENTINEL_PRIVATE_CONTENT" not in message
    assert private_file.read_text(encoding="utf-8") == "SENTINEL_PRIVATE_CONTENT\n"


def test_backup_manifest_marker_oversize_is_rejected_without_content_echo(tmp_path):
    manifest_hash = "a" * 64
    backup = tmp_path / "backup"
    backup.mkdir()
    marker = backup / bih.BACKUP_MANIFEST_MARKER
    marker.write_text("SENTINEL_" + ("x" * 256), encoding="ascii")

    with pytest.raises(ValueError, match="oversized") as excinfo:
        bih.assert_backup_dir_compatible(backup, manifest_hash)

    assert "SENTINEL_" not in str(excinfo.value)


def test_backup_manifest_marker_existing_same_hash_is_not_rewritten(tmp_path):
    manifest_hash = "a" * 64
    backup = tmp_path / "backup"
    backup.mkdir()
    marker = backup / bih.BACKUP_MANIFEST_MARKER
    marker.write_text(manifest_hash + "\n", encoding="ascii")
    before = marker.stat().st_mtime_ns

    bih.write_backup_manifest_marker(backup, manifest_hash)

    assert marker.read_text(encoding="ascii").strip() == manifest_hash
    assert marker.stat().st_mtime_ns == before
