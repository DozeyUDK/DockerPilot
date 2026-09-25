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

# runtime.env is configuration for the next process start; it is not proof of
# what the already-running Flask process imported. Query the live backend before
# changing Codespaces visibility so stale interactive processes cannot be shared.
AUTH_STATUS_URL="http://127.0.0.1:${PORT:-5000}/api/auth/status"
if ! LIVE_STATUS="$("$ROOT/.venv/bin/python" - "$AUTH_STATUS_URL" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"[demo] cannot verify running backend: {exc}", file=sys.stderr)
    raise SystemExit(1)

if payload.get("demo_mode") is not True or payload.get("demo_read_only") is not True:
    print(
        "[demo] refusing public exposure: running backend is not verified read-only "
        f"(demo_mode={payload.get('demo_mode')!r}, demo_read_only={payload.get('demo_read_only')!r})",
        file=sys.stderr,
    )
    raise SystemExit(1)

print("verified")
PY
)"; then
  echo "[demo] port remains private; restart the backend in read-only mode and retry" >&2
  exit 1
fi

if [[ "$LIVE_STATUS" != "verified" ]]; then
  echo "[demo] refusing public exposure: unexpected backend verification result" >&2
  exit 1
fi

gh codespace ports visibility 5000:public -c "$CODESPACE_NAME"
echo "[demo] verified running backend is read-only; port 5000 is now public until Codespaces resets its visibility"
bash "$ROOT/demo/status.sh"
