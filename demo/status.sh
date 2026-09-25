#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"
RUNTIME_ENV="$STATE_DIR/runtime.env"
PID_FILE="$STATE_DIR/extras.pid"

if [[ ! -f "$RUNTIME_ENV" ]]; then
  echo "[demo] runtime credentials not found; run: bash demo/bootstrap.sh" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV"
set +a

URL="http://localhost:${PORT:-5000}"
if [[ "${CODESPACES:-}" == "true" && -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  URL="https://${CODESPACE_NAME}-${PORT:-5000}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi

BACKEND="stopped"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  BACKEND="running (pid $(cat "$PID_FILE"))"
fi

cat <<EOF

DockerPilot live demo
---------------------
Backend:  $BACKEND
URL:      $URL
Username: $WEB_AUTH_USERNAME
Password: $WEB_AUTH_PASSWORD

The web app and CLI use the current repository checkout.
Demo state is isolated in: $STATE_DIR
EOF

if [[ "${CODESPACES:-}" == "true" ]]; then
  cat <<EOF

Port 5000 is PRIVATE by default.
To share the demo temporarily:
  bash demo/public.sh

To make it private again:
  bash demo/private.sh
EOF
fi

if docker info >/dev/null 2>&1; then
  echo
  docker compose -p dockerpilot-demo -f "$ROOT/demo/compose.yml" ps
fi
