#!/usr/bin/env python3
"""Fail if tracked repository text contains emoji or direct UI icon glyphs.

Only the generated JS icon map may contain Nerd Fonts Private Use Area glyphs.
The other code should reference the shared semantic icon registry instead.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Escape notation avoids embedding the symbols this checker prohibits.
EMOJI = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u20E3"
    r"\u2300-\u23FF\u2B00-\u2BFF]"
)
PRIVATE_USE = re.compile(r"[\uE000-\uF8FF\U000F0000-\U000FFFFD]")
ICON_MAP = "DockerPilotExtras/frontend/src/icons.mjs"
UI_DIR = "DockerPilotExtras/frontend/src/"
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz", ".woff",
    ".woff2", ".ttf", ".otf", ".pdf", ".db", ".sqlite", ".zip", ".gz",
    ".tar", ".wasm", ".exe", ".dll", ".so", ".pyc", ".lockb",
}


def tracked_paths() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # Installed/source distributions can be missing the Git index.
        return [
            path.relative_to(ROOT)
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(segment in {
                ".git", ".venv", "venv", "node_modules", "build",
                "dist", "__pycache__", ".pytest_cache",
            } for segment in path.relative_to(ROOT).parts)
        ]
    return [Path(part.decode("utf-8")) for part in result.stdout.split(b"\0") if part]


def issues() -> list[str]:
    found = []
    for relative in tracked_paths():
        if relative.suffix.lower() in BINARY_SUFFIXES:
            continue
        path = ROOT / relative
        try:
            contents = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue  # Binary or inaccessible file, not UTF-8 source.
        for line_number, line in enumerate(contents.splitlines(), 1):
            if EMOJI.search(line):
                found.append(f"{relative}:{line_number}: emoji in tracked source")
            if (
                relative.as_posix().startswith(UI_DIR)
                and relative.as_posix() != ICON_MAP
                and PRIVATE_USE.search(line)
            ):
                found.append(f"{relative}:{line_number}: hardcoded UI glyph")
    return found


def main() -> int:
    found = issues()
    if found:
        print("\n".join(found))
        print(f"FAIL: {len(found)} unsupported emoji/icon occurrences.")
        return 1
    print("PASS: no emoji in tracked text and no hardcoded frontend icon glyphs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
