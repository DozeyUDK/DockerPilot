"""CLI entrypoint for dockerpilot-secure-broker (standalone or socket-activated)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    os.environ["PYTHONNOUSERSITE"] = "1"
    # Refuse ambient user PYTHONPATH pollution when running as installed broker.
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
    args = parser.parse_args(argv)

    from dockerpilot.secure_deploy_broker.bundle_helpers import (
        normalize_spec_to_compose,
        plan_firewall_actions,
    )
    from dockerpilot.secure_deploy_broker.config import load_broker_config
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer
    from dockerpilot.secure_deploy_broker.verifier import resolve_broker_dozeyguard_config

    cfg = load_broker_config(Path(args.config))
    dg = resolve_broker_dozeyguard_config(
        executable=cfg.dozeyguard_path,
        policy_path=cfg.policy_path,
        expected_policy_sha256=cfg.expected_policy_sha256,
    )
    runtime = BrokerRuntimeConfig(
        socket_path=args.socket or cfg.socket_path,
        dozeyguard=dg,
        expected_peer_uid=cfg.expected_peer_uid,
        request_timeout=cfg.request_timeout_seconds,
        expected_binary_sha256=cfg.expected_binary_sha256,
        expected_policy_sha256=cfg.expected_policy_sha256,
        expected_artifact_uid=0 if os.geteuid() == 0 else None,
        expected_artifact_gid=0 if os.geteuid() == 0 else None,
        socket_activation=cfg.socket_activation,
        allowed_operations=cfg.allowed_operations,
    )
    server = BrokerServer(
        runtime,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
