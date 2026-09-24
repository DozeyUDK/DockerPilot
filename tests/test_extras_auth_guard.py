from pathlib import Path
import sys

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.auth_guard import SlidingWindowRateLimiter, csrf_token_matches


def test_rate_limiter_blocks_after_configured_failures_and_can_clear():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(max_failures=3, window_seconds=60, clock=lambda: now[0])

    assert limiter.check("ip:user") == (True, 0)
    assert limiter.register_failure("ip:user")[0]
    assert limiter.register_failure("ip:user")[0]
    allowed, retry = limiter.register_failure("ip:user")
    assert not allowed
    assert retry > 0
    assert limiter.check("ip:user")[0] is False

    limiter.clear("ip:user")
    assert limiter.check("ip:user") == (True, 0)


def test_rate_limiter_window_expires():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(max_failures=1, window_seconds=10, clock=lambda: now[0])
    assert limiter.register_failure("key")[0] is False
    now[0] = 111.0
    assert limiter.check("key") == (True, 0)


def test_csrf_token_match_is_fail_closed():
    assert csrf_token_matches(header_token="abc", session_token="abc")
    assert not csrf_token_matches(header_token="", session_token="abc")
    assert not csrf_token_matches(header_token="abc", session_token="")
    assert not csrf_token_matches(header_token="abc", session_token="def")
