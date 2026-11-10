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


def _tar_fixture(path, members):
    """Create an archive from (name, type, content/linkname) records."""
    from io import BytesIO

    with tarfile.open(path, "w:gz") as tar:
        for name, kind, payload in members:
            item = tarfile.TarInfo(name)
            if kind == "file":
                raw = payload.encode("utf-8")
                item.size = len(raw)
                tar.addfile(item, BytesIO(raw))
            else:
                if kind == "directory":
                    item.mode = 0o755
                item.type = {
                    "directory": tarfile.DIRTYPE,
                    "symlink": tarfile.SYMTYPE,
                    "hardlink": tarfile.LNKTYPE,
                    "fifo": tarfile.FIFOTYPE,
                }[kind]
                if kind in ("symlink", "hardlink"):
                    item.linkname = payload
                tar.addfile(item)


def test_restore_docker_volume_dot_layout(tmp_path):
    archive = tmp_path / "vol.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./postgres/data.sql", "file", "ok"),
    ])
    destination = tmp_path / "volumes" / "postgres-data"
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "postgres" / "data.sql").read_text() == "ok"
    assert not (destination.parent / "postgres" / "data.sql").exists()


def test_restore_named_root_preserves_destination_layout(tmp_path):
    archive = tmp_path / "bind.tar.gz"
    _tar_fixture(archive, [
        ("db", "directory", ""),
        ("db/config.ini", "file", "ok"),
    ])
    destination = tmp_path / "restore" / "db"
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "config.ini").read_text() == "ok"
    assert not (destination / "db").exists()


def test_restore_safe_internal_links(tmp_path):
    archive = tmp_path / "links.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./data.txt", "file", "ok"),
        ("./copy.txt", "hardlink", "./data.txt"),
        ("./alias.txt", "symlink", "data.txt"),
    ])
    destination = tmp_path / "restore"
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "alias.txt").is_symlink()
    assert (destination / "alias.txt").read_text() == "ok"
    assert (destination / "copy.txt").read_text() == "ok"


def test_restore_rejects_malicious_entries_before_writing(tmp_path):
    bad_entries = [
        ("../outside.txt", "file", "escape"),
        ("/tmp/absolute-backup-probe", "file", "escape"),
        ("./escape", "symlink", "../outside.txt"),
        ("./escape", "hardlink", "../outside.txt"),
        ("./devices", "fifo", ""),
        ("./safe", "symlink", "nested"),
        ("./data.txt", "hardlink", "./data.txt"),
    ]
    for name, kind, payload in bad_entries:
        archive = tmp_path / "bad.tar.gz"
        _tar_fixture(archive, [
            (".", "directory", ""),
            ("./first.txt", "file", "should-not-be-extracted"),
            (name, kind, payload),
            *([("./safe/child.txt", "file", "bad")] if name == "./safe" else []),
        ])
        destination = tmp_path / "destination"
        logger = _Logger()
        assert not container_restore.restore_from_tar(
            SimpleNamespace(logger=logger), archive, str(destination)
        ), (name, kind)
        assert not (destination / "first.txt").exists(), (name, kind)
        assert not (tmp_path / "outside.txt").exists()
        assert logger.messages


def test_restore_rejects_existing_symlink_to_outside(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (destination / "db").symlink_to(external, target_is_directory=True)
    archive = tmp_path / "bad.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./db/secrets.txt", "file", "untrusted"),
    ])
    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (external / "secrets.txt").exists()


