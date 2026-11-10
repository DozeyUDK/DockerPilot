#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src:$ROOT/DockerPilotExtras${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="$(printenv DOCKERPILOT_DEMO_PYTHON || true)"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="$(command -v python3 || true)"
  fi
fi

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "[secure-deploy-demo] Python 3 was not found." >&2
  exit 2
fi

if ! "$PYTHON" -c "import pytest, pytest_socket, dockerpilot" >/dev/null 2>&1; then
  if [[ "$PYTHON" == "$ROOT/.venv/bin/python" ]]; then
    echo "[secure-deploy-demo] installing optional test dependencies into .venv"
    "$PYTHON" -m pip install -e ".[test]"
  else
    echo "[secure-deploy-demo] pytest and pytest-socket are required." >&2
    echo "[secure-deploy-demo] create .venv and run: .venv/bin/pip install -e '.[test]'" >&2
    exit 2
  fi
fi

if ! "$PYTHON" -c "import flask, flask_restful" >/dev/null 2>&1; then
  if [[ "$PYTHON" == "$ROOT/.venv/bin/python" ]]; then
    echo "[secure-deploy-demo] installing DockerPilotExtras backend test dependencies into .venv"
    "$PYTHON" -m pip install -r DockerPilotExtras/requirements.txt
  else
    echo "[secure-deploy-demo] Flask and Flask-RESTful are required by the broker integration regression." >&2
    echo "[secure-deploy-demo] install DockerPilotExtras/requirements.txt into the selected virtual environment." >&2
    exit 2
  fi
fi

run_scenario() {
  local title="$1"
  local test="$2"
  printf "\n[secure-deploy-demo] %s\n" "$title"
  "$PYTHON" -m pytest -vv -s --tb=short "$test"
}

echo "[secure-deploy-demo] sandboxed regression demo"
echo "[secure-deploy-demo] temporary state + fake DozeyGuard; no Docker apply or privileged host changes"

run_scenario \
  "1/3 Broker revalidation rejects forged DozeyGuard results and revoked approvals" \
  "tests/test_secure_deploy_broker_dry_run.py::test_broker_canary_dry_run"

run_scenario \
  "2/3 Approver channel trusts peer credentials, not request-supplied identity" \
  "tests/test_secure_deploy_approver_channel.py::test_approver_channel_uses_peercred_not_request_identity"

run_scenario \
  "3/3 Plan expiry is bound to the canonical plan hash" \
  "tests/test_secure_deploy_plan_expiry_binding.py::test_extending_plan_expiry_without_new_hash_is_rejected_at_plan_identity"

echo
echo "[secure-deploy-demo] all scenarios passed"
