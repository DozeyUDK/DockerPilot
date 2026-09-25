#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[demo] rebuilding from the current checkout"
bash "$ROOT/demo/stop.sh"
bash "$ROOT/demo/bootstrap.sh"
bash "$ROOT/demo/start.sh"
