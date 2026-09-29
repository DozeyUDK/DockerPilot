"""Broker-owned Dozeyguard subprocess runner (fixed argv, shell=False)."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dockerpilot.secure_deploy.canonical import sha256_canonical, sha256_hex

from .errors import VerificationError
from .integrity import open_trusted_artifact
from .verifier import BrokerDozeyguardConfig

_OUTPUT_LIMIT = 1024 * 1024
_CONTROLLED_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class DozeyguardArtifactTrust:
    """Broker-owned integrity expectations for DozeyGuard execution."""

    binary_sha256: str
    policy_sha256: str
    expected_uid: Optional[int] = None
    expected_gid: Optional[int] = None
    deny_writable_uid: Optional[int] = None


def run_broker_dozeyguard(
    compose_obj: Dict[str, Any],
    config: BrokerDozeyguardConfig,
    *,
    artifact_trust: Optional[DozeyguardArtifactTrust] = None,
) -> Dict[str, Any]:
    payload = json.dumps(compose_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return run_broker_dozeyguard_bytes(payload, config, artifact_trust=artifact_trust)


def run_broker_dozeyguard_bytes(
    payload: bytes,
    config: BrokerDozeyguardConfig,
    *,
    artifact_trust: Optional[DozeyguardArtifactTrust] = None,
) -> Dict[str, Any]:
    """Run broker-owned DozeyGuard against payload.

    Resolved broker configs carry expected hashes for both artifacts. When those
    hashes are present (the production/default path), the executable and policy
    are opened, verified and kept pinned by file descriptor for the whole
    subprocess run. The child receives ``/proc/self/fd/*`` paths plus inherited
    descriptors, so replacing either original pathname after verification cannot
    change the bytes consumed by the privileged broker execution.
    """

    if artifact_trust is None and config.expected_binary_sha256 and config.expected_policy_sha256:
        artifact_trust = DozeyguardArtifactTrust(
            binary_sha256=config.expected_binary_sha256,
            policy_sha256=config.expected_policy_sha256,
        )

    if artifact_trust is None:
        # Compatibility path for hand-constructed unit-test configs. Production
        # configs are created by resolve_broker_dozeyguard_config() and therefore
        # always carry both expected hashes.
        return _run_broker_dozeyguard_bytes(
            payload,
            executable=config.executable,
            policy_path=config.policy_path,
            pass_fds=(),
        )

    with open_trusted_artifact(
        Path(config.executable),
        expected_sha256=artifact_trust.binary_sha256,
        expected_uid=artifact_trust.expected_uid,
        expected_gid=artifact_trust.expected_gid,
        require_executable=True,
        deny_writable_uid=artifact_trust.deny_writable_uid,
    ) as executable, open_trusted_artifact(
        Path(config.policy_path),
        expected_sha256=artifact_trust.policy_sha256,
        expected_uid=artifact_trust.expected_uid,
        expected_gid=artifact_trust.expected_gid,
        require_executable=False,
        deny_writable_uid=artifact_trust.deny_writable_uid,
    ) as policy:
        return _run_broker_dozeyguard_bytes(
            payload,
            executable=executable.proc_path,
            policy_path=policy.proc_path,
            pass_fds=(executable.fd, policy.fd),
        )


def _run_broker_dozeyguard_bytes(
    payload: bytes,
    *,
    executable: str,
    policy_path: str,
    pass_fds: tuple[int, ...],
) -> Dict[str, Any]:
    expected_input_sha = sha256_hex(payload)
    argv = [
        executable,
        "scan",
        "--input",
        "-",
        "--input-format",
        "compose-json",
        "--policy",
        policy_path,
        "--output",
        "json",
        "--fail-on",
        "high",
        "--max-input-bytes",
        str(2 * 1024 * 1024),
    ]
    env = {"PATH": _CONTROLLED_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    try:
        popen_kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
            "env": env,
            "cwd": "/",
        }
        if pass_fds:
            popen_kwargs["pass_fds"] = pass_fds
        proc = subprocess.Popen(argv, **popen_kwargs)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_truncated = {"value": False}
        stderr_truncated = {"value": False}

        def read_limited(stream, chunks: list[bytes], truncated: dict[str, bool]) -> None:
            total = 0
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                remaining = _OUTPUT_LIMIT - total
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                total += len(chunk)
                if total > _OUTPUT_LIMIT:
                    truncated["value"] = True

        readers = [
            threading.Thread(target=read_limited, args=(proc.stdout, stdout_chunks, stdout_truncated), daemon=True),
            threading.Thread(target=read_limited, args=(proc.stderr, stderr_chunks, stderr_truncated), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(payload)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            returncode = proc.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise VerificationError("dozeyguard_timeout", "broker Dozeyguard timed out") from exc
        for reader in readers:
            reader.join(timeout=1)
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("dozeyguard_timeout", "broker Dozeyguard timed out") from exc
    except OSError as exc:
        raise VerificationError("dozeyguard_exec", f"broker Dozeyguard exec failed: {exc}") from exc

    if stdout_truncated["value"] or stderr_truncated["value"]:
        raise VerificationError("dozeyguard_output_too_large", "broker Dozeyguard output exceeded limit")

    if returncode == 1:
        raise VerificationError("dozeyguard_error", "broker Dozeyguard scanner error")

    text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerificationError("dozeyguard_invalid_json", "invalid broker Dozeyguard JSON") from exc
    if not isinstance(report, dict):
        raise VerificationError("dozeyguard_invalid_json", "broker Dozeyguard JSON must be object")
    if report.get("contract_version") != 1:
        raise VerificationError("scanner_contract", "unsupported contract_version")
    if (report.get("scanner") or {}).get("name") != "dozeyguard":
        raise VerificationError("scanner_contract", "unexpected scanner name")
    input_info = report.get("input") or {}
    if input_info.get("sha256") != expected_input_sha:
        raise VerificationError("input_hash_mismatch", "broker input hash mismatch")
    result = report.get("result") or {}
    if result.get("exit_code") != returncode:
        raise VerificationError("exit_mismatch", "exit/result mismatch")
    if returncode not in (0, 2):
        raise VerificationError("exit_mismatch", "unexpected exit code")

    # Recompute result hash the same way as the extras adapter.
    hash_payload = {
        "contract_version": report.get("contract_version"),
        "scanner": report.get("scanner"),
        "input": report.get("input"),
        "policy": report.get("policy"),
        "summary": report.get("summary"),
        "findings": report.get("findings"),
        "result": {"status": result.get("status"), "exit_code": result.get("exit_code")},
    }
    computed = sha256_canonical(hash_payload)
    if result.get("result_sha256") != computed:
        raise VerificationError("result_hash_mismatch", "broker result hash mismatch")
    return report
