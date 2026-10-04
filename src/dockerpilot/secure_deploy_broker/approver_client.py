"""Operator CLI for the dedicated Secure Deploy approver socket."""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
from pathlib import Path

from .protocol import PROTOCOL_VERSION, recv_message, send_message

DEFAULT_APPROVER_SOCKET = "/run/dockerpilot-secure-broker/approver.sock"


def approve_challenge(
    *,
    socket_path: str,
    challenge_id: str,
    plan_sha256: str,
    timeout: float = 15.0,
) -> dict:
    path = Path(socket_path)
    if path.is_symlink():
        raise RuntimeError("approver socket path is a symlink")
    if not path.exists():
        raise RuntimeError("approver socket missing")

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "apreq_" + secrets.token_hex(12),
        "operation": "approve_challenge",
        "challenge_id": challenge_id,
        "plan_sha256": plan_sha256,
        "client": {"name": "dockerpilot-secure-approve", "version": "1"},
    }

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        send_message(sock, payload)
        resp = recv_message(sock, timeout=timeout)
    finally:
        sock.close()

    if resp.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("approver returned unsupported protocol version")
    if resp.get("request_id") != payload["request_id"]:
        raise RuntimeError("approver response request_id mismatch")
    if resp.get("operation") != "approve_challenge":
        raise RuntimeError("approver response operation mismatch")
    if resp.get("ok") is not True:
        err = resp.get("error") or {}
        raise RuntimeError(f"{err.get('code') or 'approver_error'}: {err.get('message') or 'approval rejected'}")
    approval = resp.get("approval")
    if not isinstance(approval, dict):
        raise RuntimeError("approver response missing approval")
    return approval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dockerpilot-secure-approve")
    parser.add_argument("--challenge", required=True, dest="challenge_id")
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--socket", default=DEFAULT_APPROVER_SOCKET)
    args = parser.parse_args(argv)

    try:
        approval = approve_challenge(
            socket_path=args.socket,
            challenge_id=args.challenge_id,
            plan_sha256=args.plan_sha256,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"approval failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(approval, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
