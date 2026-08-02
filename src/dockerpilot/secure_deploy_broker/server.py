"""Unprivileged / systemd-activated Secure Deploy broker (no apply)."""

from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .canary import CanaryManager, CanaryPolicy, WORKDIR
from .errors import BrokerError, ProtocolError, VerificationError
from .integrity import assert_trusted_artifact
from .listen_fds import take_systemd_listen_fds
from .peer import assert_expected_uid, get_peer_credentials
from .protocol import (
    PROTOCOL_VERSION,
    SUPPORTED_OPERATIONS,
    recv_message,
    sanitize_error_operation,
    send_message,
    validate_request,
)
from .verifier import BrokerDozeyguardConfig, resolve_broker_dozeyguard_config, verify_plan_independent


@dataclass
class BrokerRuntimeConfig:
    socket_path: Optional[str]
    dozeyguard: BrokerDozeyguardConfig
    expected_peer_uid: Optional[int] = None
    request_timeout: float = 15.0
    expected_binary_sha256: Optional[str] = None
    expected_policy_sha256: Optional[str] = None
    expected_artifact_uid: Optional[int] = None
    expected_artifact_gid: Optional[int] = None
    deny_writable_uid: Optional[int] = None
    socket_activation: bool = False
    allowed_operations: Optional[frozenset] = None
    canary_workdir: Optional[str] = None
    canary_image: Optional[str] = None
    canary_health_timeout_seconds: int = 60
    canary_live_mode: bool = True


