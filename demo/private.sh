#!/usr/bin/env bash
set -euo pipefail

if [[ "${CODESPACES:-}" != "true" || -z "${CODESPACE_NAME:-}" ]]; then
  echo "[demo] private.sh is only for GitHub Codespaces" >&2
  exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "[demo] GitHub CLI is unavailable" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "[demo] GitHub CLI is not authenticated. Run 'gh auth login' or change visibility in the Codespaces Ports panel." >&2
  exit 1
fi

DEMO_PORT="${PORT:-5000}"
gh codespace ports visibility "${DEMO_PORT}:private" -c "$CODESPACE_NAME"
echo "[demo] port ${DEMO_PORT} is private"
