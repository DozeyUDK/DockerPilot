from pathlib import Path
import sys

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.elevation_tokens import ElevationTokenManager


class Session(dict):
    permanent = False


def test_elevation_token_is_one_time_and_session_bound_without_credentials():
    now = [100.0]
    manager = ElevationTokenManager(clock=lambda: now[0])
    owner = Session()
    other = Session()
    issued = manager.issue(
        owner,
        scope={"action": "promote", "container": "api"},
    )

    assert "sudo_password" not in issued
    assert all("sudo_password" not in entry for entry in manager._tokens.values())

    ok, message = manager.consume(
        other,
        issued["token"],
        expected_action="promote",
    )
    assert not ok
    assert "session" in message.lower()

    ok, message = manager.consume(
        owner,
        issued["token"],
        expected_action="promote",
        expected_scope={"container": "api"},
    )
    assert (ok, message) == (True, "ok")

    ok, _message = manager.consume(owner, issued["token"])
    assert not ok


def test_elevation_token_expires_and_revoke_clears_session_tokens():
    now = [100.0]
    manager = ElevationTokenManager(
        default_ttl_seconds=30,
        max_ttl_seconds=60,
        clock=lambda: now[0],
    )
    session = Session()
    first = manager.issue(session)
    second = manager.issue(session)
    assert manager.revoke_for_session(session) == 2
    assert manager.consume(session, first["token"])[0] is False
    assert manager.consume(session, second["token"])[0] is False

    third = manager.issue(session, ttl_seconds=30)
    now[0] = 131.0
    ok, message = manager.consume(session, third["token"])
    assert not ok
    assert "expired" in message.lower()


def test_manager_api_has_no_sudo_password_parameter():
    import inspect

    issue_signature = inspect.signature(ElevationTokenManager.issue)
    assert "sudo_password" not in issue_signature.parameters

    source = Path(
        sys.modules[ElevationTokenManager.__module__].__file__
    ).read_text(encoding="utf-8")
    assert '"sudo_password":' not in source
