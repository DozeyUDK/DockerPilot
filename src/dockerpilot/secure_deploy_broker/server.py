"""Unprivileged Secure Deploy broker canary (Unix socket only)."""

from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .approval import assert_approval_binds_plan
from .errors import BrokerError, ProtocolError, VerificationError
from .peer import assert_expected_uid, get_peer_credentials
from .protocol import (
    PROTOCOL_VERSION,
    SUPPORTED_OPERATIONS,
    recv_message,
    send_message,
    validate_request,
)
from .verifier import BrokerDozeyguardConfig, resolve_broker_dozeyguard_config, verify_plan_independent


@dataclass
class BrokerRuntimeConfig:
    socket_path: str
    dozeyguard: BrokerDozeyguardConfig
    expected_peer_uid: Optional[int] = None
    request_timeout: float = 15.0


class BrokerServer:
    """Single-threaded accept loop for canary tests."""

    def __init__(
        self,
        config: BrokerRuntimeConfig,
        *,
        run_dozeyguard: Callable,
        normalize_spec_to_compose: Callable,
        plan_firewall_actions: Callable,
    ):
        self.config = config
        self._run_dozeyguard = run_dozeyguard
        self._normalize = normalize_spec_to_compose
        self._firewall = plan_firewall_actions
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        path = Path(self.config.socket_path)
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise BrokerError("symlink_socket", "socket path is a symlink")
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        os.chmod(str(path), 0o600)
        sock.listen(5)
        sock.settimeout(0.5)
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="secure-deploy-broker", daemon=True)
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
        path = Path(self.config.socket_path)
        if path.exists() and not path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass

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
        operation = "ping"
        try:
            cred = get_peer_credentials(conn)
            assert_expected_uid(cred, self.config.expected_peer_uid)
            req = recv_message(conn, timeout=self.config.request_timeout)
            request_id = str(req.get("request_id") or "unknown")
            operation = str(req.get("operation") or "ping")
            validate_request(req)
            resp = self._dispatch(req)
        except (BrokerError, ProtocolError, VerificationError) as exc:
            resp = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation if operation in SUPPORTED_OPERATIONS else "ping",
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        send_message(conn, resp)

    def _dispatch(self, req: Dict[str, Any]) -> Dict[str, Any]:
        op = req["operation"]
        request_id = req["request_id"]
        if op == "ping":
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
            }
        if op == "capabilities":
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
                "capabilities": {
                    "operations": sorted(SUPPORTED_OPERATIONS),
                    "apply_supported": False,
                    "protocol_version": PROTOCOL_VERSION,
                },
            }
        if op in {"verify_plan", "dry_run"}:
            plan = req.get("plan")
            approval = req.get("approval")
            if not isinstance(plan, dict):
                raise VerificationError("plan_required", "plan object required")
            require_approval = op == "dry_run"
            verification = verify_plan_independent(
                plan,
                approval if isinstance(approval, dict) else None,
                dozeyguard_config=self.config.dozeyguard,
                require_approval=require_approval,
                run_dozeyguard=self._adapt_dozeyguard,
                normalize_spec_to_compose=self._normalize,
                plan_firewall_actions=self._firewall,
            )
            verification["dry_run"] = op == "dry_run"
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
                "verification": verification,
            }
        raise ProtocolError("broker_operation_not_supported", f"operation {op} not supported")

    def _adapt_dozeyguard(self, compose: Dict[str, Any], config: BrokerDozeyguardConfig):
        # Lazy import keeps dockerpilot.secure_deploy_broker free of Flask extras.
        from dockerpilot.secure_deploy_broker.dg_runner import run_broker_dozeyguard

        return run_broker_dozeyguard(compose, config)


def build_canary_config(
    socket_path: str,
    *,
    executable: str,
    policy_path: str,
    expected_peer_uid: Optional[int] = None,
) -> BrokerRuntimeConfig:
    dg = resolve_broker_dozeyguard_config(executable=executable, policy_path=policy_path)
    return BrokerRuntimeConfig(
        socket_path=socket_path,
        dozeyguard=dg,
        expected_peer_uid=expected_peer_uid,
    )
