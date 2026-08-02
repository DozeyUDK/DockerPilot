#!/usr/bin/env python3
"""Stdlib-only Unix-socket client for Secure Deploy broker canaries.

Do not import the ``dockerpilot`` package — its ``__init__`` may load the Docker SDK.
"""
from __future__ import annotations

import json
import socket
import struct
import uuid
from typing import Any, Dict, Mapping, Optional

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 2 * 1024 * 1024
DEFAULT_SOCKET = "/run/dockerpilot-secure-broker/broker.sock"
DEFAULT_CLIENT_NAME = "dockerpilot-extras"
DEFAULT_CLIENT_VERSION = "0.9.0-pre.2"
DEFAULT_TIMEOUT = 90.0


def frame(obj: Mapping[str, Any], *, max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > max_bytes:
        raise ValueError(f"frame too large: {len(raw)} > {max_bytes}")
    return struct.pack("!I", len(raw)) + raw


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("socket closed")
        buf += chunk
    return buf


def call(
    operation: str,
    *,
    socket_path: str = DEFAULT_SOCKET,
    plan: Any = None,
    approval: Any = None,
    client_name: str = DEFAULT_CLIENT_NAME,
    client_version: str = DEFAULT_CLIENT_VERSION,
    protocol_version: int = PROTOCOL_VERSION,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_FRAME_BYTES,
) -> Dict[str, Any]:
    """Send one length-prefixed JSON request; return decoded response."""
    req: Dict[str, Any] = {
        "protocol_version": protocol_version,
        "request_id": "breq_" + uuid.uuid4().hex[:16],
        "operation": operation,
        "client": {"name": client_name, "version": client_version},
    }
    if plan is not None:
        req["plan"] = plan
    if approval is not None:
        req["approval"] = approval
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path)
    try:
        sock.sendall(frame(req, max_bytes=max_bytes))
        (n,) = struct.unpack("!I", read_exact(sock, 4))
        if n > max_bytes:
            raise RuntimeError(f"response too large: {n}")
        return json.loads(read_exact(sock, n).decode())
    finally:
        sock.close()


def err_code(resp: Mapping[str, Any]) -> Optional[str]:
    return (resp.get("error") or {}).get("code")