def _restore_fake_host(tmp_path, tar_success, volume_type="named_volume"):
    import json
    from contextlib import nullcontext

    backup = tmp_path / "backups"
    backup.mkdir()
    (backup / "vol.tar.gz").write_bytes(b"fake archive")
    destination = tmp_path / "mount"
    destination.mkdir()
    (destination / "original.txt").write_text("keep", encoding="utf-8")
    volume_info = {
        "backup_file": str(backup / "vol.tar.gz"),
        "type": volume_type,
        "name": "test_volume",
        "source": str(destination),
    }
    (backup / "backup_metadata.json").write_text(
        json.dumps({"volumes": [volume_info]}), encoding="utf-8"
    )
    host = SimpleNamespace(
        console=_console(),
        logger=_Logger(),
        client=SimpleNamespace(
            containers=SimpleNamespace(
                get=lambda _: SimpleNamespace(attrs={"Mounts": [
                    {"Type": "volume", "Name": "test_volume", "Source": ""},
                    {"Type": "bind", "Name": "", "Source": str(destination)},
                ]})
            ),
            volumes=SimpleNamespace(
                get=lambda _: SimpleNamespace(attrs={"Mountpoint": str(destination)})
            ),
        ),
        _with_loading=lambda _: nullcontext(),
        _restore_from_tar=lambda *_: tar_success,
    )
    return host, backup, destination


def test_named_volume_false_extract_result_is_failure(tmp_path):
    host, backup, _ = _restore_fake_host(tmp_path, tar_success=False)
    assert container_restore.restore_container_data(host, "demo", str(backup)) is False
    assert any("Archive extraction failed" in log[1] for log in host.logger.messages)


def test_bind_mount_failure_rolls_back_previous_data(tmp_path):
    host, backup, destination = _restore_fake_host(
        tmp_path, tar_success=False, volume_type="bind_mount"
    )
    assert not container_restore.restore_container_data(host, "demo", str(backup))
    assert (destination / "original.txt").read_text() == "keep"
    assert not list(destination.parent.glob("mount.backup_*"))


def test_missing_archive_is_failure_not_false_success(tmp_path):
    host, backup, _ = _restore_fake_host(tmp_path, tar_success=True)
    (backup / "vol.tar.gz").unlink()
    assert not container_restore.restore_container_data(host, "demo", str(backup))


def test_restore_rejects_backup_metadata_target_not_mounted(tmp_path):
    import json

    host, backup, destination = _restore_fake_host(
        tmp_path, tar_success=True, volume_type="bind_mount"
    )
    metadata_path = backup / "backup_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["volumes"][0]["source"] = str(tmp_path / "different-source")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert not container_restore.restore_container_data(host, "demo", str(backup))
    assert (destination / "original.txt").read_text() == "keep"


def test_restore_rejects_archive_outside_backup_directory(tmp_path):
    import json

    host, backup, _ = _restore_fake_host(tmp_path, tar_success=True)
    (backup / "vol.tar.gz").unlink()
    external = tmp_path / "external-backup.tar.gz"
    external.write_bytes(b"fake archive")
    metadata_path = backup / "backup_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["volumes"][0]["backup_file"] = str(external)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert not container_restore.restore_container_data(host, "demo", str(backup))


def test_restore_regular_file_replaces_hardlink_without_changing_outside(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original", encoding="utf-8")
    (destination / "same.txt").hardlink_to(outside)
    archive = tmp_path / "valid.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./same.txt", "file", "restored"),
    ])
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "same.txt").read_text() == "restored"
    assert outside.read_text() == "original"


def test_failed_bind_restore_discards_partial_output_before_rollback(tmp_path):
    host, backup, destination = _restore_fake_host(
        tmp_path, tar_success=False, volume_type="bind_mount"
    )

    def failing_extract(_archive, path):
        (Path(path) / "partial.txt").write_text("incomplete", encoding="utf-8")
        return False

    host._restore_from_tar = failing_extract
    assert not container_restore.restore_container_data(host, "demo", str(backup))
    assert (destination / "original.txt").read_text() == "keep"
    assert not (destination / "partial.txt").exists()


