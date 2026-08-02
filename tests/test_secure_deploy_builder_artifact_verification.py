"""Root broker staging artifact verification tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "tools" / "secure_deploy" / "build_root_broker_staging.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_root_broker_staging", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(builder, staging: Path, rel: str, dest: str, content: str, mode: str = "0o644") -> dict:
    path = staging / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "destination": dest,
        "staging_path": rel,
        "sha256": builder.sha256_file(path),
        "mode": mode,
        "type": "file",
    }


def test_manifest_artifact_verification_rejects_tampered_python_and_unit_before_install(tmp_path):
    builder = _load_builder()
    staging = tmp_path / "staging"
    python_art = _artifact(
        builder,
        staging,
        "bundle/usr/libexec/dockerpilot-secure-broker/python/run_broker.py",
        "/usr/libexec/dockerpilot-secure-broker/python/run_broker.py",
        "print('ok')\n",
    )
    unit_art = _artifact(
        builder,
        staging,
        "bundle/etc/systemd/system/dockerpilot-secure-broker.service",
        "/etc/systemd/system/dockerpilot-secure-broker.service",
        "[Service]\nExecStart=/usr/libexec/dockerpilot-secure-broker/broker\n",
    )
    manifest = {"artifacts": [python_art, unit_art]}

    builder.verify_manifest_artifacts(staging, manifest)

    (staging / python_art["staging_path"]).write_text("print('tampered')\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        builder.verify_manifest_artifacts(staging, manifest)

    (staging / python_art["staging_path"]).write_text("print('ok')\n", encoding="utf-8")
    (staging / unit_art["staging_path"]).write_text("[Service]\nExecStart=/bin/sh\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        builder.verify_manifest_artifacts(staging, manifest)


def test_manifest_artifact_verification_rejects_symlink_destination_mode_and_traversal(tmp_path):
    builder = _load_builder()
    staging = tmp_path / "staging"
    artifact = _artifact(
        builder,
        staging,
        "bundle/usr/libexec/dockerpilot-secure-broker/broker",
        "/usr/libexec/dockerpilot-secure-broker/broker",
        "#!/bin/sh\n",
        mode="0o755",
    )
    builder.verify_manifest_artifacts(staging, {"artifacts": [artifact]})

    symlink_art = dict(artifact)
    symlink_path = staging / "bundle/usr/libexec/dockerpilot-secure-broker/link"
    symlink_path.symlink_to(staging / artifact["staging_path"])
    symlink_art["staging_path"] = str(symlink_path.relative_to(staging))
    with pytest.raises(SystemExit, match="missing/invalid staging artifact"):
        builder.verify_manifest_artifacts(staging, {"artifacts": [symlink_art]})

    bad_dest = dict(artifact, destination="/usr/libexec/dockerpilot-secure-broker/../../evil")
    with pytest.raises(SystemExit, match="path traversal rejected"):
        builder.verify_manifest_artifacts(staging, {"artifacts": [bad_dest]})

    bad_mode = dict(artifact, mode="0o666")
    with pytest.raises(SystemExit, match="group/other-writable"):
        builder.verify_manifest_artifacts(staging, {"artifacts": [bad_mode]})

    bad_staging = dict(artifact, staging_path="../outside")
    with pytest.raises(SystemExit, match="invalid staging_path"):
        builder.verify_manifest_artifacts(staging, {"artifacts": [bad_staging]})


def test_generated_install_verifies_artifacts_before_first_mutation(tmp_path, monkeypatch):
    builder = _load_builder()
    monkeypatch.setattr(builder, "STAGING", tmp_path)
    builder._write_install_scripts("a" * 64, "b" * 64, "c" * 64)
    install_text = (tmp_path / "INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh").read_text(encoding="utf-8")

    verify_pos = install_text.index("all manifest artifacts verified before mutation")
    mutation_tokens = [
        'mkdir -p "$BACKUP_DIR"',
        "MUTATING=1",
        'subprocess.check_call(["groupadd"',
        '"useradd", "--system"',
        'subprocess.check_call(["usermod"',
        "systemctl daemon-reload",
        "systemctl start dockerpilot-secure-broker.socket",
    ]
    assert all(verify_pos < install_text.index(token) for token in mutation_tokens)
