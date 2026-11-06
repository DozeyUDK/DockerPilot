import os
from pathlib import Path

from dockerpilot.services.configuration_archive import export_configuration, import_configuration


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message):
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def error(self, message):
        self.messages.append(str(message))


def test_configuration_archive_round_trip(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "deployment.yml").write_text("name: demo\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    console = FakeConsole()
    logger = FakeLogger()

    monkeypatch.chdir(source)
    assert export_configuration(console, logger, str(archive))
    (source / "deployment.yml").unlink()
    assert import_configuration(console, logger, str(archive))
    assert (source / "deployment.yml").read_text(encoding="utf-8") == "name: demo\n"


def test_configuration_import_missing_archive_returns_false(tmp_path):
    console = FakeConsole()
    assert not import_configuration(console, FakeLogger(), str(tmp_path / "missing.tar.gz"))
    assert any("Archive not found" in message for message in console.messages)


def _config_archive(path, entries):
    """Build archive fixtures without extracting any untrusted member."""
    from io import BytesIO
    import tarfile

    with tarfile.open(path, "w:gz") as archive:
        for name, contents, kind, linkname in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                payload = contents.encode("utf-8")
                info.size = len(payload)
                archive.addfile(info, BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = linkname
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = linkname
                archive.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)


def test_config_import_rejects_traversal_without_writing_any_members(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    archive = tmp_path / "bad.tar.gz"
    _config_archive(archive, [
        ("deployment.yml", "safe", "file", ""),
        ("../outside.txt", "malicious", "file", ""),
    ])
    console, logger = FakeConsole(), FakeLogger()
    monkeypatch.chdir(work)
    assert import_configuration(console, logger, str(archive)) is False
    assert not (tmp_path / "outside.txt").exists()
    assert not (work / "deployment.yml").exists()  # Preflight rejects entire archive.
    assert logger.messages


def test_config_import_rejects_unknown_files_and_symlinks(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    for name, kind, target in [
        ("unexpected.txt", "file", ""),
        ("deployment.yml", "symlink", "../outside.txt"),
        ("deployment.yml", "hardlink", "alerts.yml"),
        ("deployment.yml", "fifo", ""),
        ("/tmp/absolute", "file", ""),
    ]:
        archive = tmp_path / f"bad-{kind}-{len(name)}.tar.gz"
        _config_archive(archive, [(name, "content", kind, target)])
        assert not import_configuration(FakeConsole(), FakeLogger(), str(archive))
    assert list(work.iterdir()) == []


def test_config_import_does_not_follow_preexisting_symlink(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("original", encoding="utf-8")
    (work / "deployment.yml").symlink_to(external)
    archive = tmp_path / "archive.tar.gz"
    _config_archive(archive, [("deployment.yml", "changed", "file", "")])
    monkeypatch.chdir(work)
    assert not import_configuration(FakeConsole(), FakeLogger(), str(archive))
    assert external.read_text(encoding="utf-8") == "original"