def test_named_root_restores_original_private_mode_and_contents(tmp_path):
    import stat

    archive = tmp_path / "private-bind.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        from io import BytesIO

        root = tarfile.TarInfo("private")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        root.uid, root.gid = 2001, 2002
        tar.addfile(root)
        data = b"confidential"
        file = tarfile.TarInfo("private/secret.txt")
        file.mode = 0o600
        file.size = len(data)
        tar.addfile(file, BytesIO(data))

    destination = tmp_path / "restore" / "private"
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "secret.txt").read_bytes() == b"confidential"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_named_root_applies_archived_uid_gid_in_privileged_restore(tmp_path, monkeypatch):
    from dockerpilot.services import safe_tar

    archive = tmp_path / "owner.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("private")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        root.uid, root.gid = 3141, 5926
        tar.addfile(root)

    calls = []
    # Simulate the chown branch without requiring a privileged test runner.
    monkeypatch.setattr(safe_tar.os, "geteuid", lambda: 0)
    monkeypatch.setattr(safe_tar.os, "chown", lambda *args, **kwargs: calls.append(args))
    destination = tmp_path / "restore" / "private"
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination.resolve(), 3141, 5926) in calls


def test_volume_dot_root_restores_directory_mode(tmp_path):
    import stat

    archive = tmp_path / "volume.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        tar.addfile(root)

    destination = tmp_path / "volume"
    destination.mkdir(mode=0o755)
    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_restore_hardlinks_is_idempotent_and_keeps_external_hardlink_unchanged(tmp_path):
    import os

    archive = tmp_path / "volume.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./main.txt", "file", "restored-content"),
        ("./alias.txt", "hardlink", "./main.txt"),
    ])
    destination = tmp_path / "volume"
    host = SimpleNamespace(logger=_Logger())
    assert container_restore.restore_from_tar(host, archive, str(destination))
    assert os.stat(destination / "alias.txt").st_ino == os.stat(destination / "main.txt").st_ino

    outside = tmp_path / "outside.txt"
    outside.write_text("external-content", encoding="utf-8")
    (destination / "alias.txt").unlink()
    (destination / "alias.txt").hardlink_to(outside)

    # Replaying the backup must replace the destination inode atomically,
    # without editing another existing name of the old hardlink.
    assert container_restore.restore_from_tar(host, archive, str(destination))
    assert (destination / "main.txt").read_text() == "restored-content"
    assert (destination / "alias.txt").read_text() == "restored-content"
    assert outside.read_text() == "external-content"
    assert os.stat(destination / "alias.txt").st_ino == os.stat(destination / "main.txt").st_ino
    assert not list(destination.glob(".dockerpilot-hardlink-*"))

    assert container_restore.restore_from_tar(host, archive, str(destination))
    assert os.stat(destination / "alias.txt").st_ino == os.stat(destination / "main.txt").st_ino


def test_restore_does_not_overwrite_symlink_hardlink_destination(tmp_path):
    archive = tmp_path / "volume.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./main.txt", "file", "archive-content"),
        ("./alias.txt", "hardlink", "./main.txt"),
    ])
    destination = tmp_path / "volume"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("external-content", encoding="utf-8")
    (destination / "alias.txt").symlink_to(outside)
    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "alias.txt").is_symlink()
    assert outside.read_text() == "external-content"
    assert not (destination / "main.txt").exists()


def test_restore_tar_add_internal_symlink_chain(tmp_path):
    """tar.add() produces valid links to earlier archive-created symlinks."""
    source = tmp_path / "source" / "data"
    source.mkdir(parents=True)
    (source / "a.txt").write_text("original", encoding="utf-8")
    (source / "b-link").symlink_to("a.txt")
    (source / "c-link").symlink_to("b-link")

    archive = tmp_path / "chain.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="data")

    destination = tmp_path / "restore" / "data"
    host = SimpleNamespace(logger=_Logger())
    assert container_restore.restore_from_tar(host, archive, str(destination))
    assert (destination / "b-link").is_symlink()
    assert (destination / "c-link").is_symlink()
    assert (destination / "c-link").read_text(encoding="utf-8") == "original"


