"""systemd LISTEN_FDS socket activation helpers (stdlib only)."""

from __future__ import annotations

import os
import socket
from typing import List

from .errors import BrokerError

_SD_LISTEN_FDS_START = 3


def take_systemd_listen_fds() -> List[socket.socket]:
    """Return exactly one AF_UNIX stream listening socket from systemd, or [].

    Raises BrokerError when LISTEN_FDS is set but invalid (zero after unset,
    multiple FDs, non-unix, or non-listening).
    """
    listen_pid = os.environ.get("LISTEN_PID")
    listen_fds = os.environ.get("LISTEN_FDS")
    if listen_fds is None and listen_pid is None:
        return []
    if listen_pid is None or listen_fds is None:
        raise BrokerError("listen_fds_incomplete", "LISTEN_PID and LISTEN_FDS must both be set")
    try:
        pid = int(listen_pid)
        n_fds = int(listen_fds)
    except ValueError as exc:
        raise BrokerError("listen_fds_invalid", "LISTEN_PID/LISTEN_FDS must be integers") from exc
    if pid != os.getpid():
        raise BrokerError("listen_fds_pid", "LISTEN_PID does not match current process")
    if n_fds <= 0:
        raise BrokerError("listen_fds_count", "LISTEN_FDS must be > 0 when set")
    if n_fds != 1:
        raise BrokerError("listen_fds_count", "exactly one LISTEN_FDS socket required")

    # Clear env so children do not inherit.
    os.environ.pop("LISTEN_PID", None)
    os.environ.pop("LISTEN_FDS", None)
    os.environ.pop("LISTEN_FDNAMES", None)

    fd = _SD_LISTEN_FDS_START
    try:
        sock = socket.socket(fileno=fd)
    except OSError as exc:
        raise BrokerError("listen_fds_socket", f"failed to adopt fd {fd}: {exc}") from exc
    try:
        if sock.family != socket.AF_UNIX:
            raise BrokerError("listen_fds_family", "LISTEN_FDS socket must be AF_UNIX")
        # Listening sockets report a non-empty getsockname path or abstract name,
        # and accept() works; SO_ACCEPTCONN is Linux-specific.
        try:
            acceptconn = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        except OSError:
            acceptconn = 1  # best-effort on platforms without SO_ACCEPTCONN
        if not acceptconn:
            raise BrokerError("listen_fds_not_listening", "LISTEN_FDS socket is not listening")
    except BrokerError:
        try:
            sock.close()
        except OSError:
            pass
        raise
    return [sock]
