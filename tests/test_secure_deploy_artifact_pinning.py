from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from dockerpilot.secure_deploy_broker import dg_runner
from dockerpilot.secure_deploy_broker.errors import BrokerError
from dockerpilot.secure_deploy_broker.integrity import open_trusted_artifact
from dockerpilot.secure_deploy_broker.verifier import resolve_broker_dozeyguard_config

ROOT = Path(__file__).resolve().parents[1]
FAKE_DG = ROOT / "tests" / "fixtures" / "secure_deploy_preview" / "fake_dozeyguard.py"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/fd").is_dir(),
    reason="broker artifact pinning requires Linux /proc/self/fd semantics",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _trusted_fixture(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "dozeyguard"
    shutil.copyfile(FAKE_DG, executable)
    executable.chmod(0o755)
    policy = tmp_path / "policy.toml"
    policy.write_text("[policy]\nname = 'trusted'\n", encoding="utf-8")
    policy.chmod(0o644)
    return executable, policy


def test_open_trusted_artifact_keeps_verified_inode_after_atomic_replace(tmp_path):
    artifact = tmp_path / "artifact.bin"
    trusted_bytes = b"trusted broker artifact\n"
    artifact.write_bytes(trusted_bytes)
    artifact.chmod(0o644)

    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"attacker replacement\n")
    replacement.chmod(0o644)

    with open_trusted_artifact(
        artifact,
        expected_sha256=_sha256(trusted_bytes),
    ) as pinned:
        os.replace(replacement, artifact)
        assert artifact.read_bytes() == b"attacker replacement\n"
        assert Path(pinned.proc_path).read_bytes() == trusted_bytes


def test_runner_rejects_replacement_that_happens_before_verified_open(tmp_path):
    executable, policy = _trusted_fixture(tmp_path)
    config = resolve_broker_dozeyguard_config(
        executable=str(executable),
        policy_path=str(policy),
    )

    executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(BrokerError) as exc_info:
        dg_runner.run_broker_dozeyguard_bytes(b'{"services":{}}', config)
    assert exc_info.value.code == "artifact_hash"


def test_runner_pins_binary_and_policy_across_atomic_replacement_before_popen(monkeypatch, tmp_path):
    executable, policy = _trusted_fixture(tmp_path)
    trusted_executable = executable.read_bytes()
    trusted_policy = policy.read_bytes()
    config = resolve_broker_dozeyguard_config(
        executable=str(executable),
        policy_path=str(policy),
    )

    evil_executable = tmp_path / "evil-dozeyguard"
    evil_executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    evil_executable.chmod(0o755)
    evil_policy = tmp_path / "evil-policy.toml"
    evil_policy.write_text("[policy]\nname = 'attacker'\n", encoding="utf-8")
    evil_policy.chmod(0o644)

    real_popen = dg_runner.subprocess.Popen
    captured: dict[str, object] = {}

    def racing_popen(argv, *args, **kwargs):
        captured["argv"] = list(argv)
        captured["pass_fds"] = tuple(kwargs.get("pass_fds") or ())

        assert str(argv[0]).startswith("/proc/self/fd/")
        policy_index = argv.index("--policy") + 1
        assert str(argv[policy_index]).startswith("/proc/self/fd/")
        assert Path(argv[0]).read_bytes() == trusted_executable
        assert Path(argv[policy_index]).read_bytes() == trusted_policy

        # AT-22: replace both original pathnames after verification but before
        # exec/open in the child. The inherited FDs must stay on trusted bytes.
        os.replace(evil_executable, executable)
        os.replace(evil_policy, policy)

        assert executable.read_bytes().startswith(b"#!/bin/sh")
        assert policy.read_text(encoding="utf-8").find("attacker") >= 0
        assert Path(argv[0]).read_bytes() == trusted_executable
        assert Path(argv[policy_index]).read_bytes() == trusted_policy
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(dg_runner.subprocess, "Popen", racing_popen)

    report = dg_runner.run_broker_dozeyguard_bytes(b'{"services":{}}', config)

    assert report["result"]["status"] == "pass"
    assert report["result"]["exit_code"] == 0
    assert len(captured["pass_fds"]) == 2
