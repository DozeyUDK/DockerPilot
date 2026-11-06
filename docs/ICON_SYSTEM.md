# Unified GUI / TUI / CLI icon system

DockerPilot uses [Nerd Fonts Symbols Only Mono v3.5.1](../icons/THIRD_PARTY_NOTICES.md) for React, Rich and Textual.

## Architecture

- One source of truth: icons/catalog.json maps semantic names to Nerd Fonts codepoints, portable fallbacks, and upstream glyphs.
- Run tools/icons/generate.py to generate Python src/dockerpilot/ui_icons.py and JS DockerPilotExtras/frontend/src/icons.mjs.
- GUI loads a locally bundled 7.6 KB WOFF2 subset through src/index.css, restricted by unicode-range to Private Use Area codepoints.
- CLI and Textual TUI use identical codepoints via the icon(name) Python helper.
- No CDN is required at runtime.

## Terminal compatibility

A Nerd Font must be installed on the client side, not on the server. By default, CLI outputs portable ASCII labels, never emoji or Private Use Area characters.

To opt in, use the environment setting DOCKERPILOT_ICON_MODE=nerd.
To force plain messages, use DOCKERPILOT_ICON_MODE=ascii.

This setting only changes presentation, not command behavior.

## Updating icons

Edit icons/catalog.json and run:
  python3 tools/icons/generate.py
  python3 tools/icons/generate.py --check
  python3 -m pytest -q tests/test_ui_icons.py
  cd DockerPilotExtras/frontend && npm test && npm run build

Commit the catalog and generated files together. Do not add emoji to user-facing strings. In Python use icon('success'); in React import ICONS from src/icons.mjs.

See icons/THIRD_PARTY_NOTICES.md for license attribution.

To regenerate the font subset from a checksum-pinned upstream release: install fonttools==4.60.1 and brotli==1.1.0 in a temporary development environment, then run python3 tools/icons/build_font.py. Do not install these tooling dependencies in production.
