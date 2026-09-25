#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"
RUNTIME_ENV="$STATE_DIR/runtime.env"

if [[ "${CODESPACES:-}" != "true" || -z "${CODESPACE_NAME:-}" ]]; then
  echo "[demo] public.sh is only for GitHub Codespaces" >&2
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
if [[ ! -f "$RUNTIME_ENV" ]]; then
  echo "[demo] runtime credentials not found; start the demo first" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV"
set +a
if [[ "${DOCKERPILOT_DEMO_ALLOW_MUTATIONS:-false}" == "true" ]]; then
  echo "[demo] refusing to expose an interactive demo publicly; set DOCKERPILOT_DEMO_ALLOW_MUTATIONS=false and restart first" >&2
  exit 1
fi

gh codespace ports visibility 5000:public -c "$CODESPACE_NAME"
echo "[demo] read-only port 5000 is now public until Codespaces resets its visibility"
bash "$ROOT/demo/status.sh"
