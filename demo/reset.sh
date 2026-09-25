#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"

STATE_REAL="$(realpath -m "$STATE_DIR")"
HOME_REAL="$(realpath -m "$HOME")"
ROOT_REAL="$(realpath -m "$ROOT")"
if [[ "$STATE_REAL" == "/" || "$STATE_REAL" == "$HOME_REAL" || "$STATE_REAL" == "$ROOT_REAL" || "$(basename "$STATE_REAL")" != ".dockerpilot_demo" ]]; then
  echo "[demo] refusing unsafe demo state directory: $STATE_REAL" >&2
  exit 1
fi

if [[ -d "$STATE_REAL" ]]; then
  if [[ ! -f "$STATE_REAL/.dockerpilot-demo-owner" || ! -f "$STATE_REAL/.dockerpilot-demo-state" ]]; then
    echo "[demo] refusing to reset state directory without DockerPilot ownership proof: $STATE_REAL" >&2
    exit 1
  fi
  bash "$ROOT/demo/stop.sh"
  rm -rf -- "$STATE_REAL/home"
  rm -f -- "$STATE_REAL/runtime.env" "$STATE_REAL/extras.log" "$STATE_REAL/extras.pid" "$STATE_REAL/.dockerpilot-demo-state"
fi

"$ROOT/.venv/bin/python" "$ROOT/demo/prepare_runtime.py" --state-dir "$STATE_REAL" --rotate
DOCKERPILOT_DEMO_STATE_DIR="$STATE_REAL" bash "$ROOT/demo/start.sh"
