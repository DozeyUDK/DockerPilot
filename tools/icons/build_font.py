#!/usr/bin/env python3
"""Build a small, verified Nerd Fonts WOFF2 subset for the DockerPilot GUI.

Tooling only: pip install 'fonttools==4.60.1' 'brotli==1.1.0'
No runtime Python dependency is required to use the generated font.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = (
    "https://raw.githubusercontent.com/ryanoasis/nerd-fonts/v3.5.1/"
    "patched-fonts/NerdFontsSymbolsOnly/SymbolsNerdFontMono-Regular.ttf"
)
SOURCE_SHA256 = "fe471e538392f51910faab985fa8e192a39dd3426125edd15b71b3680df0e749"
OUTPUT = ROOT / "DockerPilotExtras/frontend/src/assets/SymbolsNerdFontMono-Regular.woff2"


def main() -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    catalog = json.loads((ROOT / "icons/catalog.json").read_text(encoding="utf-8"))
    codepoints = {int(record["codepoint"], 16) for record in catalog["icons"].values()}

    with urllib.request.urlopen(UPSTREAM, timeout=30) as response:
        original = response.read(8 * 1024 * 1024)
    digest = hashlib.sha256(original).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Upstream font checksum mismatch; refusing unverified source")

    with tempfile.TemporaryDirectory(prefix="dockerpilot-icons-") as directory:
        source_path = Path(directory) / "original.ttf"
        source_path.write_bytes(original)
        font = TTFont(source_path)
        missing = codepoints.difference(font.getBestCmap())
        if missing:
            raise ValueError(f"Upstream font is missing glyphs {missing!r}")

        options = subset.Options()
        options.desubroutinize = True
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=sorted(codepoints))
        subsetter.subset(font)

        font.flavor = "woff2"
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        font.save(OUTPUT)
        verification = TTFont(OUTPUT)
        if not codepoints.issubset(verification.getBestCmap()):
            OUTPUT.unlink()
            raise ValueError("Subsetting lost a required icon")

        print(f"Built {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
