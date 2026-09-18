"""Safety tests for per-run DockerPilot migration credentials."""

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
from dockerpilot.execution_context import resolve_sudo_password, sudo_credential


class _Pilot:
    def __init__(self):
        self._sudo_password = None

    def _get_sudo_password(self):
        return resolve_sudo_password(self._sudo_password)


def test_execution_context_isolates_concurrent_credentials_without_mutating_shared_pilot():
    pilot = _Pilot()
    first_entered = threading.Event()
    second_entered = threading.Event()
    observations = []

    def execute(password, *, first=False):
        with DockerPilotExecutionContext(lambda: pilot, sudo_password=password) as capability:
            assert capability.pilot is pilot
            assert pilot._sudo_password is None
            assert password not in pilot.__dict__.values()
            if first:
                first_entered.set()
                assert second_entered.wait(timeout=2)
            else:
                assert first_entered.wait(timeout=2)
                second_entered.set()
            observations.append((password, capability.pilot._get_sudo_password()))
            assert pilot._sudo_password is None
            assert password not in pilot.__dict__.values()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, "first-secret", first=True)
        second = pool.submit(execute, "second-secret")
        first.result(timeout=2)
        second.result(timeout=2)

    assert sorted(observations) == [
        ("first-secret", "first-secret"),
        ("second-secret", "second-secret"),
    ]
    assert pilot._sudo_password is None


def test_execution_context_keeps_secrets_out_of_repr_and_migration_spec():
    context = DockerPilotExecutionContext(lambda: _Pilot(), sudo_password="do-not-expose")
    spec = MigrationSpec("api", "dev", "prod", True, False)

    assert "do-not-expose" not in repr(context)
    assert "sudo" not in repr(spec).lower()
    assert "password" not in spec.to_payload()


def test_execution_context_clears_credential_after_failure():
    pilot = _Pilot()

    try:
        with DockerPilotExecutionContext(lambda: pilot, sudo_password="temporary"):
            raise RuntimeError("migration failed")
    except RuntimeError:
        pass

    assert pilot._sudo_password is None
    assert pilot._get_sudo_password() is None


def test_execution_context_is_one_shot_and_forgets_credential_on_enter():
    pilot = _Pilot()
    context = DockerPilotExecutionContext(lambda: pilot, sudo_password="one-shot")

    with context:
        assert context._sudo_password is None
        assert pilot._get_sudo_password() == "one-shot"

    with pytest.raises(RuntimeError, match="already been used"):
        context.__enter__()


def test_execution_context_forgets_credential_when_pilot_provider_fails():
    def fail_provider():
        raise RuntimeError("pilot unavailable")

    context = DockerPilotExecutionContext(
        fail_provider,
        sudo_password="must-not-be-retained",
    )

    with pytest.raises(RuntimeError, match="pilot unavailable"):
        context.__enter__()

    assert context._sudo_password is None
    assert "must-not-be-retained" not in repr(context)
    with pytest.raises(RuntimeError, match="already been used"):
        context.__enter__()


def test_scoped_credential_overrides_and_can_mask_legacy_fallback():
    assert resolve_sudo_password("legacy") == "legacy"

    with sudo_credential("scoped"):
        assert resolve_sudo_password("legacy") == "scoped"

    with sudo_credential(None):
        assert resolve_sudo_password("legacy") is None

    assert resolve_sudo_password("legacy") == "legacy"
