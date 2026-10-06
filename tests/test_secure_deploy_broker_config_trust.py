from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dockerpilot.secure_deploy_broker import config as broker_config
from dockerpilot.secure_deploy_broker.errors import BrokerError


def _config(peer_uid: int) -> dict:
    return {
        "protocol_version": 1,
        "socket_activation": False,
        "socket_path": "/tmp/dockerpilot-secure-broker-test.sock",
        "max_frame_bytes": 2097152,
        "request_timeout_seconds": 15,
        "expected_peer_uid": peer_uid,
        "allowed_approver_uids": [os.getuid()],
        "dozeyguard_path": "/usr/libexec/dockerpilot-secure-broker/bin/dozeyguard",
        "policy_path": "/etc/dockerpilot-secure-broker/policy.toml",
        "expected_binary_sha256": "a" * 64,
        "expected_policy_sha256": "b" * 64,
        "schemas_root": "/usr/libexec/dockerpilot-secure-broker/schemas",
        "state_root": "/var/lib/dockerpilot-secure-broker",
        "allowed_operations": ["ping", "capabilities", "verify_plan", "dry_run"],
        "canary_live_mode": False,
    }


def _write_config(path: Path, *, peer_uid: int | None = None) -> dict:
    payload = _config(os.getuid() if peer_uid is None else peer_uid)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return payload


def test_load_config_rejects_group_or_other_writable_file(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path)
    path.chmod(0o666)

    with pytest.raises(BrokerError) as exc_info:
        broker_config.load_broker_config(path)
    assert exc_info.value.code == "config_writable"


def test_load_config_rejects_unexpected_owner(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path)

    with pytest.raises(BrokerError) as exc_info:
        broker_config.load_broker_config(path, expected_uid=os.getuid() + 1)
    assert exc_info.value.code == "config_owner"


def test_load_config_rejects_final_symlink(tmp_path):
    target = tmp_path / "real.json"
    _write_config(target)
    link = tmp_path / "config.json"
    link.symlink_to(target)

    with pytest.raises(BrokerError) as exc_info:
        broker_config.load_broker_config(link)
    assert exc_info.value.code == "config_symlink"


@pytest.mark.skipif(os.name != "posix", reason="FIFO semantics are POSIX-specific")
def test_load_config_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "config.json"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(BrokerError) as exc_info:
        broker_config.load_broker_config(fifo)
    assert exc_info.value.code == "config_file_type"


def test_trusted_parent_chain_rejects_writable_component(tmp_path):
    trusted_root = tmp_path / "trusted"
    nested = trusted_root / "etc" / "dockerpilot-secure-broker"
    nested.mkdir(parents=True)
    trusted_root.chmod(0o700)
    (trusted_root / "etc").chmod(0o700)
    nested.chmod(0o700)
    path = nested / "config.json"
    _write_config(path)

    broker_config._assert_trusted_parent_chain(
        path,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        stop_at=trusted_root,
    )

    nested.chmod(0o777)
    with pytest.raises(BrokerError) as exc_info:
        broker_config._assert_trusted_parent_chain(
            path,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            stop_at=trusted_root,
        )
    assert exc_info.value.code == "config_parent_writable"


def test_trusted_parent_chain_rejects_symlink_component(tmp_path):
    trusted_root = tmp_path / "trusted"
    real_parent = trusted_root / "real"
    real_parent.mkdir(parents=True)
    trusted_root.chmod(0o700)
    real_parent.chmod(0o700)
    link_parent = trusted_root / "link"
    link_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(BrokerError) as exc_info:
        broker_config._assert_trusted_parent_chain(
            link_parent / "config.json",
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            stop_at=trusted_root,
        )
    assert exc_info.value.code == "config_parent_symlink"


def test_config_bytes_are_read_from_same_open_fd_after_path_replacement(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    original = _write_config(path, peer_uid=1234)
    replacement = tmp_path / "replacement.json"
    _write_config(replacement, peer_uid=5678)

    real_read = broker_config._read_config_fd
    raced = {"done": False}

    def replace_then_read(fd: int, *, size_hint: int) -> bytes:
        if not raced["done"]:
            os.replace(replacement, path)
            raced["done"] = True
        return real_read(fd, size_hint=size_hint)

    monkeypatch.setattr(broker_config, "_read_config_fd", replace_then_read)
    loaded = broker_config.load_broker_config(
        path,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
    )

    assert raced["done"] is True
    assert loaded.expected_peer_uid == original["expected_peer_uid"] == 1234
    assert json.loads(path.read_text(encoding="utf-8"))["expected_peer_uid"] == 5678