class BrokerServer:
    """Accept loop for standalone Unix socket or systemd LISTEN_FDS."""

    def __init__(
        self,
        config: BrokerRuntimeConfig,
        *,
        run_dozeyguard: Callable,
        normalize_spec_to_compose: Callable,
        plan_firewall_actions: Callable,
        canary_manager: Optional[CanaryManager] = None,
    ):
        self.config = config
        self._run_dozeyguard = run_dozeyguard
        self._normalize = normalize_spec_to_compose
        self._firewall = plan_firewall_actions
        self._canary_manager = canary_manager
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._owns_socket_path = False
        self._socket_path_to_cleanup: Optional[str] = None
        self._allowed = frozenset(config.allowed_operations or SUPPORTED_OPERATIONS)

    def start(self) -> None:
        activated = take_systemd_listen_fds()
        if activated:
            sock = activated[0]
            sock.settimeout(0.5)
            self._sock = sock
            self._owns_socket_path = False
        else:
            # Installed canary config sets socket_activation=true: never bind a
            # second socket; require exactly one systemd LISTEN_FDS (validated above).
            if self.config.socket_activation:
                raise BrokerError(
                    "listen_fds_required",
                    "socket activation required but LISTEN_FDS absent",
                )
            if not self.config.socket_path:
                raise BrokerError("socket_path_required", "standalone mode requires socket_path")
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
            self._owns_socket_path = True
            self._socket_path_to_cleanup = str(path)
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="secure-deploy-broker", daemon=True)
        self._thread.start()

    def start_from_existing_socket(
        self,
        sock: socket.socket,
        *,
        owns_path: bool = False,
        socket_path: Optional[str] = None,
    ) -> None:
        """Test/helper entry for socket activation without real systemd."""
        sock.settimeout(0.5)
        self._sock = sock
        self._owns_socket_path = owns_path
        self._socket_path_to_cleanup = socket_path
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
        if self._owns_socket_path and self._socket_path_to_cleanup:
            path = Path(self._socket_path_to_cleanup)
            if path.exists() and not path.is_symlink():
                try:
                    path.unlink()
                except OSError:
                    pass
            self._socket_path_to_cleanup = None

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
            assert_expected_uid(cred, self.config.expected_peer_uid)
            req = recv_message(conn, timeout=self.config.request_timeout)
            request_id = str(req.get("request_id") or "unknown")
            # Capture caller operation for error envelopes before validation.
            # Do not default unknown/missing ops to "ping".
            operation = sanitize_error_operation(req.get("operation"))
            validate_request(req)
            op = req["operation"]
            if op not in self._allowed:
                raise ProtocolError("broker_operation_not_supported", f"operation {op} not allowed")
            resp = self._dispatch(req)
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
                "error": {"code": "broker_internal_error", "message": "internal broker error"},
            }
        try:
            send_message(conn, resp)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Client disconnects must only fail the current connection. The
            # broker thread must stay alive to accept later requests.
            return

    def _assert_artifacts(self) -> None:
        if self.config.expected_binary_sha256:
            assert_trusted_artifact(
                Path(self.config.dozeyguard.executable),
                expected_sha256=self.config.expected_binary_sha256,
                expected_uid=self.config.expected_artifact_uid,
                expected_gid=self.config.expected_artifact_gid,
                require_executable=True,
                deny_writable_uid=self.config.deny_writable_uid,
            )
        if self.config.expected_policy_sha256:
            assert_trusted_artifact(
                Path(self.config.dozeyguard.policy_path),
                expected_sha256=self.config.expected_policy_sha256,
                expected_uid=self.config.expected_artifact_uid,
                expected_gid=self.config.expected_artifact_gid,
                require_executable=False,
                deny_writable_uid=self.config.deny_writable_uid,
            )

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
                    "operations": sorted(self._allowed),
                    "apply_supported": False,
                    "canary_supported": True,
                    "protocol_version": PROTOCOL_VERSION,
                },
            }
        if op in {"verify_plan", "dry_run"}:
            self._assert_artifacts()
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
        if op == "admit_canary_execution":
            self._assert_artifacts()
            admission = self._canary().admit(
                plan_id=str(req.get("plan_id")),
                plan_sha256=str(req.get("plan_sha256")),
                approval_id=str(req.get("approval_id")),
                admission_bundle_sha256=str(req.get("admission_bundle_sha256")),
                template_id=str(req.get("template_id")),
                dozeyguard_config=self.config.dozeyguard,
                run_dozeyguard=self._adapt_dozeyguard,
                normalize_spec_to_compose=self._normalize,
                plan_firewall_actions=self._firewall,
            )
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
                "canary_admission": admission,
            }
        if op == "deploy_canary":
            self._assert_artifacts()
            result = self._canary().deploy(
                plan_id=str(req.get("plan_id")),
                plan_sha256=str(req.get("plan_sha256")),
                approval_id=str(req.get("approval_id")),
                run_dozeyguard_bytes=self._adapt_dozeyguard_bytes,
                dozeyguard_config=self.config.dozeyguard,
            )
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
                "canary_result": result,
            }
        if op == "remove_canary":
            self._assert_artifacts()
            result = self._canary().remove(execution_id=str(req.get("canary_execution_id")))
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": op,
                "ok": True,
                "canary_result": result,
            }
        raise ProtocolError("broker_operation_not_supported", f"operation {op} not supported")

    def _adapt_dozeyguard(self, compose: Dict[str, Any], config: BrokerDozeyguardConfig):
        from dockerpilot.secure_deploy_broker.dg_runner import run_broker_dozeyguard

        return run_broker_dozeyguard(compose, config)

    def _adapt_dozeyguard_bytes(self, compose_bytes: bytes, config: BrokerDozeyguardConfig):
        from dockerpilot.secure_deploy_broker.dg_runner import run_broker_dozeyguard_bytes

        return run_broker_dozeyguard_bytes(compose_bytes, config)

    def _canary(self) -> CanaryManager:
        if self._canary_manager is None:
            policy = CanaryPolicy(
                workdir=Path(self.config.canary_workdir) if self.config.canary_workdir else WORKDIR,
                image=self.config.canary_image or CanaryPolicy().image,
                health_timeout_seconds=int(self.config.canary_health_timeout_seconds),
                live_mode=bool(self.config.canary_live_mode),
            )
            self._canary_manager = CanaryManager(policy=policy)
        return self._canary_manager


def build_canary_config(
    socket_path: str,
    *,
    executable: str,
    policy_path: str,
    expected_peer_uid: Optional[int] = None,
    expected_binary_sha256: Optional[str] = None,
    expected_policy_sha256: Optional[str] = None,
    expected_artifact_uid: Optional[int] = None,
    deny_writable_uid: Optional[int] = None,
) -> BrokerRuntimeConfig:
    """Test/helper config. Production units must set expected_peer_uid via BrokerConfig."""
    import os

    dg = resolve_broker_dozeyguard_config(executable=executable, policy_path=policy_path)
    # Explicit UID required at runtime; for local tests default to the current test user.
    peer_uid = os.getuid() if expected_peer_uid is None else int(expected_peer_uid)
    return BrokerRuntimeConfig(
        socket_path=socket_path,
        dozeyguard=dg,
        expected_peer_uid=peer_uid,
        expected_binary_sha256=expected_binary_sha256,
        expected_policy_sha256=expected_policy_sha256,
        expected_artifact_uid=expected_artifact_uid,
        deny_writable_uid=deny_writable_uid,
        socket_activation=False,
    )
