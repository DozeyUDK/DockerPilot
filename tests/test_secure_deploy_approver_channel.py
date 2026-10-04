from __future__ import annotations

import os
import socket

import pytest

from dockerpilot.secure_deploy_broker.approval_authority import BrokerApprovalAuthority
from dockerpilot.secure_deploy_broker.approver_server import ApproverServer
from dockerpilot.secure_deploy_broker.errors import ProtocolError, VerificationError
from dockerpilot.secure_deploy_broker.peer import PeerCred
from dockerpilot.secure_deploy_broker.protocol import (
    PROTOCOL_VERSION,
    recv_message,
    send_message,
    validate_request,
)
from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer
from dockerpilot.secure_deploy_broker.verifier import BrokerDozeyguardConfig

pytestmark = pytest.mark.skipif(os.name != "posix", reason="approval authority is POSIX-only")

PLAN_ID = "plan_aaaaaaaaaaaaaaaaaaaaaaaa"
PLAN_SHA = "a" * 64
APPROVER_UID = 4242


def _authority(tmp_path):
    return BrokerApprovalAuthority(
        tmp_path / "authority",
        allowed_approver_uids=frozenset({APPROVER_UID}),
        expected_owner_uid=os.getuid(),
    )


def _server(authority):
    return BrokerServer(
        BrokerRuntimeConfig(
            socket_path=None,
            dozeyguard=BrokerDozeyguardConfig(executable="/unused", policy_path="/unused"),
            expected_peer_uid=os.getuid(),
            approval_authority=authority,
        ),
        run_dozeyguard=None,
        normalize_spec_to_compose=lambda spec: {},
        plan_firewall_actions=lambda spec, plan_sha256: {},
    )


def _fake_verification():
    return {
        "status": "pass",
        "broker_verification_sha256": "b" * 64,
        "stages": [{"name": "plan_schema", "ok": True}],
        "plan_id": PLAN_ID,
        "plan_sha256": PLAN_SHA,
        "dozeyguard_exit_code": 0,
        "blocking_findings": 0,
    }


def test_at13_client_supplied_approval_json_is_rejected_by_protocol():
    with pytest.raises(ProtocolError) as exc_info:
        validate_request(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "req_aaaaaaaa",
                "operation": "dry_run",
                "client": {"name": "dockerpilot-extras", "version": "1"},
                "plan": {"plan_id": PLAN_ID},
                "approval_id": "appr_bbbbbbbbbbbbbbbbbbbbbbbb",
                "approval": {
                    "approval_version": 1,
                    "approval_id": "appr_bbbbbbbbbbbbbbbbbbbbbbbb",
                    "plan_id": PLAN_ID,
                    "plan_sha256": PLAN_SHA,
                    "status": "approved",
                },
            }
        )
    assert exc_info.value.code == "forbidden_field"


def test_at13_dry_run_requires_broker_owned_approval(monkeypatch, tmp_path):
    authority = _authority(tmp_path)
    server = _server(authority)
    monkeypatch.setattr(
        "dockerpilot.secure_deploy_broker.server.verify_plan_independent",
        lambda *args, **kwargs: _fake_verification(),
    )

    plan = {"plan_id": PLAN_ID, "plan_sha256": PLAN_SHA}
    challenge_response = server._dispatch(
        {
            "operation": "create_approval_challenge",
            "request_id": "req_challenge",
            "plan": plan,
        }
    )
    challenge = challenge_response["approval_challenge"]
    approval_id = challenge["approval_id"]

    with pytest.raises(VerificationError) as before:
        server._dispatch(
            {
                "operation": "dry_run",
                "request_id": "req_before",
                "plan": plan,
                "approval_id": approval_id,
            }
        )
    assert before.value.code == "approval_authority_missing"

    authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )

    accepted = server._dispatch(
        {
            "operation": "dry_run",
            "request_id": "req_after",
            "plan": plan,
            "approval_id": approval_id,
        }
    )
    assert accepted["ok"] is True
    assert accepted["verification"]["dry_run"] is True
    assert any(stage["name"] == "approval_provenance" for stage in accepted["verification"]["stages"])


def test_approver_channel_uses_peercred_not_request_identity(monkeypatch, tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approver = ApproverServer(
        authority=authority,
        socket_activation=False,
        socket_path=str(tmp_path / "unused.sock"),
    )

    server_sock, client_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    monkeypatch.setattr(
        "dockerpilot.secure_deploy_broker.approver_server.get_peer_credentials",
        lambda conn: PeerCred(pid=123, uid=9999, gid=9999),
    )
    send_message(
        client_sock,
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "apreq_denied",
            "operation": "approve_challenge",
            "challenge_id": challenge["challenge_id"],
            "plan_sha256": PLAN_SHA,
            "client": {"name": "dockerpilot-secure-approve", "version": "1"},
        },
    )
    approver._handle(server_sock)
    denied = recv_message(client_sock)
    server_sock.close()
    client_sock.close()

    assert denied["ok"] is False
    assert denied["error"]["code"] == "approval_approver_uid"

    server_sock, client_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    monkeypatch.setattr(
        "dockerpilot.secure_deploy_broker.approver_server.get_peer_credentials",
        lambda conn: PeerCred(pid=124, uid=APPROVER_UID, gid=APPROVER_UID),
    )
    send_message(
        client_sock,
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "apreq_allowed",
            "operation": "approve_challenge",
            "challenge_id": challenge["challenge_id"],
            "plan_sha256": PLAN_SHA,
            "client": {"name": "dockerpilot-secure-approve", "version": "1"},
        },
    )
    approver._handle(server_sock)
    allowed = recv_message(client_sock)
    server_sock.close()
    client_sock.close()

    assert allowed["ok"] is True
    assert allowed["approval"]["provenance"] == {"kind": "unix_peer_uid", "uid": APPROVER_UID}
