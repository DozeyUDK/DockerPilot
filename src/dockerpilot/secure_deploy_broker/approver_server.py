"""Dedicated broker approval channel.

This listener is intentionally separate from the DockerPilot Extras control
socket. The request cannot choose its authoritative identity; the approver UID
comes exclusively from Linux SO_PEERCRED and is checked by
BrokerApprovalAuthority.
"""

from __future__ import annotations

import os
import re
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .approval_authority import BrokerApprovalAuthority
from .errors import BrokerError, ProtocolError, VerificationError
from .listen_fds import take_systemd_listen_fds
from .peer import get_peer_credentials
from .protocol import PROTOCOL_VERSION, recv_message, sanitize_error_operation, send_message

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_KEYS = frozenset(
    {
        "protocol_version",
        "request_id",
        "operation",
        "challenge_id",
        "plan_sha256",
        "client",
    }
)


class ApproverServer:
    """Root-owned approval listener with a single closed operation."""

    def __init__(
        self,
        *,
        authority: BrokerApprovalAuthority,
        socket_path: Optional[str] = None,
        request_timeout: float = 15.0,
        socket_activation: bool = True,
    ):
        self.authority = authority
        self.socket_path = socket_path
        self.request_timeout = float(request_timeout)
        self.socket_activation = bool(socket_activation)
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._owns_socket_path = False

    def start(self) -> None:
        activated = take_systemd_listen_fds()
        if activated:
            sock = activated[0]
            sock.settimeout(0.5)
            self._sock = sock
        else:
            if self.socket_activation:
                raise BrokerError("listen_fds_required", "approver socket activation required but LISTEN_FDS absent")
            if not self.socket_path:
                raise BrokerError("socket_path_required", "standalone approver mode requires socket path")
            path = Path(self.socket_path)
            if path.exists() or path.is_symlink():
                if path.is_symlink():
                    raise BrokerError("symlink_socket", "approver socket path is a symlink")
                path.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(path))
            os.chmod(path, 0o600)
            sock.listen(5)
            sock.settimeout(0.5)
            self._sock = sock
            self._owns_socket_path = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="secure-deploy-approver", daemon=True)
        self._thread.start()

    def start_from_existing_socket(self, sock: socket.socket) -> None:
        sock.settimeout(0.5)
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="secure-deploy-approver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._owns_socket_path and self.socket_path:
            path = Path(self.socket_path)
            if path.exists() and not path.is_symlink():
                try:
                    path.unlink()
                except OSError:
                    pass
        self._owns_socket_path = False

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle(self, conn: socket.socket) -> None:
        request_id = "unknown"
        operation = "unknown"
        try:
            cred = get_peer_credentials(conn)
            req = recv_message(conn, timeout=self.request_timeout)
            request_id = str(req.get("request_id") or "unknown")
            operation = sanitize_error_operation(req.get("operation"))
            self._validate_request(req)
            approval = self.authority.approve_challenge(
                str(req["challenge_id"]),
                approver_uid=cred.uid,
                expected_plan_sha256=str(req["plan_sha256"]),
            )
            resp = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "ok": True,
                "approval": {
                    "approval_id": approval["approval_id"],
                    "challenge_id": approval["challenge_id"],
                    "plan_id": approval["plan_id"],
                    "plan_sha256": approval["plan_sha256"],
                    "approved_at": approval["approved_at"],
                    "expires_at": approval["expires_at"],
                    "provenance": approval["provenance"],
                    "status": approval["status"],
                },
            }
        except (BrokerError, ProtocolError, VerificationError) as exc:
            resp = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except Exception:
            resp = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "ok": False,
                "error": {"code": "approver_internal_error", "message": "internal approver error"},
            }
        try:
            send_message(conn, resp)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _validate_request(self, req: Dict[str, Any]) -> None:
        unknown = set(req) - _ALLOWED_KEYS
        if unknown:
            raise ProtocolError("approver_request_field", f"unsupported approver field: {sorted(unknown)[0]}")
        if req.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("unknown_protocol_version", "unsupported protocol_version")
        if req.get("operation") != "approve_challenge":
            raise ProtocolError("approver_operation_not_supported", "only approve_challenge is supported")
        request_id = req.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
            raise ProtocolError("invalid_request_schema", "invalid request_id")
        challenge_id = req.get("challenge_id")
        if not isinstance(challenge_id, str) or not _ID_RE.fullmatch(challenge_id):
            raise ProtocolError("invalid_request_schema", "invalid challenge_id")
        plan_sha = req.get("plan_sha256")
        if not isinstance(plan_sha, str) or not _SHA_RE.fullmatch(plan_sha):
            raise ProtocolError("invalid_request_schema", "invalid plan_sha256")
        client = req.get("client")
        if client is not None:
            if not isinstance(client, dict) or set(client) - {"name", "version"}:
                raise ProtocolError("invalid_request_schema", "invalid approver client")
            if client.get("name") != "dockerpilot-secure-approve":
                raise ProtocolError("invalid_request_schema", "unexpected approver client")
