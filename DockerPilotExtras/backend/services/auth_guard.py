"""Authentication abuse controls and CSRF helpers for DockerPilot Extras."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network


class SlidingWindowRateLimiter:
    """Small in-memory failed-attempt limiter suitable for a single Extras process."""

    def __init__(self, *, max_failures: int = 5, window_seconds: int = 60, clock=None):
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = max(1, int(window_seconds))
        self._clock = clock or time.time
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def check(self, key: str) -> tuple[bool, int]:
        now = float(self._clock())
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.max_failures:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - events[0])))
            return False, retry_after

    def register_failure(self, key: str) -> tuple[bool, int]:
        now = float(self._clock())
        with self._lock:
            events = self._prune(key, now)
            events.append(now)
            if len(events) < self.max_failures:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - events[0])))
            return False, retry_after

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


def parse_trusted_proxy_networks(value: str | None) -> tuple:
    """Parse explicitly trusted proxy IPs/CIDRs.

    Forwarded client-address headers are security-sensitive. We only consult
    them when the socket peer belongs to one of these explicitly configured
    networks; otherwise ``request.remote_addr`` remains authoritative.
    """
    networks = []
    for item in str(value or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            network = ip_network(candidate, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid trusted proxy IP/CIDR: {candidate}") from exc
        if network.prefixlen == 0:
            raise ValueError("Refusing an all-addresses trusted proxy network")
        networks.append(network)
    return tuple(networks)


def _is_trusted_proxy(address, trusted_proxy_networks: tuple) -> bool:
    return any(address.version == network.version and address in network for network in trusted_proxy_networks)


def resolve_client_ip_for_rate_limit(
    *,
    remote_addr: str | None,
    forwarded_for: str | None,
    trusted_proxy_networks: tuple,
) -> str:
    """Resolve the rate-limit client IP without trusting arbitrary forwarding headers.

    If the direct peer is not explicitly trusted, X-Forwarded-For is ignored.
    For a trusted proxy chain, walk X-Forwarded-For from the nearest hop back
    toward the origin and return the first address that is not itself a trusted
    proxy. This resists a client-prepended spoofed address when the edge proxy
    appends the real client address to an existing header.
    """
    peer_text = str(remote_addr or "").strip()
    if not peer_text:
        return "unknown"
    try:
        peer_ip = ip_address(peer_text)
    except ValueError:
        return peer_text

    if not trusted_proxy_networks or not _is_trusted_proxy(peer_ip, trusted_proxy_networks):
        return str(peer_ip)

    forwarded_ips = []
    for item in str(forwarded_for or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            forwarded_ips.append(ip_address(candidate))
        except ValueError:
            continue

    if not forwarded_ips:
        return str(peer_ip)

    for candidate in reversed(forwarded_ips):
        if not _is_trusted_proxy(candidate, trusted_proxy_networks):
            return str(candidate)

    # Ambiguous chain containing only trusted proxy addresses: fail closed to
    # the direct peer instead of inventing a client identity from the header.
    return str(peer_ip)


def csrf_token_matches(*, header_token: str | None, session_token: str | None) -> bool:
    """Constant-time validation of a session-bound CSRF token."""
    header = (header_token or "").strip()
    stored = (session_token or "").strip()
    return bool(header) and bool(stored) and hmac.compare_digest(header, stored)
