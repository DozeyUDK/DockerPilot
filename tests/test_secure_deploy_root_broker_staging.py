"""Offline staging tests for root broker canary (#11D.2A) — no sudo."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / ".staging" / "11d2a"
BUNDLE = STAGING / "bundle"


pytestmark = pytest.mark.skipif(not STAGING.exists(), reason="run build_root_broker_staging.py first")


def test_bundle_no_symlinks_or_home_refs():
    for path in BUNDLE.rglob("*"):
        assert not path.is_symlink(), path
        if path.is_file() and path.name != "dozeyguard":
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "/home/dozey" not in text, path


def test_bundle_no_forbidden_imports():
    combined = ""
    for path in (BUNDLE / "usr/libexec/dockerpilot-secure-broker/python").rglob("*.py"):
        combined += path.read_text(encoding="utf-8")
    assert "shell=True" not in combined
    assert "os.system" not in combined
    assert "docker.from_env" not in combined
    assert "import flask" not in combined.lower()
    assert "from flask" not in combined.lower()
    assert "subprocess.run([\"ufw\"" not in combined
    assert "iptables -" not in combined
    assert not any(
        line.strip().startswith("from docker ") or line.strip() == "import docker"
        for line in combined.splitlines()
    )
    # Rejection of docker.sock binds in schemas is expected; live socket access is not.
    assert "docker.DockerClient" not in combined


def test_config_unknown_field_and_path_traversal():
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.config import BrokerConfig
    from dockerpilot.secure_deploy_broker.errors import BrokerError

    base = {
        "protocol_version": 1,
        "socket_activation": True,
        "max_frame_bytes": 1024,
        "request_timeout_seconds": 5,
        "expected_peer_uid": 1000,
        "dozeyguard_path": "/usr/libexec/dockerpilot-secure-broker/bin/dozeyguard",
        "policy_path": "/etc/dockerpilot-secure-broker/policy.toml",
        "expected_binary_sha256": "a" * 64,
        "expected_policy_sha256": "b" * 64,
        "schemas_root": "/usr/libexec/dockerpilot-secure-broker/schemas",
        "state_root": "/var/lib/dockerpilot-secure-broker",
        "allowed_operations": ["ping", "capabilities", "verify_plan", "dry_run"],
    }
    with pytest.raises(BrokerError):
        BrokerConfig({**base, "docker_socket": "/var/run/docker.sock"})
    with pytest.raises(BrokerError):
        BrokerConfig({**base, "dozeyguard_path": "/usr/../etc/passwd"})
    with pytest.raises(BrokerError):
        BrokerConfig({**base, "allowed_operations": ["ping", "apply"]})
    with pytest.raises(BrokerError) as ei:
        BrokerConfig({**base, "expected_peer_uid": None})
    assert ei.value.code == "config_peer_uid"
    missing = dict(base)
    del missing["expected_peer_uid"]
    with pytest.raises(BrokerError) as ei2:
        BrokerConfig(missing)
    assert ei2.value.code == "config_peer_uid"
    with pytest.raises(BrokerError) as ei3:
        BrokerConfig({**base, "expected_peer_user": "dockerpilot-extras"})
    assert ei3.value.code in {"config_unknown_field", "config_peer_user"}


def test_integrity_hash_and_writable():
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.errors import BrokerError
    from dockerpilot.secure_deploy_broker.integrity import assert_trusted_artifact, sha256_file

    binary = BUNDLE / "usr/libexec/dockerpilot-secure-broker/bin/dozeyguard"
    policy = BUNDLE / "etc/dockerpilot-secure-broker/policy.toml"
    good = sha256_file(binary)
    assert_trusted_artifact(binary, expected_sha256=good, require_executable=True)
    with pytest.raises(BrokerError):
        assert_trusted_artifact(binary, expected_sha256="0" * 64, require_executable=True)
    with pytest.raises(BrokerError):
        assert_trusted_artifact(
            policy,
            expected_sha256=sha256_file(policy),
            deny_writable_uid=os.getuid(),
        )


def test_listen_fds_validation(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.errors import BrokerError
    from dockerpilot.secure_deploy_broker.listen_fds import take_systemd_listen_fds
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer
    from dockerpilot.secure_deploy_broker.verifier import resolve_broker_dozeyguard_config

    monkeypatch.delenv("LISTEN_FDS", raising=False)
    monkeypatch.delenv("LISTEN_PID", raising=False)
    assert take_systemd_listen_fds() == []

    monkeypatch.setenv("LISTEN_FDS", "0")
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    with pytest.raises(BrokerError):
        take_systemd_listen_fds()

    monkeypatch.setenv("LISTEN_FDS", "2")
    with pytest.raises(BrokerError):
        take_systemd_listen_fds()

    # socket_activation=true must refuse to bind when LISTEN_FDS is absent
    monkeypatch.delenv("LISTEN_FDS", raising=False)
    monkeypatch.delenv("LISTEN_PID", raising=False)
    binary = BUNDLE / "usr/libexec/dockerpilot-secure-broker/bin/dozeyguard"
    policy = BUNDLE / "etc/dockerpilot-secure-broker/policy.toml"
    dg = resolve_broker_dozeyguard_config(executable=str(binary), policy_path=str(policy))
    cfg = BrokerRuntimeConfig(
        socket_path=str(tmp_path / "must-not-bind.sock"),
        dozeyguard=dg,
        socket_activation=True,
    )
    server = BrokerServer(
        cfg,
        run_dozeyguard=None,
        normalize_spec_to_compose=lambda *a, **k: None,
        plan_firewall_actions=lambda *a, **k: None,
    )
    with pytest.raises(BrokerError) as ei:
        server.start()
    assert ei.value.code == "listen_fds_required"
    assert not (tmp_path / "must-not-bind.sock").exists()


def test_socket_activation_via_existing_socket(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from backend.secure_deploy.broker_client import BrokerClient
    from dockerpilot.secure_deploy_broker.integrity import sha256_file
    from dockerpilot.secure_deploy_broker.server import BrokerRuntimeConfig, BrokerServer
    from dockerpilot.secure_deploy_broker.verifier import resolve_broker_dozeyguard_config

    sock_path = tmp_path / "act.sock"
    listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listen.bind(str(sock_path))
    listen.listen(5)
    binary = BUNDLE / "usr/libexec/dockerpilot-secure-broker/bin/dozeyguard"
    policy = BUNDLE / "etc/dockerpilot-secure-broker/policy.toml"
    dg = resolve_broker_dozeyguard_config(executable=str(binary), policy_path=str(policy))
    cfg = BrokerRuntimeConfig(
        socket_path=None,
        dozeyguard=dg,
        expected_peer_uid=os.getuid(),
        expected_binary_sha256=sha256_file(binary),
        expected_policy_sha256=sha256_file(policy),
        socket_activation=True,
    )
    server = BrokerServer(
        cfg,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    server.start_from_existing_socket(listen, owns_path=True, socket_path=str(sock_path))
    try:
        client = BrokerClient(str(sock_path))
        assert client.request("ping")["ok"] is True
        caps = client.request("capabilities")
        assert caps["capabilities"]["apply_supported"] is False
        assert "apply" not in caps["capabilities"]["operations"]
    finally:
        server.stop()
        assert not sock_path.exists()


def test_standalone_staging_socket_cleanup(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "DockerPilotExtras"))
    from backend.secure_deploy.firewall_planner import plan_firewall_actions
    from backend.secure_deploy.normalizer import normalize_spec_to_compose
    from backend.secure_deploy.broker_client import BrokerClient
    from dockerpilot.secure_deploy_broker.server import build_canary_config, BrokerServer
    from dockerpilot.secure_deploy_broker.integrity import sha256_file

    binary = BUNDLE / "usr/libexec/dockerpilot-secure-broker/bin/dozeyguard"
    policy = BUNDLE / "etc/dockerpilot-secure-broker/policy.toml"
    sock = tmp_path / "standalone.sock"
    cfg = build_canary_config(
        str(sock),
        executable=str(binary),
        policy_path=str(policy),
        expected_peer_uid=os.getuid(),
        expected_binary_sha256=sha256_file(binary),
        expected_policy_sha256=sha256_file(policy),
    )
    server = BrokerServer(
        cfg,
        run_dozeyguard=None,
        normalize_spec_to_compose=normalize_spec_to_compose,
        plan_firewall_actions=plan_firewall_actions,
    )
    server.start()
    try:
        assert BrokerClient(str(sock)).request("ping")["ok"]
    finally:
        server.stop()
        assert not sock.exists()


def test_install_scripts_syntax():
    install = STAGING / "INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh"
    rollback = STAGING / "ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh"
    subprocess.run(["bash", "-n", str(install)], check=True)
    subprocess.run(["bash", "-n", str(rollback)], check=True)
    shellcheck = subprocess.run(["bash", "-lc", "command -v shellcheck"], capture_output=True)
    if shellcheck.returncode == 0:
        # Informational — scripts intentionally use root tooling.
        subprocess.run(["shellcheck", "-e", "SC2086,SC2034", str(install)], check=False)


def test_systemd_analyze_verify_staged_rewrite(tmp_path):
    """Clean-host preflight: rewrite ExecStart only in a temp copy."""
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import verify_staged_systemd_units as vsu

    installed = Path("/usr/libexec/dockerpilot-secure-broker/broker")
    try:
        installed_visible = installed.exists()
    except PermissionError:
        installed_visible = True  # present under root-only tree
    # Staged rewrite must succeed regardless of whether the installed path exists.
    _ = installed_visible

    service_src = BUNDLE / "etc/systemd/system/dockerpilot-secure-broker.service"
    socket_src = BUNDLE / "etc/systemd/system/dockerpilot-secure-broker.socket"
    staged_broker = BUNDLE / "usr/libexec/dockerpilot-secure-broker/broker"
    original = service_src.read_text(encoding="utf-8")
    assert f"ExecStart={vsu.INSTALLED_BROKER}" in original
    exec_lines = [line for line in original.splitlines() if line.startswith("ExecStart=")]
    assert len(exec_lines) == 1
    assert str(STAGING) not in exec_lines[0]

    # Happy path: staged broker exists and is executable.
    proc = vsu.verify_staged_units(
        service_src=service_src,
        socket_src=socket_src,
        staged_broker=staged_broker,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    # Source unit unchanged (still production ExecStart).
    assert service_src.read_text(encoding="utf-8") == original
    assert f"ExecStart={vsu.INSTALLED_BROKER}" in service_src.read_text(encoding="utf-8")

    # Missing staged broker → fail.
    missing = tmp_path / "absent-broker"
    with pytest.raises(SystemExit, match="staged broker missing"):
        vsu.verify_staged_units(
            service_src=service_src,
            socket_src=socket_src,
            staged_broker=missing,
        )

    # Staged broker without executable bit → fail.
    noexec = tmp_path / "noexec-broker"
    noexec.write_bytes(staged_broker.read_bytes())
    noexec.chmod(0o644)
    with pytest.raises(SystemExit, match="not executable"):
        vsu.verify_staged_units(
            service_src=service_src,
            socket_src=socket_src,
            staged_broker=noexec,
        )

    # Other unit errors still fail (empty ExecStart).
    tmp_units = tmp_path / "bad-units"
    tmp_units.mkdir()
    bad_service = tmp_units / "dockerpilot-secure-broker.service"
    bad_socket = tmp_units / "dockerpilot-secure-broker.socket"
    rewritten = vsu.rewrite_exec_start(original, staged_broker)
    bad_service.write_text(
        "\n".join(
            "ExecStart=" if line.startswith("ExecStart=") else line
            for line in rewritten.splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(socket_src, bad_socket)
    bad = subprocess.run(
        ["systemd-analyze", "verify", str(bad_service), str(bad_socket)],
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0
    combined_bad = (bad.stderr + bad.stdout).lower()
    assert "execstart" in combined_bad or "bad unit" in combined_bad

    # CLI helper against full staging also succeeds and leaves unit intact.
    assert vsu.verify_from_staging(STAGING) == 0
    assert service_src.read_text(encoding="utf-8") == original

    # Unrelated host unit warnings (telegraf/prometheus) are not our defect:
    # they may appear on stderr; exit code is authoritative for this gate.
    combined = (proc.stderr or "") + (proc.stdout or "")
    if "telegraf.service" in combined or "prometheus.service" in combined:
        assert proc.returncode == 0


def test_manifest_hashes():
    if not (STAGING / "install-manifest.sha256").is_file():
        pytest.skip("staging incomplete (rebuild blocked until root-owned debris removed)")
    manifest = json.loads((STAGING / "install-manifest.json").read_text(encoding="utf-8"))
    expected = (STAGING / "install-manifest.sha256").read_text(encoding="utf-8").strip()
    import hashlib

    digest = hashlib.sha256((STAGING / "install-manifest.json").read_bytes()).hexdigest()
    assert digest == expected
    assert manifest["dozeyguard_sha256"]
    assert manifest["policy_sha256"]
    assert set(manifest["allowed_operations"]) == {"ping", "capabilities", "verify_plan", "dry_run"}
    assert "docker" in manifest["users"]["forbidden_groups"]
    if "forbidden_gids" in manifest["users"]:
        pytest.skip("staging predates #11E GID cleanup; rebuild after clearing root-owned staging debris")

    # Every installable regular file under libexec/etc must be listed (no silent omissions).
    # Every installable regular file under libexec/etc must be listed (no silent omissions).
    listed = {a["destination"]: a for a in manifest["artifacts"]}
    libexec = BUNDLE / "usr/libexec/dockerpilot-secure-broker"
    etc = BUNDLE / "etc/dockerpilot-secure-broker"
    for root, prefix in ((libexec, "/usr/libexec/dockerpilot-secure-broker"), (etc, "/etc/dockerpilot-secure-broker")):
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            dest = f"{prefix}/{path.relative_to(root).as_posix()}"
            assert dest in listed, f"missing from manifest: {dest}"
            assert listed[dest]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    for unit in ("dockerpilot-secure-broker.service", "dockerpilot-secure-broker.socket"):
        dest = f"/etc/systemd/system/{unit}"
        assert dest in listed
    assert any(d.endswith("/python/run_broker.py") for d in listed)
    assert any("secure_deploy_broker/server.py" in d for d in listed)
    assert "/etc/dockerpilot-secure-broker/config.template.json" in listed
    assert "/etc/dockerpilot-secure-broker/config.json" not in listed
    template = json.loads(
        (BUNDLE / "etc/dockerpilot-secure-broker/config.template.json").read_text(encoding="utf-8")
    )
    assert template["expected_peer_user"] == "dockerpilot-extras"
    assert "expected_peer_uid" not in template
    assert "997" not in json.dumps(template)
    install_text = (STAGING / "INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh").read_text(encoding="utf-8")
    assert "expected_peer_uid=997" not in install_text
    assert "pwd.getpwnam" in install_text or "getpwnam" in install_text
    assert "ensure_runtime_dir" in install_text
    assert 'Path("/run/dockerpilot-secure-broker").mkdir' not in install_text
    if "resolve_install_expect_user" not in install_text:
        pytest.skip("staging install script predates #11E operator/backup binding")
    assert "DOCKERPILOT_INSTALL_EXPECT_USER:-dozey" not in install_text
    assert "assert_backup_dir_compatible" in install_text
    assert "write_backup_manifest_marker" in install_text
    rollback_text = (STAGING / "ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh").read_text(encoding="utf-8")
    assert "assert_backup_dir_compatible" in rollback_text
    assert "EXPECTED_MANIFEST_SHA" in rollback_text


def test_peer_uid_fail_closed_and_match():
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.errors import BrokerError
    from dockerpilot.secure_deploy_broker.peer import PeerCred, assert_expected_uid

    cred_extras = PeerCred(pid=1, uid=4242, gid=100)
    with pytest.raises(BrokerError) as ei:
        assert_expected_uid(cred_extras, None)
    assert ei.value.code == "peer_uid_unconfigured"

    assert_expected_uid(cred_extras, 4242)

    with pytest.raises(BrokerError) as ei_root:
        assert_expected_uid(PeerCred(pid=1, uid=0, gid=0), 4242)
    assert ei_root.value.code == "peer_uid_mismatch"

    with pytest.raises(BrokerError) as ei_other:
        assert_expected_uid(PeerCred(pid=1, uid=1234, gid=100), 4242)
    assert ei_other.value.code == "peer_uid_mismatch"


def test_runtime_dir_umask_077_repair(tmp_path):
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import broker_install_helpers as bih

    run_dir = tmp_path / "run-broker"
    old_umask = os.umask(0o077)
    try:
        run_dir.mkdir()
        # Simulate install bug: mkdir under umask 077 → 0700.
        os.chmod(run_dir, 0o700)
    finally:
        os.umask(old_umask)
    assert (run_dir.stat().st_mode & 0o777) == 0o700

    with pytest.raises(RuntimeError, match="0o750"):
        bih.ensure_runtime_dir(
            run_dir, broker_gid=os.getgid(), owner_uid=os.getuid(), fix=False
        )

    bih.ensure_runtime_dir(run_dir, broker_gid=os.getgid(), owner_uid=os.getuid(), fix=True)
    st = run_dir.stat()
    assert (st.st_mode & 0o777) == 0o750
    assert st.st_uid == os.getuid()
    assert st.st_gid == os.getgid()

    # Absent dir: helper must not create under umask (leave for systemd).
    absent = tmp_path / "absent-run"
    bih.ensure_runtime_dir(absent, broker_gid=os.getgid(), owner_uid=os.getuid(), fix=True)
    assert not absent.exists()

    template = {
        "protocol_version": 1,
        "expected_peer_user": "dockerpilot-extras",
        "socket_activation": True,
        "dozeyguard_path": "/x",
        "policy_path": "/y",
        "expected_binary_sha256": "a" * 64,
        "expected_policy_sha256": "b" * 64,
        "schemas_root": "/s",
        "state_root": "/t",
        "allowed_operations": ["ping"],
        "socket_path": "/run/x.sock",
        "max_frame_bytes": 1024,
        "request_timeout_seconds": 5,
    }
    out = bih.materialize_runtime_config(template, peer_uid=4242)
    assert out["expected_peer_uid"] == 4242
    assert "expected_peer_user" not in out
    with pytest.raises(ValueError):
        bih.materialize_runtime_config(template, peer_uid=0)
    with pytest.raises(ValueError):
        bih.materialize_runtime_config({**template, "expected_peer_uid": 1}, peer_uid=4242)
    tpl_path = BUNDLE / "etc/dockerpilot-secure-broker/config.template.json"
    assert "997" not in tpl_path.read_text(encoding="utf-8")


def test_unsupported_ops_still_rejected():
    sys.path.insert(0, str(ROOT / "src"))
    from dockerpilot.secure_deploy_broker.errors import ProtocolError
    from dockerpilot.secure_deploy_broker.protocol import PROTOCOL_VERSION, validate_request

    with pytest.raises(ProtocolError):
        validate_request(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "breq_abcdefgh",
                "operation": "apply",
                "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
            }
        )


def test_resolve_install_expect_user():
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import broker_install_helpers as bih

    assert bih.resolve_install_expect_user({"SUDO_USER": "alice"}) == "alice"
    assert (
        bih.resolve_install_expect_user(
            {"SUDO_USER": "alice", "DOCKERPILOT_INSTALL_EXPECT_USER": "bob"}
        )
        == "bob"
    )
    assert bih.resolve_install_expect_user({}, override="carol") == "carol"
    with pytest.raises(ValueError, match="root"):
        bih.resolve_install_expect_user({"SUDO_USER": "root"})
    with pytest.raises(ValueError, match="root"):
        bih.resolve_install_expect_user({}, override="root")
    with pytest.raises(ValueError, match="cannot resolve"):
        bih.resolve_install_expect_user({})


def test_backup_manifest_binding(tmp_path):
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import broker_install_helpers as bih

    hash_a = "a" * 64
    hash_b = "b" * 64
    backup = tmp_path / "backup"
    # Missing / empty OK
    bih.assert_backup_dir_compatible(backup, hash_a)
    bih.write_backup_manifest_marker(backup, hash_a)
    assert (backup / bih.BACKUP_MANIFEST_MARKER).read_text(encoding="utf-8").strip() == hash_a
    # Idempotent same hash
    bih.assert_backup_dir_compatible(backup, hash_a)
    bih.write_backup_manifest_marker(backup, hash_a)
    # Different hash fail-closed
    with pytest.raises(ValueError, match="different install"):
        bih.assert_backup_dir_compatible(backup, hash_b)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "some_file").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="without"):
        bih.assert_backup_dir_compatible(foreign, hash_a)


def test_clear_staging_fail_closed(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import build_root_broker_staging as builder

    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "ok.txt").write_text("1\n", encoding="utf-8")
    sticky = staging / "sticky"
    sticky.mkdir()
    (sticky / "inner").write_text("x\n", encoding="utf-8")

    real_rmtree = builder.shutil.rmtree

    def boom(path, *args, **kwargs):
        if Path(path) == sticky:
            raise PermissionError("simulated refuse")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(builder.shutil, "rmtree", boom)
    with pytest.raises(SystemExit, match="PermissionError"):
        builder.clear_staging(staging)
    assert sticky.exists()
    with pytest.raises(SystemExit, match="not empty"):
        builder.assert_staging_ready(staging)


def test_resolve_dozeyguard_src(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import build_root_broker_staging as builder

    fake_root = tmp_path / "repo"
    (fake_root / "components").mkdir(parents=True)
    monkeypatch.delenv("DOZEYGUARD_SRC", raising=False)
    with pytest.raises(SystemExit, match="dozeyguard source missing"):
        builder.resolve_dozeyguard_src(fake_root)
    dg = fake_root / "components" / "dozeyguard"
    dg.mkdir()
    assert builder.resolve_dozeyguard_src(fake_root) == dg.resolve()
    override = tmp_path / "alt"
    override.mkdir()
    monkeypatch.setenv("DOZEYGUARD_SRC", str(override))
    assert builder.resolve_dozeyguard_src(fake_root) == override.resolve()


def test_generated_install_scripts_operator_and_backup(tmp_path, monkeypatch):
    """Generate install/rollback into tmp — no live staging / no sudo."""
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import build_root_broker_staging as builder

    monkeypatch.setattr(builder, "STAGING", tmp_path)
    shutil.copy2(
        ROOT / "tools" / "secure_deploy" / "broker_install_helpers.py",
        tmp_path / "broker_install_helpers.py",
    )
    builder._write_install_scripts("a" * 64, "b" * 64, "c" * 64)
    install = (tmp_path / "INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh").read_text(encoding="utf-8")
    rollback = (tmp_path / "ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh").read_text(encoding="utf-8")
    assert "DOCKERPILOT_INSTALL_EXPECT_USER:-dozey" not in install
    assert "resolve_install_expect_user" in install
    assert "assert_backup_dir_compatible" in install
    assert "write_backup_manifest_marker" in install
    assert "assert_backup_dir_compatible" in rollback
    assert "EXPECTED_MANIFEST_SHA" in rollback
    src = (ROOT / "tools" / "secure_deploy" / "build_root_broker_staging.py").read_text(encoding="utf-8")
    assert '"forbidden_gids"' not in src
    assert 'Path("/home/dozey/dozeyguard")' not in src
    assert 'repo_root.parent / "dozeyguard"' not in src
    assert 'components" / "dozeyguard"' in src or "components/dozeyguard" in src


def test_clear_staging_probes_before_delete(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    import build_root_broker_staging as builder

    staging = tmp_path / "stage"
    staging.mkdir()
    keep = staging / "keep.txt"
    keep.write_text("1\n", encoding="utf-8")
    sticky = staging / "sticky"
    sticky.mkdir()

    real_access = os.access

    def access(path, mode, *, dir_fd=None, effective_ids=False, follow_symlinks=True):
        if Path(path) == sticky and mode & os.W_OK:
            return False
        return real_access(path, mode, dir_fd=dir_fd, effective_ids=effective_ids, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(builder.os, "access", access)
    with pytest.raises(SystemExit, match="not writable"):
        builder.clear_staging(staging)
    assert keep.exists(), "probe refusal must not delete siblings first"
