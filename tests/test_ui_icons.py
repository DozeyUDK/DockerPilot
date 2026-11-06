"""Regression checks for unified Nerd Font icon mapping and terminal fallback."""
from __future__ import annotations

import json
from pathlib import Path

from dockerpilot.ui_icons import FALLBACKS, GLYPHS, icon

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_matches_generated_python_map():
    catalog = json.loads((ROOT / "icons/catalog.json").read_text(encoding="utf-8"))
    assert GLYPHS == {
        name: chr(int(entry["codepoint"], 16))
        for name, entry in catalog["icons"].items()
    }
    assert FALLBACKS == {
        name: entry["fallback"] for name, entry in catalog["icons"].items()
    }
    assert all(value.isascii() for value in FALLBACKS.values())


def test_ascii_is_safe_default(monkeypatch):
    monkeypatch.delenv("DOCKERPILOT_ICON_MODE", raising=False)
    assert icon("success") == "OK"
    assert icon("error") == "ERROR"


def test_nerd_mode_uses_catalog_glyphs(monkeypatch):
    monkeypatch.setenv("DOCKERPILOT_ICON_MODE", "nerd")
    assert icon("success") == GLYPHS["success"]
    assert icon("docker") == GLYPHS["docker"]
    monkeypatch.setenv("DOCKERPILOT_ICON_MODE", "ascii")
    assert icon("success") == "OK"


def test_icon_catalog_codegen_is_current():
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/icons/generate.py"), "--check"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_no_legacy_emoji_in_gui_or_python_presentation():
    catalog = json.loads((ROOT / "icons/catalog.json").read_text(encoding="utf-8"))
    symbols = tuple(catalog["legacy_visual_markers"])
    import re
    broad_emoji = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u20E3]")
    directories = (
        (ROOT / "DockerPilotExtras", (".py", ".sh")),
        (ROOT / "scripts", (".sh",)),
        (ROOT / "tools/searcher/templates", (".html",)),
        (ROOT / "DockerPilotExtras/frontend/src", (".jsx", ".js", ".mjs")),
        (ROOT / "src/dockerpilot", (".py",)),
        (ROOT / "DockerPilotExtras/backend", (".py",)),
    )
    exceptions = {"ui_icons.py", "icons.mjs"}
    for directory, suffixes in directories:
        for file in directory.rglob("*"):
            if (not file.is_file() or file.suffix not in suffixes or file.name in exceptions
                or any(part in {".venv", "venv", "node_modules", "__pycache__"} for part in file.parts)):
                continue
            text = file.read_text(encoding="utf-8")
            leftovers = [symbol for symbol in symbols if symbol in text]
            assert not leftovers, f"{file.relative_to(ROOT)}: {leftovers}"
            assert not broad_emoji.search(text), f"Unmapped emoji: {file.relative_to(ROOT)}"
    for file in (ROOT / "install.sh",):
        assert not broad_emoji.search(file.read_text(encoding="utf-8")), f"Unmapped emoji: {file.relative_to(ROOT)}"


def test_extras_setup_installs_shared_package_into_its_venv():
    """Extras scripts import dockerpilot.ui_icons, so their venv needs DockerPilot."""
    script = (ROOT / "DockerPilotExtras/setup_extras.sh").read_text(encoding="utf-8")
    assert '"$VENV_DIR/bin/pip" install -q -r requirements.txt' in script
    assert '"$VENV_DIR/bin/pip" install -q -e "$SCRIPT_DIR/.."' in script
