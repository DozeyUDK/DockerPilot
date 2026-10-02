from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dockerpilot.secure_deploy_broker.approval_authority import BrokerApprovalAuthority
from dockerpilot.secure_deploy_broker.errors import VerificationError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="broker approval authority requires POSIX file locking")


PLAN_ID = "plan_aaaaaaaaaaaaaaaaaaaaaaaa"
PLAN_SHA = "a" * 64
OTHER_SHA = "b" * 64
APPROVER_UID = 4242


def _clock(start: datetime):
    current = {"value": start}

    def now() -> datetime:
        return current["value"]

    return current, now


def _authority(tmp_path: Path, *, clock=None, allowed=frozenset({APPROVER_UID})) -> BrokerApprovalAuthority:
    return BrokerApprovalAuthority(
        tmp_path / "authority",
        allowed_approver_uids=allowed,
        expected_owner_uid=os.getuid(),
        clock=clock,
    )


def test_broker_owned_challenge_approval_roundtrip(tmp_path):
    authority = _authority(tmp_path)

    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approval = authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )
    loaded = authority.require_approval(
        approval["approval_id"],
        plan_id=PLAN_ID,
        plan_sha256=PLAN_SHA,
    )

    assert approval["approval_version"] == 2
    assert approval["challenge_id"] == challenge["challenge_id"]
    assert approval["provenance"] == {"kind": "unix_peer_uid", "uid": APPROVER_UID}
    assert loaded == approval

    approval_path = authority.approvals_dir / f'{approval["approval_id"]}.json'
    assert approval_path.stat().st_mode & 0o777 == 0o600


def test_at13_fabricated_extras_approval_has_no_broker_authority(tmp_path):
    authority = _authority(tmp_path)
    fabricated = {
        "approval_version": 1,
        "approval_id": "appr_bbbbbbbbbbbbbbbbbbbbbbbb",
        "plan_id": PLAN_ID,
        "plan_sha256": PLAN_SHA,
        "status": "approved",
        "actor": "admin",
    }

    # This is the exact architecture property #63 will wire into live broker
    # verification: client-controlled approval JSON is not evidence of authority.
    with pytest.raises(VerificationError) as exc_info:
        authority.require_approval(
            fabricated["approval_id"],
            plan_id=fabricated["plan_id"],
            plan_sha256=fabricated["plan_sha256"],
        )
    assert exc_info.value.code == "approval_authority_missing"


def test_unauthorized_approver_uid_is_rejected(tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)

    with pytest.raises(VerificationError) as exc_info:
        authority.approve_challenge(
            challenge["challenge_id"],
            approver_uid=9999,
            expected_plan_sha256=PLAN_SHA,
        )
    assert exc_info.value.code == "approval_approver_uid"


def test_challenge_is_bound_to_plan_hash_and_one_shot(tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)

    with pytest.raises(VerificationError) as wrong_hash:
        authority.approve_challenge(
            challenge["challenge_id"],
            approver_uid=APPROVER_UID,
            expected_plan_sha256=OTHER_SHA,
        )
    assert wrong_hash.value.code == "approval_challenge_hash"

    approval = authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )
    assert approval["status"] == "approved"

    with pytest.raises(VerificationError) as replay:
        authority.approve_challenge(
            challenge["challenge_id"],
            approver_uid=APPROVER_UID,
            expected_plan_sha256=PLAN_SHA,
        )
    assert replay.value.code == "approval_challenge_status"


