"""Unix-socket client for Secure Deploy broker (no Docker fallback)."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from dockerpilot.secure_deploy_broker.errors import BrokerError, ProtocolError
from dockerpilot.secure_deploy_broker.protocol import (
    PROTOCOL_VERSION,
    recv_message,
    send_message,
    validate_response,
)

from .errors import ScannerError, SecureDeployError
from .store import new_id

DEFAULT_TIMEOUT = 15.0
CLIENT_NAME = "dockerpilot-extras"
CLIENT_VERSION = os.environ.get("DOCKERPILOT_EXTRAS_VERSION", "0.9.0-pre.2")
ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "plan_id",
        "plan_sha256",
        "approval_id",
        "admission_bundle_sha256",
        "template_id",
        "canary_execution_id",
    }
)


class BrokerClient:
    def __init__(self, socket_path: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
        self.socket_path = socket_path or os.environ.get("SECURE_DEPLOY_BROKER_SOCKET", "")
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        if not self.socket_path:
            raise SecureDeployError("broker_unavailable", "broker socket not configured", 503)
        path = Path(self.socket_path)
        if path.is_symlink():
            raise SecureDeployError("broker_symlink", "broker socket path is a symlink", 502)
        if not path.exists():
            raise SecureDeployError("broker_unavailable", "broker socket missing", 503)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(path))
        except OSError as exc:
            sock.close()
            raise SecureDeployError("broker_unavailable", f"broker connect failed: {exc}", 503) from exc
        return sock

    def request(
        self,
        operation: str,
        *,
        plan: Optional[Dict[str, Any]] = None,
        approval: Optional[Dict[str, Any]] = None,
        reconnect: bool = False,
        **fields: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": new_id("breq"),
            "operation": operation,
            "client": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }
        if plan is not None:
            payload["plan"] = plan
        if approval is not None:
            payload["approval"] = approval
        unknown = set(fields) - ALLOWED_REQUEST_FIELDS
        if unknown:
            raise SecureDeployError(
                "broker_request_field",
                f"unsupported broker request field: {sorted(unknown)[0]}",
                400,
            )
        payload.update(fields)

        attempts = (
            2
            if reconnect
            and operation
            in {"ping", "capabilities", "verify_plan", "dry_run", "admit_canary_execution", "deploy_canary"}
            else 1
        )
        last_exc: Exception | None = None
        for _ in range(attempts):
            sock = None
            try:
                sock = self._connect()
                send_message(sock, payload)
                resp = recv_message(sock, timeout=self.timeout)
                validate_response(resp)
                if not resp.get("ok"):
                    err = resp.get("error") or {}
                    raise SecureDeployError(
                        str(err.get("code") or "broker_error"),
                        str(err.get("message") or "broker rejected request"),
                        502,
                    )
                return resp
            except (ProtocolError, BrokerError) as exc:
                last_exc = exc
            except OSError as exc:
                last_exc = exc
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        raise SecureDeployError(
            "broker_unavailable",
            f"broker request failed: {last_exc}",
            503,
        ) from last_exc

    def dry_run(self, plan: Dict[str, Any], approval: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("dry_run", plan=plan, approval=approval, reconnect=True)

    def verify_plan(self, plan: Dict[str, Any], approval: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request("verify_plan", plan=plan, approval=approval, reconnect=True)

    def admit_canary_execution(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        approval_id: str,
        admission_bundle_sha256: str,
    ) -> Dict[str, Any]:
        return self.request(
            "admit_canary_execution",
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            approval_id=approval_id,
            admission_bundle_sha256=admission_bundle_sha256,
            template_id="dockerpilot-secure-canary-v1",
            reconnect=True,
        )

    def deploy_canary(self, *, plan_id: str, plan_sha256: str, approval_id: str) -> Dict[str, Any]:
        return self.request(
            "deploy_canary",
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            approval_id=approval_id,
            reconnect=True,
        )

    def remove_canary(self, *, canary_execution_id: str) -> Dict[str, Any]:
        return self.request("remove_canary", canary_execution_id=canary_execution_id)
