"""Safety tests for per-run DockerPilot migration capabilities."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading

import pytest


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.migration_execution import DockerPilotExecutionContext
from backend.services.migration_runner import MigrationSpec
from dockerpilot.execution_context import (
    privileged_backup_authorization,
    resolve_privileged_backup_authorization,
    resolve_sudo_password,
    sudo_credential,
)


class _Pilot:
    def __init__(self):
        self._sudo_password = None

    def _get_sudo_password(self):
        return resolve_sudo_password(self._sudo_password)

    def _is_privileged_backup_authorized(self):
        return resolve_privileged_backup_authorization(True)


def test_execution_context_isolates_concurrent_backup_authorization():
    pilot = _Pilot()
    first_entered = threading.Event()
    second_entered = threading.Event()
    observations = []

    def execute(authorized, *, first=False):
        with DockerPilotExecutionContext(
            lambda: pilot,
            privileged_backup_authorized=authorized,
        ) as capability:
            assert capability.pilot is pilot
            assert pilot._sudo_password is None
            if first:
                first_entered.set()
                assert second_entered.wait(timeout=2)
            else:
                assert first_entered.wait(timeout=2)
                second_entered.set()
            observations.append((authorized, capability.pilot._is_privileged_backup_authorized()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, True, first=True)
        second = pool.submit(execute, False)
        first.result(timeout=2)
        second.result(timeout=2)

    assert sorted(observations) == [(False, False), (True, True)]
    assert pilot._sudo_password is None


def test_execution_context_contains_no_os_credential_and_spec_has_no_secret():
    context = DockerPilotExecutionContext(
        lambda: _Pilot(),
        privileged_backup_authorized=True,
    )
    spec = MigrationSpec("api", "dev", "prod", True, False)

    assert "password" not in repr(context).lower()
    assert "sudo" not in repr(context).lower()
    assert "sudo" not in repr(spec).lower()
    assert "password" not in spec.to_payload()


def test_execution_context_clears_authorization_after_failure():
    pilot = _Pilot()

    try:
        with DockerPilotExecutionContext(
            lambda: pilot,
            privileged_backup_authorized=True,
        ):
            assert pilot._is_privileged_backup_authorized() is True
            raise RuntimeError("migration failed")
    except RuntimeError:
        pass

    assert pilot._is_privileged_backup_authorized() is True


def test_execution_context_is_one_shot_and_forgets_authorization_on_enter():
    pilot = _Pilot()
    context = DockerPilotExecutionContext(
        lambda: pilot,
        privileged_backup_authorized=True,
    )

    with context:
        assert context._privileged_backup_authorized is False
        assert pilot._is_privileged_backup_authorized() is True

    with pytest.raises(RuntimeError, match="already been used"):
        context.__enter__()


def test_execution_context_forgets_authorization_when_pilot_provider_fails():
    def fail_provider():
        raise RuntimeError("pilot unavailable")

    context = DockerPilotExecutionContext(
        fail_provider,
        privileged_backup_authorized=True,
    )

    with pytest.raises(RuntimeError, match="pilot unavailable"):
        context.__enter__()

    assert context._privileged_backup_authorized is False
    with pytest.raises(RuntimeError, match="already been used"):
        context.__enter__()


def test_privileged_backup_scope_overrides_trusted_cli_fallback():
    assert resolve_privileged_backup_authorization(True) is True

    with privileged_backup_authorization(False):
        assert resolve_privileged_backup_authorization(True) is False

    with privileged_backup_authorization(True):
        assert resolve_privileged_backup_authorization(False) is True

    assert resolve_privileged_backup_authorization(True) is True


def test_sudo_credential_remains_cli_only_and_independent():
    assert resolve_sudo_password("legacy") == "legacy"

    with sudo_credential("scoped"):
        assert resolve_sudo_password("legacy") == "scoped"

    with privileged_backup_authorization(True):
        assert resolve_sudo_password(None) is None
