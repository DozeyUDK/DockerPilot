"""Linux SO_PEERCRED helpers for broker canary."""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass

from .errors import BrokerError

# Linux: SOL_SOCKET / SO_PEERCRED
_SOL_SOCKET = getattr(socket, "SOL_SOCKET", 1)
_SO_PEERCRED = 17


@dataclass(frozen=True)
class PeerCred:
    pid: int
    uid: int
    gid: int


def get_peer_credentials(conn: socket.socket) -> PeerCred:
    try:
        raw = conn.getsockopt(_SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
    except OSError as exc:
        raise BrokerError("peercred_unavailable", f"SO_PEERCRED failed: {exc}") from exc
    pid, uid, gid = struct.unpack("3i", raw)
    return PeerCred(pid=pid, uid=uid, gid=gid)


def assert_expected_uid(cred: PeerCred, expected_uid: int | None = None) -> None:
    want = os.getuid() if expected_uid is None else int(expected_uid)
    if cred.uid != want:
        raise BrokerError("peer_uid_mismatch", "peer UID rejected")
