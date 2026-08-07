#!/usr/bin/env python3
"""Build offline staging bundle for root broker canary (#11D.2A). Does not install."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / ".staging" / "11d2a"
BUNDLE = STAGING / "bundle"
POLICY_SRC = ROOT / "deploy" / "policy" / "broker-preview.toml"
BACKUP_DIR_CANARY = "/var/backups/dockerpilot-secure-broker-11d2a"


def resolve_dozeyguard_src(repo_root: Path = ROOT) -> Path:
    """Locate dozeyguard sources: ``DOZEYGUARD_SRC`` or monorepo ``components/dozeyguard``."""
    env = os.environ.get("DOZEYGUARD_SRC")
    if env:
        path = Path(env).expanduser().resolve()
    else:
        path = (repo_root / "components" / "dozeyguard").resolve()
    if not path.is_dir():
        raise SystemExit(
            f"dozeyguard source missing: {path}\n"
            "Expected monorepo path components/dozeyguard, or set DOZEYGUARD_SRC "
            "to an alternate checkout for local overrides."
        )
    return path


DEST_PREFIX = {
    "libexec": "/usr/libexec/dockerpilot-secure-broker",
    "etc": "/etc/dockerpilot-secure-broker",
    "var": "/var/lib/dockerpilot-secure-broker",
    "run": "/run/dockerpilot-secure-broker",
}

ALLOWED_INSTALL_EXACT = {
    "/etc/systemd/system/dockerpilot-secure-broker.service",
    "/etc/systemd/system/dockerpilot-secure-broker.socket",
}
ALLOWED_INSTALL_PREFIXES = (
    "/usr/libexec/dockerpilot-secure-broker/",
    "/etc/dockerpilot-secure-broker/",
    "/var/lib/dockerpilot-secure-broker/",
    "/run/dockerpilot-secure-broker/",
)

PURE_MODULES = [
    ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "normalizer.py",
    ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "firewall_planner.py",
    ROOT / "DockerPilotExtras" / "backend" / "secure_deploy" / "errors.py",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def copy_file(src: Path, dst: Path, mode: int) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    os.chmod(dst, mode)
    if dst.is_symlink():
        raise RuntimeError(f"symlink not allowed in bundle: {dst}")
    digest = sha256_file(dst)
    return {
        "source": str(src),
        "destination": None,  # filled by caller for install paths
        "staging_path": str(dst.relative_to(STAGING)),
        "sha256": digest,
        "mode": oct(mode),
        "type": "file",
        "required": True,
        "rollback_action": "restore_or_remove",
    }


def parse_artifact_mode(mode: object, *, destination: str) -> int:
    if isinstance(mode, str):
        try:
            parsed = int(mode, 8)
        except ValueError as exc:
            raise SystemExit(f"invalid artifact mode for {destination}: {mode}") from exc
    elif isinstance(mode, int):
        parsed = mode
    else:
        raise SystemExit(f"invalid artifact mode for {destination}: {mode!r}")
    if parsed < 0 or parsed > 0o777:
        raise SystemExit(f"artifact mode out of range for {destination}: {oct(parsed)}")
    if parsed & 0o022:
        raise SystemExit(f"refusing group/other-writable mode for {destination}")
    return parsed


def assert_allowed_install_destination(destination: str | Path) -> str:
    text = str(destination)
    dest = Path(text)
    if not dest.is_absolute() or ".." in dest.parts:
        raise SystemExit(f"path traversal rejected: {text}")
    if text in ALLOWED_INSTALL_EXACT:
        return text
    if any(text.startswith(prefix) for prefix in ALLOWED_INSTALL_PREFIXES):
        return text
    raise SystemExit(f"destination outside allowlist: {text}")


def artifact_source_path(staging: Path, artifact: dict) -> Path:
    raw = artifact.get("staging_path")
    if not isinstance(raw, str) or not raw:
        raise SystemExit(f"missing staging_path for {artifact.get('destination')}")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"invalid staging_path for {artifact.get('destination')}: {raw}")
    return staging / rel


def verify_manifest_artifacts(staging: Path, manifest: dict) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise SystemExit("manifest artifacts must be a list")
    for artifact in artifacts:
        if artifact.get("type") != "file":
            raise SystemExit(f"unsupported artifact type for {artifact.get('destination')}")
        destination = artifact.get("destination")
        if not isinstance(destination, str) or not destination:
            raise SystemExit("manifest artifact missing destination")
        assert_allowed_install_destination(destination)
        if destination == "/etc/dockerpilot-secure-broker/config.json":
            raise SystemExit("manifest must not ship pre-baked runtime config.json")
        parse_artifact_mode(artifact.get("mode"), destination=destination)
        source = artifact_source_path(staging, artifact)
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"missing/invalid staging artifact {source}")
        expected_sha = artifact.get("sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise SystemExit(f"invalid sha256 for {destination}")
        actual_sha = sha256_file(source)
        if actual_sha != expected_sha:
            raise SystemExit(f"sha256 mismatch for {destination}")


def build_dozeyguard() -> Path:
    dozeyguard_src = resolve_dozeyguard_src()
    if not dozeyguard_src.is_dir():
        raise SystemExit(f"dozeyguard source missing: {dozeyguard_src}")
    subprocess.run(["cargo", "fmt", "--check"], cwd=dozeyguard_src, check=True)
    subprocess.run(
        ["cargo", "clippy", "--all-targets", "--", "-D", "warnings"],
        cwd=dozeyguard_src,
        check=True,
    )
    subprocess.run(["cargo", "test", "-q"], cwd=dozeyguard_src, check=True)
    subprocess.run(["cargo", "build", "--release", "-q"], cwd=dozeyguard_src, check=True)
    binary = dozeyguard_src / "target" / "release" / "dozeyguard"
    if not binary.is_file() or binary.is_symlink():
        raise SystemExit("release dozeyguard missing or symlink")
    # Quick fixture smoke (no docker/network).
    fixture = ROOT / "tests" / "fixtures" / "secure_deploy" / "cross" / "normalized_compose.json"
    policy = POLICY_SRC
    proc = subprocess.run(
        [
            str(binary),
            "scan",
            "--input",
            str(fixture),
            "--input-format",
            "compose-json",
            "--policy",
            str(policy),
            "--output",
            "json",
            "--fail-on",
            "high",
            "--max-input-bytes",
            "2097152",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode not in (0, 2):
        raise SystemExit(f"dozeyguard smoke failed: rc={proc.returncode} err={proc.stderr[:200]}")
    return binary, dozeyguard_src


def clear_staging(path: Path) -> None:
    """Remove prior staging contents. Fail closed on PermissionError / leftovers.

    Probes removelability of the whole tree before deleting anything, so a
    refusal cannot leave a half-wiped staging directory.
    """
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return
    if path.is_symlink() or not path.is_dir():
        raise SystemExit(f"staging path must be a directory: {path}")

    def _assert_tree_removable(node: Path) -> None:
        if not os.access(node.parent, os.W_OK | os.X_OK):
            raise SystemExit(
                f"staging cleanup refused: parent not writable for {node}; "
                "aborting build — refuse partial staging"
            )
        if node.is_symlink() or node.is_file():
            return
        if not node.is_dir():
            raise SystemExit(f"refusing non-file staging entry: {node}")
        if not os.access(node, os.W_OK | os.X_OK):
            raise SystemExit(
                f"staging cleanup refused: directory not writable {node}; "
                "aborting build — refuse partial staging"
            )
        for child in node.iterdir():
            _assert_tree_removable(child)

    children = list(path.iterdir())
    for child in children:
        try:
            _assert_tree_removable(child)
        except SystemExit:
            raise
        except OSError as exc:
            raise SystemExit(
                f"staging cleanup probe failed for {child}: {exc}; aborting build"
            ) from exc

    for child in children:
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                raise SystemExit(f"refusing to leave non-file staging entry: {child}")
        except PermissionError as exc:
            raise SystemExit(
                f"staging cleanup failed (PermissionError) for {child}: {exc}; "
                "aborting build — refuse partial staging"
            ) from exc
        except OSError as exc:
            raise SystemExit(
                f"staging cleanup failed for {child}: {exc}; aborting build"
            ) from exc
    remaining = list(path.iterdir())
    if remaining:
        raise SystemExit(
            "staging not empty after cleanup — aborting build: "
            + ", ".join(str(p.name) for p in remaining)
        )
    path.mkdir(parents=True, exist_ok=True)


def assert_staging_ready(path: Path) -> None:
    """Staging must exist and be empty before generating a new bundle."""
    if not path.is_dir():
        raise SystemExit(f"staging missing after cleanup: {path}")
    leftover = list(path.iterdir())
    if leftover:
        raise SystemExit(
            "staging not empty before generate — aborting build: "
            + ", ".join(str(p.name) for p in leftover)
        )


def main() -> int:
    clear_staging(STAGING)
    assert_staging_ready(STAGING)

    binary, dozeyguard_src = build_dozeyguard()
    try:
        version = subprocess.check_output(
            ["git", "-C", str(dozeyguard_src), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        version = "local-" + sha256_file(binary)[:12]

    libexec = BUNDLE / "usr" / "libexec" / "dockerpilot-secure-broker"
    etc = BUNDLE / "etc" / "dockerpilot-secure-broker"
    var = BUNDLE / "var" / "lib" / "dockerpilot-secure-broker"
    (libexec / "bin").mkdir(parents=True)
    (libexec / "python" / "dockerpilot").mkdir(parents=True)
    (libexec / "python" / "secure_deploy_pure").mkdir(parents=True)
    (libexec / "schemas").mkdir(parents=True)
    etc.mkdir(parents=True)
    (var / "state").mkdir(parents=True)
    (var / "approvals").mkdir(parents=True)
    (var / "runs").mkdir(parents=True)

    artifacts: list[dict] = []

    # Entrypoint wrapper — uses installed tree only.
    entry = libexec / "broker"
    write_text(
        entry,
        """#!/bin/sh
