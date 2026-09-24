from pathlib import Path
import sys

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.elevation_tokens import ElevationTokenManager


class Session(dict):
    permanent = False


def test_elevation_token_is_one_time_and_session_bound():
    now = [100.0]
    manager = ElevationTokenManager(clock=lambda: now[0])
    owner = Session()
    other = Session()
    issued = manager.issue(
        owner,
        sudo_password="secret",
        scope={"action": "promote", "container": "api"},
    )

    ok, message, password = manager.consume(
        other,
        issued["token"],
        expected_action="promote",
    )
    assert not ok
    assert "session" in message.lower()
    assert password is None

    ok, message, password = manager.consume(
        owner,
        issued["token"],
        expected_action="promote",
        expected_scope={"container": "api"},
    )
    assert (ok, message, password) == (True, "ok", "secret")

    ok, _message, password = manager.consume(owner, issued["token"])
    assert not ok
    assert password is None


def test_elevation_token_expires_and_revoke_clears_session_tokens():
    now = [100.0]
    manager = ElevationTokenManager(default_ttl_seconds=30, max_ttl_seconds=60, clock=lambda: now[0])
    session = Session()
    first = manager.issue(session, sudo_password="one")
    second = manager.issue(session, sudo_password="two")
    assert manager.revoke_for_session(session) == 2
    assert manager.consume(session, first["token"])[0] is False
    assert manager.consume(session, second["token"])[0] is False

    third = manager.issue(session, sudo_password="three", ttl_seconds=30)
    now[0] = 131.0
    ok, message, password = manager.consume(session, third["token"])
    assert not ok
    assert "expired" in message.lower()
    assert password is None
