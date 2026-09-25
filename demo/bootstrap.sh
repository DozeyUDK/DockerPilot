#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DOCKERPILOT_DEMO_STATE_DIR:-${HOME}/.dockerpilot_demo}"
cd "$ROOT"

echo "[demo] preparing Python environment"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[tui]"
.venv/bin/pip install -r DockerPilotExtras/requirements.txt

echo "[demo] building DockerPilotExtras frontend from this checkout"
npm ci --prefix DockerPilotExtras/frontend
npm run build --prefix DockerPilotExtras/frontend

echo "[demo] preparing isolated runtime credentials"
.venv/bin/python demo/prepare_runtime.py --state-dir "$STATE_DIR"

echo "[demo] bootstrap complete"
