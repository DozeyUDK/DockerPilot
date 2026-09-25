#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_HOME="$HOME"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOST_HOME}/.dockerpilot_demo}"
RUNTIME_ENV="$STATE_DIR/runtime.env"
DEMO_HOME="$STATE_DIR/home"
PID_FILE="$STATE_DIR/extras.pid"
LOG_FILE="$STATE_DIR/extras.log"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "[demo] .venv is missing; running bootstrap"
  bash "$ROOT/demo/bootstrap.sh"
fi

if [[ ! -f "$RUNTIME_ENV" ]]; then
  "$ROOT/.venv/bin/python" "$ROOT/demo/prepare_runtime.py" --state-dir "$STATE_DIR"
fi

set -a
# Generated values contain only shell-safe URL-safe tokens and simple scalars.
# shellcheck disable=SC1090
source "$RUNTIME_ENV"
set +a

# Codespaces keeps forwarded-port visibility across backend restarts. Never
# start a mutation-enabled backend while the port may still be public.
MUTATIONS_FLAG="${DOCKERPILOT_DEMO_ALLOW_MUTATIONS:-false}"
MUTATIONS_FLAG="${MUTATIONS_FLAG,,}"
if [[ "$MUTATIONS_FLAG" == "true" && "${CODESPACES:-}" == "true" ]]; then
  echo "[demo] interactive mode requested; forcing port 5000 private before backend startup"
  if ! CODESPACE_NAME="${CODESPACE_NAME:-}" bash "$ROOT/demo/private.sh"; then
    echo "[demo] refusing interactive startup because private port visibility could not be enforced" >&2
    exit 1
  fi
fi

mkdir -p "$DEMO_HOME"

for _ in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! docker info >/dev/null 2>&1; then
  echo "[demo] Docker daemon is not available" >&2
  exit 1
fi

echo "[demo] starting isolated sample containers"
docker compose -p dockerpilot-demo -f "$ROOT/demo/compose.yml" up -d --remove-orphans

"$ROOT/.venv/bin/python" "$ROOT/demo/seed_demo.py" --home "$DEMO_HOME"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  LIVE_MODE="$("$ROOT/.venv/bin/python" - "http://127.0.0.1:${PORT:-5000}/api/auth/status" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        payload = json.load(response)
    print("true" if not payload.get("demo_read_only", False) else "false")
except Exception:
    pass
PY
)"
  if [[ "$LIVE_MODE" != "$MUTATIONS_FLAG" ]]; then
    echo "[demo] configured access mode changed; restarting DockerPilotExtras"
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
      sleep 0.1
    done
    rm -f "$PID_FILE"
  else
    echo "[demo] DockerPilotExtras already running (pid $(cat "$PID_FILE"))"
  fi
fi

if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "[demo] starting DockerPilotExtras on port ${PORT:-5000}"
  (
    export HOME="$DEMO_HOME"
    cd "$ROOT/DockerPilotExtras"
    exec "$ROOT/.venv/bin/python" run_dev.py
  ) >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
fi

READY=false
for _ in $(seq 1 30); do
  if "$ROOT/.venv/bin/python" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT:-5000}/', timeout=1).read(1)" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done

if [[ "$READY" != "true" ]]; then
  echo "[demo] DockerPilotExtras did not become ready; last log lines:" >&2
  tail -n 50 "$LOG_FILE" >&2 || true
  exit 1
fi

DOCKERPILOT_DEMO_STATE_DIR="$STATE_DIR" bash "$ROOT/demo/status.sh"