def test_expired_challenge_and_approval_fail_closed(tmp_path):
    start = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    current, clock = _clock(start)
    authority = _authority(tmp_path, clock=clock)

    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    current["value"] = start + timedelta(seconds=121)
    with pytest.raises(VerificationError) as expired_challenge:
        authority.approve_challenge(
            challenge["challenge_id"],
            approver_uid=APPROVER_UID,
            expected_plan_sha256=PLAN_SHA,
        )
    assert expired_challenge.value.code == "approval_challenge_expired"

    current["value"] = start
    challenge2 = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approval = authority.approve_challenge(
        challenge2["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )
    current["value"] = start + timedelta(seconds=601)
    with pytest.raises(VerificationError) as expired_approval:
        authority.require_approval(
            approval["approval_id"],
            plan_id=PLAN_ID,
            plan_sha256=PLAN_SHA,
        )
    assert expired_approval.value.code == "approval_authority_expired"


def test_broker_owned_approval_cannot_be_rebound_to_other_plan(tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approval = authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )

    with pytest.raises(VerificationError) as wrong_plan:
        authority.require_approval(
            approval["approval_id"],
            plan_id="plan_cccccccccccccccccccccccc",
            plan_sha256=PLAN_SHA,
        )
    assert wrong_plan.value.code == "approval_authority_plan"

    with pytest.raises(VerificationError) as wrong_hash:
        authority.require_approval(
            approval["approval_id"],
            plan_id=PLAN_ID,
            plan_sha256=OTHER_SHA,
        )
    assert wrong_hash.value.code == "approval_authority_hash"


def test_symlink_or_mode_tamper_in_broker_ledger_is_rejected(tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approval = authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )
    approval_path = authority.approvals_dir / f'{approval["approval_id"]}.json'

    original = json.loads(approval_path.read_text(encoding="utf-8"))
    approval_path.unlink()
    target = tmp_path / "foreign.json"
    target.write_text(json.dumps(original), encoding="utf-8")
    target.chmod(0o600)
    approval_path.symlink_to(target)

    with pytest.raises(VerificationError) as symlink:
        authority.require_approval(
            approval["approval_id"],
            plan_id=PLAN_ID,
            plan_sha256=PLAN_SHA,
        )
    assert symlink.value.code == "approval_authority_symlink"

    approval_path.unlink()
    approval_path.write_text(json.dumps(original), encoding="utf-8")
    approval_path.chmod(0o644)
    with pytest.raises(VerificationError) as mode:
        authority.require_approval(
            approval["approval_id"],
            plan_id=PLAN_ID,
            plan_sha256=PLAN_SHA,
        )
    assert mode.value.code == "approval_authority_mode"


def test_existing_unsafe_state_dir_is_rejected_not_repaired(tmp_path):
    state_root = tmp_path / "authority"
    state_root.mkdir()
    state_root.chmod(0o777)
    authority = BrokerApprovalAuthority(
        state_root,
        allowed_approver_uids=frozenset({APPROVER_UID}),
        expected_owner_uid=os.getuid(),
    )

    with pytest.raises(VerificationError) as exc_info:
        authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    assert exc_info.value.code == "approval_authority_mode"
    assert state_root.stat().st_mode & 0o777 == 0o777


def test_challenge_record_limit_bounds_untrusted_request_growth(tmp_path):
    authority = BrokerApprovalAuthority(
        tmp_path / "authority",
        allowed_approver_uids=frozenset({APPROVER_UID}),
        expected_owner_uid=os.getuid(),
        max_challenge_records=1,
    )
    authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)

    with pytest.raises(VerificationError) as exc_info:
        authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    assert exc_info.value.code == "approval_authority_limit"


def test_corrupt_broker_owned_record_fails_closed(tmp_path):
    authority = _authority(tmp_path)
    challenge = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    approval = authority.approve_challenge(
        challenge["challenge_id"],
        approver_uid=APPROVER_UID,
        expected_plan_sha256=PLAN_SHA,
    )
    approval_path = authority.approvals_dir / f'{approval["approval_id"]}.json'
    corrupted = json.loads(approval_path.read_text(encoding="utf-8"))
    corrupted["approval_version"] = 1
    approval_path.write_text(json.dumps(corrupted), encoding="utf-8")
    approval_path.chmod(0o600)

    with pytest.raises(VerificationError) as exc_info:
        authority.require_approval(
            approval["approval_id"],
            plan_id=PLAN_ID,
            plan_sha256=PLAN_SHA,
        )
    assert exc_info.value.code == "approval_authority_record"


def test_expired_challenge_is_reclaimed_before_quota_check(tmp_path):
    start = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    current, clock = _clock(start)
    authority = BrokerApprovalAuthority(
        tmp_path / "authority",
        allowed_approver_uids=frozenset({APPROVER_UID}),
        expected_owner_uid=os.getuid(),
        clock=clock,
        max_challenge_records=1,
    )
    first = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)
    current["value"] = start + timedelta(seconds=121)

    second = authority.create_challenge(plan_id=PLAN_ID, plan_sha256=PLAN_SHA)

    assert second["challenge_id"] != first["challenge_id"]
    assert not (authority.challenges_dir / f'{first["challenge_id"]}.json').exists()
    assert (authority.challenges_dir / f'{second["challenge_id"]}.json').is_file()


def test_expected_owner_uid_is_required(tmp_path):
    with pytest.raises(TypeError):
        BrokerApprovalAuthority(
            tmp_path / "authority",
            allowed_approver_uids=frozenset({APPROVER_UID}),
        )
