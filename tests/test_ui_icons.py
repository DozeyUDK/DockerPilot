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


def test_no_emoji_or_hardcoded_frontend_glyphs_in_repository():
    """All tracked text, including docs and tests, must stay emoji-free."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/icons/check_no_emoji.py")],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_icon_catalog_uses_semantic_keys_only():
    catalog = json.loads((ROOT / "icons/catalog.json").read_text(encoding="utf-8"))
    assert "legacy_visual_markers" not in catalog
    assert all(name.isascii() for name in catalog["icons"])



def test_extras_setup_installs_shared_package_into_its_venv():
    """Extras scripts import dockerpilot.ui_icons, so their venv needs DockerPilot."""
    script = (ROOT / "DockerPilotExtras/setup_extras.sh").read_text(encoding="utf-8")
    assert '"$VENV_DIR/bin/pip" install -q -r requirements.txt' in script
    assert '"$VENV_DIR/bin/pip" install -q -e "$SCRIPT_DIR/.."' in script
