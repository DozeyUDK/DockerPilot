"""Remote probe helpers used by status checks."""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from ..security import safe_error_message


def build_status_context(server_config):
    """Return normalized status context for local/remote checks."""
    if not server_config:
        return {
            "mode": "local",
            "server_id": "local",
            "server_name": "Local",
            "hostname": "localhost",
        }

    return {
        "mode": "remote",
        "server_id": server_config.get("id", "remote"),
        "server_name": server_config.get("name") or server_config.get("hostname") or "Remote server",
        "hostname": server_config.get("hostname", "unknown"),
    }


def run_remote_probe(
    server_config,
    command: str,
    execute_command_via_ssh: Callable,
    logger=None,
    attempts: int = 2,
    retry_delay: float = 0.25,
    failure_aggregator=None,
    operation: str = "status_probe",
) -> Tuple[str, Optional[str]]:
    """Execute a remote probe and report one aggregated failure per operation."""
    last_error = None
    last_exception = None

    for attempt in range(1, max(1, attempts) + 1):
        try:
            output = execute_command_via_ssh(server_config, command, check_exit_status=False)
            if failure_aggregator:
                failure_aggregator.record_success(server_config, operation)
            return (output or "").strip(), None
        except Exception as exc:
            last_error = str(exc)
            last_exception = exc
            if attempt < max(1, attempts):
                time.sleep(retry_delay)

    if failure_aggregator:
        failure_aggregator.record_failure(
            server_config,
            operation,
            last_exception or RuntimeError(last_error or "unknown remote probe error"),
        )
    elif logger:
        server_id = (
            server_config.get("id") or server_config.get("hostname") or "remote"
            if isinstance(server_config, dict)
            else "remote"
        )
        error = safe_error_message(
            last_exception or RuntimeError(last_error or "unknown remote probe error")
        )
        logger.warning(
            "Remote probe failed after %s attempt(s): %s",
            max(1, attempts),
            error,
            extra={
                "event": "remote_operation_failed",
                "server_id": str(server_id)[:128],
                "operation": operation,
                "error_type": type(last_exception).__name__ if last_exception else "RemoteError",
                "error": error,
                "occurrences": 1,
                "downtime_seconds": 0.0,
            },
        )

    return "", last_error or "unknown remote probe error"


def probe_remote_binary_version(
    server_config,
    binary_name: str,
    missing_marker: str,
    no_version_marker: str,
    run_remote_probe_fn: Callable,
) -> Tuple[str, Optional[str]]:
    """Probe binary version across POSIX and Windows-like shells."""
    mismatch_tokens = [
        "command not found",
        "is not recognized as",
        "not recognized as the name",
        "unexpected token",
        "parseexception",
        "syntax error",
    ]

    probes = [
        (
            f"{binary_name} --version 2>/dev/null || "
            f"{binary_name} version 2>/dev/null || "
            f"/usr/local/bin/{binary_name} --version 2>/dev/null || "
            f"/usr/bin/{binary_name} --version 2>/dev/null || "
            f"\"$HOME/.local/bin/{binary_name}\" --version 2>/dev/null || "
            f"(command -v {binary_name} >/dev/null 2>&1 && echo {no_version_marker}) || "
            f"echo {missing_marker}"
        ),
        (
            "powershell -NoProfile -NonInteractive -Command "
            f"\"$cmd = Get-Command {binary_name} -ErrorAction SilentlyContinue; "
            f"if ($cmd) {{ try {{ & {binary_name} --version }} catch {{ try {{ & {binary_name} version }} catch {{ '{no_version_marker}' }} }} }} "
            f"else {{ '{missing_marker}' }}\" 2>$null"
        ),
        (
            "cmd /c "
            f"\"where {binary_name} >NUL 2>&1 && "
            f"({binary_name} --version 2>NUL || {binary_name} version 2>NUL || echo {no_version_marker}) || "
            f"echo {missing_marker}\" 2>NUL"
        ),
    ]

    last_non_empty = ""
    last_error = None

    for command in probes:
        output, error = run_remote_probe_fn(command)
        text = (output or "").strip()
        if error:
            last_error = error
        if not text:
            continue

        last_non_empty = text
        lower = text.lower()
        if any(token in lower for token in mismatch_tokens):
            continue
        if missing_marker in text:
            continue
        return text, None

    if last_non_empty:
        return last_non_empty, last_error
    return missing_marker, last_error
