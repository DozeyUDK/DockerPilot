"""Remote probe helpers used by status checks."""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple


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
) -> Tuple[str, Optional[str]]:
    """Execute a remote probe command with small retry for transient SSH issues."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            output = execute_command_via_ssh(server_config, command, check_exit_status=False)
            return (output or "").strip(), None
        except Exception as exc:
            last_error = str(exc)
            if logger:
                logger.warning(
                    "Remote probe failed (attempt %s/%s): %s | command=%s",
                    attempt,
                    attempts,
                    exc,
                    command,
                )
            if attempt < attempts:
                time.sleep(retry_delay)

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