set -eu
export PYTHONNOUSERSITE=1
export DOCKERPILOT_BROKER_STRICT_ENV=1
unset PYTHONPATH PYTHONHOME || true
export PATH=/usr/bin:/bin
exec /usr/bin/python3 -I \\
  /usr/libexec/dockerpilot-secure-broker/python/run_broker.py \\
  \"$@\"
""",
        0o755,
    )
    artifacts.append(
        {
            "source": "generated:broker",
            "destination": f"{DEST_PREFIX['libexec']}/broker",
            "staging_path": str(entry.relative_to(STAGING)),
            "sha256": sha256_file(entry),
            "owner": "root",
            "group": "root",
            "mode": "0o755",
            "type": "file",
            "required": True,
            "rollback_action": "restore_or_remove",
        }
    )

    # Copy broker package (no Flask).
    pkg_src = ROOT / "src" / "dockerpilot" / "secure_deploy_broker"
    pkg_dst = libexec / "python" / "dockerpilot" / "secure_deploy_broker"
    shutil.copytree(
        pkg_src,
        pkg_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Vendor pure helpers into package to avoid /home imports at runtime.
    pure_dst = libexec / "python" / "secure_deploy_pure"
    pure_dst.mkdir(parents=True, exist_ok=True)
    (pure_dst / "__init__.py").write_text("", encoding="utf-8")
    for mod in PURE_MODULES:
        # Copy first, then flatten relative imports BEFORE hashing into the
        # install manifest (content must match what gets installed).
        dst = pure_dst / mod.name
        shutil.copy2(mod, dst)
        os.chmod(dst, 0o644)
        if mod.name in ("normalizer.py", "firewall_planner.py"):
            text = dst.read_text(encoding="utf-8")
            text = text.replace("from .errors import", "from errors import")
            dst.write_text(text, encoding="utf-8")
        item = {
            "source": str(mod.relative_to(ROOT)),
            "destination": f"{DEST_PREFIX['libexec']}/python/secure_deploy_pure/{mod.name}",
            "staging_path": str(dst.relative_to(STAGING)),
            "sha256": sha256_file(dst),
            "owner": "root",
            "group": "root",
            "mode": "0o644",
            "type": "file",
            "required": True,
            "rollback_action": "restore_or_remove",
        }
        artifacts.append(item)

    # Patch bundle_helpers in the staged copy to import vendored pure modules.
    helpers = pkg_dst / "bundle_helpers.py"
    helpers.write_text(
        '''"""Pure helpers for installed broker (vendored; no /home imports)."""
from __future__ import annotations
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2] / "secure_deploy_pure"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from normalizer import normalize_spec_to_compose  # noqa: E402
from firewall_planner import plan_firewall_actions  # noqa: E402
__all__ = ["normalize_spec_to_compose", "plan_firewall_actions"]
''',
        encoding="utf-8",
    )

    # Also need dockerpilot.secure_deploy canonical/schemas for verifier.
    sd_src = ROOT / "src" / "dockerpilot" / "secure_deploy"
    sd_dst = libexec / "python" / "dockerpilot" / "secure_deploy"
    shutil.copytree(sd_src, sd_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (libexec / "python" / "dockerpilot" / "__init__.py").write_text("", encoding="utf-8")

    run_broker = libexec / "python" / "run_broker.py"
    write_text(
        run_broker,
        """#!/usr/bin/env python3
