"""CLI entrypoint for DockerPilot secure broker / approver services."""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path


def _serve_until_stopped(server) -> int:
    server.start()
    stopped = False

    def _stop(*_a):
        nonlocal stopped
        if stopped:
            return
        stopped = True
        server.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopped:
        signal.pause()
    return 0


def main(argv: list[str] | None = None) -> int:
    os.environ["PYTHONNOUSERSITE"] = "1"
    if os.environ.get("DOCKERPILOT_BROKER_STRICT_ENV", "1") == "1":
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("PYTHONHOME", None)

    parser = argparse.ArgumentParser(prog="dockerpilot-secure-broker")
    parser.add_argument("--config", required=True, help="Absolute path to broker config JSON")
    parser.add_argument(
        "--socket",
        default=None,
        help="Standalone Unix socket path (ignored when LISTEN_FDS is set)",
    )
    parser.add_argument(
        "--approver",
        action="store_true",
        help="Run the isolated approval service instead of the control broker",
    )
    args = parser.parse_args(argv)

    from dockerpilot.secure_deploy_broker.approval_authority import BrokerApprovalAuthority
    from dockerpilot.secure_deploy_broker.config import load_broker_config

    running_as_root = os.geteuid() == 0
    expected_artifact_uid = 0 if running_as_root else None
    expected_artifact_gid = 0 if running_as_root else None
    cfg = load_broker_config(
        Path(args.config),
        expected_uid=expected_artifact_uid,
        expected_gid=expected_artifact_gid,
        require_trusted_parents=running_as_root,
    )
    authority = BrokerApprovalAuthority(
        Path(cfg.state_root) / "approval_authority",
        allowed_approver_uids=cfg.allowed_approver_uids,
        expected_owner_uid=os.geteuid(),
    )

    if args.approver:
        from dockerpilot.secure_deploy_broker.approver_server import ApproverServer

        default_socket = str(Path(cfg.socket_path or "/run/dockerpilot-secure-broker/broker.sock").with_name("approver.sock"))
        server = ApproverServer(
            authority=authority,
            socket_path=args.socket or default_socket,
            request_timeout=cfg.request_timeout_seconds,
            socket_activation=cfg.socket_activation,
        )
        return _serve_until_stopped(server)

    from dockerpilot.secure_deploy_broker.bundle_helpers import (
        normalize_spec_to_compose,
        plan_firewall_actions,
    )
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer
    from dockerpilot.secure_deploy_broker.verifier import resolve_broker_dozeyguard_config

    dg = resolve_broker_dozeyguard_config(
        executable=cfg.dozeyguard_path,
        policy_path=cfg.policy_path,
        expected_binary_sha256=cfg.expected_binary_sha256,
        expected_policy_sha256=cfg.expected_policy_sha256,
        expected_artifact_uid=expected_artifact_uid,
        expected_artifact_gid=expected_artifact_gid,
    )
    runtime = BrokerRuntimeConfig(
        socket_path=args.socket or cfg.socket_path,
        dozeyguard=dg,
        expected_peer_uid=cfg.expected_peer_uid,
        request_timeout=cfg.request_timeout_seconds,
        expected_binary_sha256=cfg.expected_binary_sha256,
        expected_policy_sha256=cfg.expected_policy_sha256,
        expected_artifact_uid=expected_artifact_uid,
        expected_artifact_gid=expected_artifact_gid,
        socket_activation=cfg.socket_activation,
        allowed_operations=cfg.allowed_operations,
        canary_workdir=cfg.canary_workdir,
        canary_image=cfg.canary_image,
        canary_health_timeout_seconds=cfg.canary_health_timeout_seconds,
        canary_staged_bundle_ttl_seconds=cfg.canary_staged_bundle_ttl_seconds,
        canary_live_mode=cfg.canary_live_mode,
        approval_authority=authority,
    )
    server = BrokerServer(
        runtime,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    return _serve_until_stopped(server)


if __name__ == "__main__":
    raise SystemExit(main())
