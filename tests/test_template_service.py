from pathlib import Path

from dockerpilot.services.templates import create_production_checklist, generate_documentation


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message):
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


def test_create_production_checklist_copies_template(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "production-checklist.md.template").write_text("# checklist\n", encoding="utf-8")
    output = tmp_path / "checklist.md"

    assert create_production_checklist(FakeConsole(), FakeLogger(), str(output), templates_dir=templates)
    assert output.read_text(encoding="utf-8") == "# checklist\n"


def test_generate_documentation_preserves_existing_output_semantics(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "docs-readme.md.template").write_text("README\n", encoding="utf-8")
    (templates / "docs-api.md.template").write_text("API\n", encoding="utf-8")
    # Troubleshooting is intentionally missing: the original behavior warns and continues.
    output_dir = tmp_path / "docs"
    logger = FakeLogger()

    assert generate_documentation(FakeConsole(), logger, str(output_dir), templates_dir=templates)
    assert (output_dir / "README.md").read_text(encoding="utf-8") == "README\n"
    assert (output_dir / "API.md").read_text(encoding="utf-8") == "API\n"
    assert not (output_dir / "TROUBLESHOOTING.md").exists()
    assert any(level == "warning" for level, _ in logger.messages)