import os, sys
os.environ['PYTHONNOUSERSITE']='1'
ROOT = '/usr/libexec/dockerpilot-secure-broker/python'
sys.path.insert(0, ROOT)
from dockerpilot.secure_deploy_broker.__main__ import main
raise SystemExit(main())
""",
        0o755,
    )

    # Schemas
    for name in [
        "secure-deploy-broker-request-v1.schema.json",
        "secure-deploy-broker-response-v1.schema.json",
        "secure-deploy-approval-v1.schema.json",
        "secure-deploy-canary-admission-bundle-v1.schema.json",
        "deployment-plan-v1.schema.json",
        "secure-deployment-spec-v1.schema.json",
    ]:
        src = ROOT / "schemas" / name
        item = copy_file(src, libexec / "schemas" / name, 0o644)
        item["destination"] = f"{DEST_PREFIX['libexec']}/schemas/{name}"
        item["owner"] = "root"
        item["group"] = "root"
        artifacts.append(item)

    # Dozeyguard binary + policy
    bin_item = copy_file(binary, libexec / "bin" / "dozeyguard", 0o755)
    bin_item["destination"] = f"{DEST_PREFIX['libexec']}/bin/dozeyguard"
    bin_item["owner"] = "root"
    bin_item["group"] = "root"
    artifacts.append(bin_item)

    pol_item = copy_file(POLICY_SRC, etc / "policy.toml", 0o644)
    pol_item["destination"] = f"{DEST_PREFIX['etc']}/policy.toml"
    pol_item["owner"] = "root"
    pol_item["group"] = "root"
    artifacts.append(pol_item)
    policy_sha = pol_item["sha256"]
    write_text(etc / "policy.sha256", policy_sha + "\n", 0o644)
    artifacts.append(
        {
            "source": "generated:policy.sha256",
            "destination": f"{DEST_PREFIX['etc']}/policy.sha256",
            "staging_path": str((etc / "policy.sha256").relative_to(STAGING)),
            "sha256": sha256_file(etc / "policy.sha256"),
            "owner": "root",
            "group": "root",
            "mode": "0o644",
            "type": "file",
            "required": True,
            "rollback_action": "restore_or_remove",
        }
    )

    binary_sha = bin_item["sha256"]
    # Install-time template: name-based peer identity. Installer resolves UID via
    # getpwnam and writes root-owned config.json with numeric expected_peer_uid.
    # Runtime BrokerConfig rejects expected_peer_user and requires the integer.
    allowed_operations = [
        "ping",
        "capabilities",
        "verify_plan",
        "dry_run",
        "admit_canary_execution",
        "revoke_canary_admission",
        "deploy_canary",
        "remove_canary",
    ]
    config_template = {
        "protocol_version": 1,
        "socket_activation": True,
        "socket_path": f"{DEST_PREFIX['run']}/broker.sock",
        "max_frame_bytes": 2097152,
        "request_timeout_seconds": 150,
        "expected_peer_user": "dockerpilot-extras",
        "dozeyguard_path": f"{DEST_PREFIX['libexec']}/bin/dozeyguard",
        "policy_path": f"{DEST_PREFIX['etc']}/policy.toml",
        "expected_binary_sha256": binary_sha,
        "expected_policy_sha256": policy_sha,
        "schemas_root": f"{DEST_PREFIX['libexec']}/schemas",
        "state_root": f"{DEST_PREFIX['var']}",
        "allowed_operations": allowed_operations,
        "canary_workdir": f"{DEST_PREFIX['var']}/canary/dockerpilot-secure-canary",
        "canary_image": "REPLACE_WITH_DIGEST_ONLY_CANARY_IMAGE",
        "canary_health_timeout_seconds": 60,
        "canary_staged_bundle_ttl_seconds": 300,
        "canary_live_mode": True,
    }
    write_text(
        etc / "config.template.json",
        json.dumps(config_template, indent=2, sort_keys=True) + "\n",
        0o644,
    )
    # Staging-local config for offline tests (concrete UID of the test runner).
    staging_config = {
        k: v for k, v in config_template.items() if k != "expected_peer_user"
    }
    staging_config["socket_activation"] = False
    staging_config["socket_path"] = str(STAGING / "run" / "broker.sock")
    staging_config["dozeyguard_path"] = str(libexec / "bin" / "dozeyguard")
    staging_config["policy_path"] = str(etc / "policy.toml")
    staging_config["schemas_root"] = str(libexec / "schemas")
    staging_config["state_root"] = str(var)
    staging_config["expected_peer_uid"] = os.getuid()
    staging_config["canary_image"] = (
        "docker.io/library/nginx@"
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    write_text(
        STAGING / "config.staging.json",
        json.dumps(staging_config, indent=2, sort_keys=True) + "\n",
        0o644,
    )
    # Do NOT stage a runtime config.json with null UID — installer materializes it.

    write_text(libexec / "VERSION", f"dozeyguard={version}\nbundle=11d2a\n", 0o644)

    # Systemd units into staging for install script.
    for unit in ("dockerpilot-secure-broker.service", "dockerpilot-secure-broker.socket"):
        src = ROOT / "deploy" / "systemd" / unit
        dst = BUNDLE / "etc" / "systemd" / "system" / unit
        item = copy_file(src, dst, 0o644)
        item["destination"] = f"/etc/systemd/system/{unit}"
        item["owner"] = "root"
        item["group"] = "root"
        artifacts.append(item)

    # Register EVERY regular file under libexec + etc that must be installed.
    # Earlier steps only listed a subset; omitting the Python package would leave
    # ExecStart unable to import the broker after sudo install.
    def _register_or_verify(path: Path, destination: str, mode: int) -> None:
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"refusing non-regular artifact: {path}")
        os.chmod(path, mode)
        digest = sha256_file(path)
        for art in artifacts:
            if art["destination"] == destination:
                if art["sha256"] != digest:
                    raise SystemExit(
                        f"artifact hash drift for {destination}: "
                        f"manifest={art['sha256']} file={digest}"
                    )
                return
        artifacts.append(
            {
                "source": "bundle-tree",
                "destination": destination,
                "staging_path": str(path.relative_to(STAGING)),
                "sha256": digest,
                "owner": "root",
                "group": "root",
                "mode": oct(mode),
                "type": "file",
                "required": True,
                "rollback_action": "restore_or_remove",
            }
        )

    for path in sorted(libexec.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(libexec).as_posix()
        if path.name in {"broker", "run_broker.py"} or (
            path.name == "dozeyguard" and path.parent.name == "bin"
        ):
            mode = 0o755
        else:
            mode = 0o644
        _register_or_verify(path, f"{DEST_PREFIX['libexec']}/{rel}", mode)

    for path in sorted(etc.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(etc).as_posix()
        _register_or_verify(path, f"{DEST_PREFIX['etc']}/{rel}", 0o644)

    for unit in ("dockerpilot-secure-broker.service", "dockerpilot-secure-broker.socket"):
        path = BUNDLE / "etc" / "systemd" / "system" / unit
        _register_or_verify(path, f"/etc/systemd/system/{unit}", 0o644)

    # Scan staged tree for forbidden references / symlinks.
    for path in BUNDLE.rglob("*"):
        if path.is_symlink():
            raise SystemExit(f"symlink in bundle: {path}")
        if path.is_file() and path.suffix in {".py", ".json", ".toml", ".service", ".socket", ".sh", ""}:
            # Skip binary ELF
            if path.name == "dozeyguard":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "/home/dozey" in text:
                raise SystemExit(f"bundle references /home/dozey: {path}")

    manifest = {
        "schema_version": 1,
        "bundle": "11d2a",
        "dozeyguard_git": version,
        "dozeyguard_sha256": binary_sha,
        "policy_sha256": policy_sha,
        "allowed_operations": allowed_operations,
        "artifacts": artifacts,
        "users": {
            "extras_user": "dockerpilot-extras",
            "broker_group": "dockerpilot-secure-broker",
            "forbidden_groups": ["docker"],
        },
        "destinations": DEST_PREFIX,
    }
    # Logical manifest excludes staging_path variance for reproducibility note.
    logical = {
        "schema_version": 1,
        "allowed_operations": manifest["allowed_operations"],
        "dozeyguard_sha256": binary_sha,
        "policy_sha256": policy_sha,
        "artifacts": [
            {
                "destination": a["destination"],
                "sha256": a["sha256"],
                "mode": a["mode"],
                "owner": a.get("owner"),
                "group": a.get("group"),
                "type": a["type"],
            }
            for a in artifacts
            if a.get("destination")
        ],
    }
    logical_bytes = json.dumps(logical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["logical_manifest_sha256"] = hashlib.sha256(logical_bytes).hexdigest()
    manifest_path = STAGING / "install-manifest.json"
    write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", 0o644)
    manifest_hash = sha256_file(manifest_path)
    write_text(STAGING / "install-manifest.sha256", manifest_hash + "\n", 0o644)
    # Logical hash sidecar stays in staging (not under installed libexec) so the
    # install manifest can be closed before writing it (avoids chicken/egg).
    write_text(STAGING / "MANIFEST.logical.sha256", manifest["logical_manifest_sha256"] + "\n", 0o644)
    verify_manifest_artifacts(STAGING, manifest)

    # Ship the systemd preflight helper next to the install script (not an
    # installed host artifact — outside bundle/, not in install-manifest).
    verify_helper_src = Path(__file__).resolve().parent / "verify_staged_systemd_units.py"
    shutil.copy2(verify_helper_src, STAGING / "verify_staged_systemd_units.py")
    os.chmod(STAGING / "verify_staged_systemd_units.py", 0o755)
    install_helpers_src = Path(__file__).resolve().parent / "broker_install_helpers.py"
    shutil.copy2(install_helpers_src, STAGING / "broker_install_helpers.py")
    os.chmod(STAGING / "broker_install_helpers.py", 0o644)

    # Generate install/rollback scripts (not executed here).
    _write_install_scripts(manifest_hash, binary_sha, policy_sha)

    size = sum(p.stat().st_size for p in BUNDLE.rglob("*") if p.is_file())
    print(json.dumps({
        "staging": str(STAGING),
        "size_bytes": size,
        "manifest_sha256": manifest_hash,
        "logical_manifest_sha256": manifest["logical_manifest_sha256"],
        "dozeyguard_sha256": binary_sha,
        "policy_sha256": policy_sha,
    }, indent=2))
    return 0


def _write_install_scripts(manifest_hash: str, binary_sha: str, policy_sha: str) -> None:
    install = STAGING / "INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh"
    rollback = STAGING / "ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh"
    backup_dir = BACKUP_DIR_CANARY

    install_tpl = r'''#!/usr/bin/env bash
# REQUIRES SUDO — review before running. Not executed by #11D.2A/#11D.2B-pre automation.
set -euo pipefail
umask 077
STAGING="$(cd "$(dirname "$0")" && pwd)"
export STAGING_OVERRIDE="$STAGING"
MANIFEST="$STAGING/install-manifest.json"
EXPECTED_MANIFEST_SHA="__MANIFEST_SHA__"
BACKUP_DIR="__BACKUP_DIR__"
export BACKUP_DIR
export EXPECTED_MANIFEST_SHA
ROLLBACK_SCRIPT="$STAGING/ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh"
MUTATING=0

die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "missing $1"; }

on_err() {
  local rc=$?
  if [[ "$MUTATING" -eq 1 ]]; then
    echo "INSTALL FAILED (rc=$rc) — running rollback" >&2
    MUTATING=0
    /bin/bash "$ROLLBACK_SCRIPT" || echo "ROLLBACK also failed" >&2
  fi
  exit "$rc"
}
trap on_err ERR

[[ "$(id -un)" == "root" ]] || die "must run as root via sudo"
need install; need sha256sum; need systemctl; need systemd-analyze; need python3
[[ -f "$STAGING/broker_install_helpers.py" ]] || die "missing broker_install_helpers.py"

# Resolve non-root operator: DOCKERPILOT_INSTALL_EXPECT_USER → SUDO_USER (never root).
HOST_USER_EXPECTED="$(python3 - <<'PY'
import importlib.util, os, sys
from pathlib import Path
helpers = Path(os.environ["STAGING_OVERRIDE"]) / "broker_install_helpers.py"
spec = importlib.util.spec_from_file_location("broker_install_helpers", helpers)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
try:
    print(mod.resolve_install_expect_user())
except ValueError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)" || die "cannot resolve install operator"
[[ "$HOST_USER_EXPECTED" != "root" ]] || die "install operator must not be root"
[[ "${SUDO_USER:-}" == "$HOST_USER_EXPECTED" || "$(logname 2>/dev/null || true)" == "$HOST_USER_EXPECTED" ]] \
  || die "unexpected invoking user (want $HOST_USER_EXPECTED)"

[[ -f "$MANIFEST" ]] || die "manifest missing"
[[ -x "$ROLLBACK_SCRIPT" ]] || die "rollback script missing/not executable"
echo "$EXPECTED_MANIFEST_SHA  $MANIFEST" | sha256sum -c -
echo "__BINARY_SHA__  $STAGING/bundle/usr/libexec/dockerpilot-secure-broker/bin/dozeyguard" | sha256sum -c -
echo "__POLICY_SHA__  $STAGING/bundle/etc/dockerpilot-secure-broker/policy.toml" | sha256sum -c -

# Fresh-host preflight: verify units with ExecStart temporarily rewritten to the
# staged broker binary. Does not mutate staged/installed unit files.
# Host noise from unrelated units (telegraf/prometheus) may appear on stderr and
# is ignored unless systemd-analyze returns non-zero.
VERIFY_HELPER="$STAGING/verify_staged_systemd_units.py"
[[ -f "$VERIFY_HELPER" ]] || die "missing verify_staged_systemd_units.py"
python3 "$VERIFY_HELPER" "$STAGING" || die "systemd-analyze verify failed"

# Backup dir must match this manifest hash (or be empty/absent) before mutation.
python3 - <<'PY'
import importlib.util, os
from pathlib import Path
helpers_path = Path(os.environ["STAGING_OVERRIDE"]) / "broker_install_helpers.py"
spec = importlib.util.spec_from_file_location("broker_install_helpers", helpers_path)
helpers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helpers)
helpers.assert_backup_dir_compatible(
    Path(os.environ["BACKUP_DIR"]),
    os.environ["EXPECTED_MANIFEST_SHA"],
)
print("backup dir compatible with manifest")
PY

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path

ALLOWED_EXACT = {
    "/etc/systemd/system/dockerpilot-secure-broker.service",
    "/etc/systemd/system/dockerpilot-secure-broker.socket",
}
ALLOWED_PREFIXES = (
    "/usr/libexec/dockerpilot-secure-broker/",
    "/etc/dockerpilot-secure-broker/",
    "/var/lib/dockerpilot-secure-broker/",
    "/run/dockerpilot-secure-broker/",
)

def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def assert_allowed_dest(dest):
    text = str(dest)
    if not dest.is_absolute() or ".." in dest.parts:
        raise SystemExit(f"path traversal rejected: {text}")
    if text in ALLOWED_EXACT:
        return
    if not any(text.startswith(p) for p in ALLOWED_PREFIXES):
        raise SystemExit(f"destination outside allowlist: {text}")

def artifact_mode(art):
    dest = art.get("destination")
    mode = art.get("mode")
    try:
        parsed = int(mode, 8) if isinstance(mode, str) else int(mode)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid artifact mode for {dest}: {mode}") from exc
    if parsed < 0 or parsed > 0o777:
        raise SystemExit(f"artifact mode out of range for {dest}: {oct(parsed)}")
    if parsed & 0o022:
        raise SystemExit(f"refusing group/other-writable mode for {dest}")
    return parsed

staging = Path(os.environ["STAGING_OVERRIDE"])
manifest = json.loads((staging / "install-manifest.json").read_text())
for art in manifest.get("artifacts") or []:
    if art.get("type") != "file":
        raise SystemExit(f"unsupported artifact type for {art.get('destination')}")
    dest = Path(str(art.get("destination") or ""))
    assert_allowed_dest(dest)
    if dest == Path("/etc/dockerpilot-secure-broker/config.json"):
        raise SystemExit("manifest must not ship pre-baked runtime config.json")
    artifact_mode(art)
    staging_path = Path(str(art.get("staging_path") or ""))
    if staging_path.is_absolute() or ".." in staging_path.parts:
        raise SystemExit(f"invalid staging_path for {dest}: {staging_path}")
    src = staging / staging_path
    if src.is_symlink() or not src.is_file():
        raise SystemExit(f"missing/invalid staging artifact {src}")
    expected_sha = art.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise SystemExit(f"invalid sha256 for {dest}")
    if sha256_file(src) != expected_sha:
        raise SystemExit(f"sha256 mismatch for {dest}")
print("all manifest artifacts verified before mutation")
PY

# Validate generated runtime config inputs before any host mutation. Use a
# non-root placeholder UID here; the exact dockerpilot-extras UID is resolved
# again during the mutating phase after the account is guaranteed to exist.
python3 - <<'PY'
import importlib.util, json, os
from pathlib import Path
staging = Path(os.environ["STAGING_OVERRIDE"])
helpers_path = staging / "broker_install_helpers.py"
spec = importlib.util.spec_from_file_location("broker_install_helpers", helpers_path)
helpers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helpers)
template = json.loads((staging / "bundle/etc/dockerpilot-secure-broker/config.template.json").read_text(encoding="utf-8"))
helpers.materialize_runtime_config(template, peer_uid=1)
print("runtime config template validated before mutation")
PY

mkdir -p "$BACKUP_DIR"
MUTATING=1
python3 - <<'PY'
import json, os, shutil, subprocess, sys
from pathlib import Path

ALLOWED_EXACT = {
    "/etc/systemd/system/dockerpilot-secure-broker.service",
    "/etc/systemd/system/dockerpilot-secure-broker.socket",
}
ALLOWED_PREFIXES = (
    "/usr/libexec/dockerpilot-secure-broker/",
    "/etc/dockerpilot-secure-broker/",
    "/var/lib/dockerpilot-secure-broker/",
    "/run/dockerpilot-secure-broker/",
)

def assert_allowed_dest(dest: Path) -> None:
    text = str(dest)
    if ".." in dest.parts:
        raise SystemExit(f"path traversal rejected: {dest}")
    if text in ALLOWED_EXACT:
        return
    if not any(text.startswith(p) for p in ALLOWED_PREFIXES):
        raise SystemExit(f"destination outside allowlist: {dest}")

staging = Path(os.environ["STAGING_OVERRIDE"])
manifest = json.loads((staging / "install-manifest.json").read_text())
backup = Path(os.environ["BACKUP_DIR"])

def ensure_group(name):
    r = subprocess.run(["getent", "group", name], capture_output=True)
    if r.returncode != 0:
        subprocess.check_call(["groupadd", "--system", name])
        (backup / f"created_group_{name}").write_text("1\n")

def ensure_user(name, home):
    r = subprocess.run(["getent", "passwd", name], capture_output=True)
    if r.returncode != 0:
        subprocess.check_call([
            "useradd", "--system", "--shell", "/usr/sbin/nologin",
            "--home-dir", home, "--create-home", name,
        ])
        (backup / f"created_user_{name}").write_text("1\n")

ensure_group("dockerpilot-secure-broker")
ensure_user("dockerpilot-extras", "/var/lib/dockerpilot-extras")
# Only manage dockerpilot-extras membership in the broker socket group.
groups = subprocess.check_output(["id", "-nG", "dockerpilot-extras"], text=True).split()
if "docker" in groups:
    raise SystemExit("dockerpilot-extras must not be in docker group")
if "dockerpilot-secure-broker" not in groups:
    subprocess.check_call(["usermod", "-a", "-G", "dockerpilot-secure-broker", "dockerpilot-extras"])

import pwd, grp, importlib.util, tempfile
helpers_path = staging / "broker_install_helpers.py"
spec = importlib.util.spec_from_file_location("broker_install_helpers", helpers_path)
helpers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helpers)
helpers.write_backup_manifest_marker(backup, os.environ["EXPECTED_MANIFEST_SHA"])

extras_uid = pwd.getpwnam("dockerpilot-extras").pw_uid
broker_gid = grp.getgrnam("dockerpilot-secure-broker").gr_gid
if extras_uid == 0:
    raise SystemExit("dockerpilot-extras must not be UID 0")

for art in manifest["artifacts"]:
    dest = Path(art["destination"])
    assert_allowed_dest(dest)
    src = staging / art["staging_path"]
    if not src.is_file() or src.is_symlink():
        raise SystemExit(f"missing/invalid staging artifact {src}")
    # Runtime config.json is materialized below from the template + resolved UID.
    if dest == Path("/etc/dockerpilot-secure-broker/config.json"):
        raise SystemExit("manifest must not ship pre-baked runtime config.json")
    if dest.exists() or dest.is_symlink():
        bak = backup / dest.as_posix().lstrip("/")
        bak.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise SystemExit(f"refusing to overwrite symlink dest {dest}")
        shutil.copy2(dest, bak)
        (backup / (dest.as_posix().lstrip("/") + ".existed")).write_text("1\n")
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = int(art["mode"], 8) if isinstance(art["mode"], str) else art["mode"]
    if mode & 0o022:
        raise SystemExit(f"refusing group/other-writable mode for {dest}")
    subprocess.check_call([
        "install", "-o", art.get("owner") or "root", "-g", art.get("group") or "root",
        "-m", oct(mode)[2:], str(src), str(dest),
    ])

# Materialize root-owned runtime config with numeric expected_peer_uid (no hardcoding).
template_path = staging / "bundle/etc/dockerpilot-secure-broker/config.template.json"
template = json.loads(template_path.read_text(encoding="utf-8"))
final_cfg = helpers.materialize_runtime_config(template, peer_uid=extras_uid)
cfg_dest = Path("/etc/dockerpilot-secure-broker/config.json")
assert_allowed_dest(cfg_dest)
if cfg_dest.exists() or cfg_dest.is_symlink():
    bak = backup / cfg_dest.as_posix().lstrip("/")
    bak.parent.mkdir(parents=True, exist_ok=True)
    if cfg_dest.is_symlink():
        raise SystemExit("refusing to overwrite symlink config.json")
    shutil.copy2(cfg_dest, bak)
    (backup / (cfg_dest.as_posix().lstrip("/") + ".existed")).write_text("1\n")
else:
    (backup / "generated_config_json").write_text("1\n")
cfg_dest.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(cfg_dest.parent)) as tmp:
    tmp.write(json.dumps(final_cfg, indent=2, sort_keys=True) + "\n")
    tmp_path = tmp.name
try:
    subprocess.check_call(["install", "-o", "root", "-g", "root", "-m", "644", tmp_path, str(cfg_dest)])
finally:
    Path(tmp_path).unlink(missing_ok=True)
print(f"runtime config expected_peer_uid={extras_uid} (user dockerpilot-extras)")

# State dir only — do NOT pre-create /run/... under umask 077 (that yielded 0700).
Path("/var/lib/dockerpilot-secure-broker/state").mkdir(parents=True, exist_ok=True)
os.chmod("/var/lib/dockerpilot-secure-broker", 0o750)
# If a previous broken run left /run/dockerpilot-secure-broker as 0700, repair now.
helpers.ensure_runtime_dir(Path("/run/dockerpilot-secure-broker"), broker_gid=broker_gid, fix=True)
print("files installed from manifest + materialized config")
PY

systemctl daemon-reload
systemctl start dockerpilot-secure-broker.socket
systemctl is-active dockerpilot-secure-broker.socket
# Post-start: runtime dir + socket ownership/mode must match unit (0750 / 0660).
python3 - <<'PY'
import grp, importlib.util
from pathlib import Path
import os
staging = Path(os.environ["STAGING_OVERRIDE"])
spec = importlib.util.spec_from_file_location(
    "broker_install_helpers", staging / "broker_install_helpers.py"
)
helpers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helpers)
gid = grp.getgrnam("dockerpilot-secure-broker").gr_gid
helpers.ensure_runtime_dir(Path("/run/dockerpilot-secure-broker"), broker_gid=gid, fix=True)
helpers.assert_socket_mode(Path("/run/dockerpilot-secure-broker/broker.sock"), broker_gid=gid)
print("runtime dir + socket mode OK")
PY
systemctl show dockerpilot-secure-broker.service -p CapabilityBoundingSet -p AmbientCapabilities
# No systemctl enable in first canary.
MUTATING=0
trap - ERR
echo "INSTALL OK — enable skipped for first canary"
'''
    install.write_text(
        install_tpl.replace("__MANIFEST_SHA__", manifest_hash)
        .replace("__BINARY_SHA__", binary_sha)
        .replace("__POLICY_SHA__", policy_sha)
        .replace("__BACKUP_DIR__", backup_dir),
        encoding="utf-8",
    )
    os.chmod(install, 0o700)

    rollback_tpl = r'''#!/usr/bin/env bash
# REQUIRES SUDO — rollback only files from #11D.2A manifest/backups.
# Idempotent. Does not delete users/groups that existed before this install.
set -euo pipefail
umask 077
STAGING="$(cd "$(dirname "$0")" && pwd)"
export STAGING_OVERRIDE="$STAGING"
BACKUP_DIR="__BACKUP_DIR__"
export BACKUP_DIR
EXPECTED_MANIFEST_SHA="__MANIFEST_SHA__"
export EXPECTED_MANIFEST_SHA
[[ "$(id -un)" == "root" ]] || { echo "must be root" >&2; exit 1; }
systemctl stop dockerpilot-secure-broker.service 2>/dev/null || true
systemctl stop dockerpilot-secure-broker.socket 2>/dev/null || true
python3 - <<'PY'
import importlib.util, json, os, shutil, subprocess
from pathlib import Path

ALLOWED_EXACT = {
    "/etc/systemd/system/dockerpilot-secure-broker.service",
    "/etc/systemd/system/dockerpilot-secure-broker.socket",
}
ALLOWED_PREFIXES = (
    "/usr/libexec/dockerpilot-secure-broker/",
    "/etc/dockerpilot-secure-broker/",
    "/var/lib/dockerpilot-secure-broker/",
    "/run/dockerpilot-secure-broker/",
)

def assert_allowed_dest(dest: Path) -> None:
    text = str(dest)
    if ".." in dest.parts:
        raise SystemExit(f"path traversal rejected: {dest}")
    if text in ALLOWED_EXACT:
        return
    if not any(text.startswith(p) for p in ALLOWED_PREFIXES):
        raise SystemExit(f"destination outside allowlist: {dest}")

staging = Path(os.environ["STAGING_OVERRIDE"])
manifest = json.loads((staging / "install-manifest.json").read_text())
backup = Path(os.environ["BACKUP_DIR"])
helpers_path = staging / "broker_install_helpers.py"
spec = importlib.util.spec_from_file_location("broker_install_helpers", helpers_path)
helpers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helpers)
# Refuse rollback against a backup bound to a different install.
helpers.assert_backup_dir_compatible(backup, os.environ["EXPECTED_MANIFEST_SHA"])
for art in manifest["artifacts"]:
    dest = Path(art["destination"])
    assert_allowed_dest(dest)
    bak = backup / dest.as_posix().lstrip("/")
    existed = Path(str(bak) + ".existed")
    if bak.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bak, dest)
    elif dest.is_file() and not dest.is_symlink():
        # Only remove files this install created (no pre-existing backup marker).
        if not existed.exists():
            dest.unlink()
# Generated runtime config.json (not a static manifest artifact).
cfg = Path("/etc/dockerpilot-secure-broker/config.json")
assert_allowed_dest(cfg)
cfg_bak = backup / cfg.as_posix().lstrip("/")
if cfg_bak.is_file():
    cfg.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_bak, cfg)
elif (backup / "generated_config_json").exists() and cfg.is_file() and not cfg.is_symlink():
    cfg.unlink()
subprocess.run(["systemctl", "daemon-reload"], check=False)
# Remove user/group ONLY if created by this install script.
if (backup / "created_user_dockerpilot-extras").exists():
    subprocess.run(["userdel", "dockerpilot-extras"], check=False)
if (backup / "created_group_dockerpilot-secure-broker").exists():
    subprocess.run(["groupdel", "dockerpilot-secure-broker"], check=False)
print("ROLLBACK complete")
PY
'''
    rollback.write_text(
        rollback_tpl.replace("__BACKUP_DIR__", backup_dir).replace("__MANIFEST_SHA__", manifest_hash),
        encoding="utf-8",
    )
    os.chmod(rollback, 0o700)


if __name__ == "__main__":
    raise SystemExit(main())
