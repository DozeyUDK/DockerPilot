from pathlib import Path
import sys

import pytest

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.auth_guard import (
    SlidingWindowRateLimiter,
    csrf_token_matches,
    parse_trusted_proxy_networks,
    resolve_client_ip_for_rate_limit,
)


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


def test_untrusted_peer_cannot_spoof_forwarded_for():
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")
    resolved = resolve_client_ip_for_rate_limit(
        remote_addr="203.0.113.25",
        forwarded_for="198.51.100.77",
        trusted_proxy_networks=trusted,
    )
    assert resolved == "203.0.113.25"


def test_trusted_proxy_uses_forwarded_client_ip():
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")
    resolved = resolve_client_ip_for_rate_limit(
        remote_addr="10.0.0.10",
        forwarded_for="198.51.100.77",
        trusted_proxy_networks=trusted,
    )
    assert resolved == "198.51.100.77"


def test_trusted_proxy_chain_ignores_client_prepended_spoof():
    trusted = parse_trusted_proxy_networks("10.0.0.0/8,192.0.2.10/32")
    resolved = resolve_client_ip_for_rate_limit(
        remote_addr="10.0.0.10",
        forwarded_for="1.2.3.4, 198.51.100.77, 192.0.2.10",
        trusted_proxy_networks=trusted,
    )
    assert resolved == "198.51.100.77"


def test_all_trusted_forwarded_chain_fails_closed_to_direct_peer():
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")
    resolved = resolve_client_ip_for_rate_limit(
        remote_addr="10.0.0.10",
        forwarded_for="10.0.0.20, 10.0.0.30",
        trusted_proxy_networks=trusted,
    )
    assert resolved == "10.0.0.10"


def test_invalid_trusted_proxy_configuration_is_rejected():
    with pytest.raises(ValueError):
        parse_trusted_proxy_networks("10.0.0.0/8,not-an-ip")


def test_all_addresses_proxy_network_is_rejected():
    with pytest.raises(ValueError):
        parse_trusted_proxy_networks("0.0.0.0/0")
    with pytest.raises(ValueError):
        parse_trusted_proxy_networks("::/0")


def test_csrf_token_match_is_fail_closed():
    assert csrf_token_matches(header_token="abc", session_token="abc")
    assert not csrf_token_matches(header_token="", session_token="abc")
    assert not csrf_token_matches(header_token="abc", session_token="")
    assert not csrf_token_matches(header_token="abc", session_token="def")
