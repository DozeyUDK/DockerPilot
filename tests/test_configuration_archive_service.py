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