def test_restore_chain_through_preexisting_safe_symlink(tmp_path):
    """A previously existing in-root target link is allowed for symlinks."""
    destination = tmp_path / "volume"
    destination.mkdir()
    (destination / "base-link").symlink_to("data.txt")
    archive = tmp_path / "chain.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./data.txt", "file", "safe"),
        ("./new-link", "symlink", "base-link"),
    ])

    assert container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert (destination / "new-link").is_symlink()
    assert (destination / "new-link").read_text(encoding="utf-8") == "safe"


def test_restore_rejects_symlink_chain_to_existing_outside_target(tmp_path):
    """Following an existing symlink out of the restore root remains blocked."""
    destination = tmp_path / "volume"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original", encoding="utf-8")
    (destination / "escape-link").symlink_to(outside)
    archive = tmp_path / "escape.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./safe.txt", "file", "should not be written"),
        ("./new-link", "symlink", "escape-link"),
    ])

    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (destination / "safe.txt").exists()
    assert outside.read_text(encoding="utf-8") == "original"
    assert not (destination / "new-link").exists()


def test_restore_rejects_target_traversal_hidden_by_symlink_parent(tmp_path):
    """Lexical normalization alone must not hide a path via an outside symlink."""
    destination = tmp_path / "volume"
    destination.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (destination / "alias").symlink_to(outside_dir, target_is_directory=True)
    archive = tmp_path / "escape.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./safe.txt", "file", "should not be written"),
        ("./nested-link", "symlink", "alias/../outside.txt"),
    ])

    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (destination / "safe.txt").exists()
    assert not (destination / "nested-link").exists()


def test_restore_rejects_write_through_existing_internal_symlink(tmp_path):
    """A valid symlink target does not make writes through symlinks safe."""
    destination = tmp_path / "volume"
    destination.mkdir()
    (destination / "data").mkdir()
    (destination / "alias").symlink_to("data", target_is_directory=True)
    archive = tmp_path / "write.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./alias/new.txt", "file", "must not write through alias"),
    ])

    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (destination / "data" / "new.txt").exists()


def test_archive_symlink_graph_rejects_future_escape(tmp_path):
    """A later archive link must not turn an earlier target into an escape."""
    archive = tmp_path / "delayed-escape.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./safe.txt", "file", "must not write before preflight"),
        ("./x", "symlink", "alias/../outside.txt"),
        ("./alias", "symlink", "."),
    ])
    destination = tmp_path / "data"
    outside = tmp_path / "outside.txt"
    outside.write_text("external", encoding="utf-8")
    logger = _Logger()

    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=logger), archive, str(destination)
    )
    assert not (destination / "safe.txt").exists()
    assert not (destination / "x").is_symlink()
    assert not (destination / "alias").is_symlink()
    assert outside.read_text(encoding="utf-8") == "external"
    assert logger.messages


def test_archive_symlink_graph_rejects_future_escape_in_reverse_order(tmp_path):
    """Archive symlink creation order cannot determine containment safety."""
    archive = tmp_path / "delayed-escape-reverse.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./alias", "symlink", "."),
        ("./x", "symlink", "alias/../outside.txt"),
    ])
    destination = tmp_path / "data"
    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (destination / "x").is_symlink()


def test_archive_symlink_graph_rejects_indirect_cycles(tmp_path):
    """Cyclic internal symlinks fail preflight instead of consuming time."""
    archive = tmp_path / "cycle.tar.gz"
    _tar_fixture(archive, [
        (".", "directory", ""),
        ("./alpha", "symlink", "beta"),
        ("./beta", "symlink", "alpha"),
    ])
    destination = tmp_path / "data"
    assert not container_restore.restore_from_tar(
        SimpleNamespace(logger=_Logger()), archive, str(destination)
    )
    assert not (destination / "alpha").is_symlink()
    assert not (destination / "beta").is_symlink()
