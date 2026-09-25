#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"
PID_FILE="$STATE_DIR/extras.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "[demo] stopping DockerPilotExtras pid $PID"
    kill "$PID"
    for _ in $(seq 1 20); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.25
    done
  fi
  rm -f "$PID_FILE"
fi

if docker info >/dev/null 2>&1; then
  docker compose -p dockerpilot-demo -f "$ROOT/demo/compose.yml" down --remove-orphans
fi
