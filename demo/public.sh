#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${CODESPACES:-}" != "true" || -z "${CODESPACE_NAME:-}" ]]; then
  echo "[demo] public.sh is only for GitHub Codespaces" >&2
  exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "[demo] GitHub CLI is unavailable" >&2
  exit 1
fi

gh codespace ports visibility 5000:public -c "$CODESPACE_NAME"
echo "[demo] port 5000 is now public until Codespaces resets its visibility"
bash "$ROOT/demo/status.sh"
