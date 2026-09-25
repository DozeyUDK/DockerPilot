#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"
MARKER="$STATE_DIR/.dockerpilot-demo-state"

bash "$ROOT/demo/stop.sh"

if [[ -d "$STATE_DIR" ]]; then
  if [[ ! -f "$MARKER" ]]; then
    echo "[demo] refusing to remove unmarked state directory: $STATE_DIR" >&2
    exit 1
  fi
  rm -rf "$STATE_DIR/home"
  rm -f "$STATE_DIR/runtime.env" "$STATE_DIR/extras.log" "$STATE_DIR/extras.pid" "$MARKER"
fi

"$ROOT/.venv/bin/python" "$ROOT/demo/prepare_runtime.py" --state-dir "$STATE_DIR" --rotate
bash "$ROOT/demo/start.sh"
