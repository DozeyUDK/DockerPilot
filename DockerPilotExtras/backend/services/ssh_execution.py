"""Safe local/SSH command execution for DockerPilot Extras."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .server_config import _load_private_key
from .ssh_security import create_verified_ssh_client


def _local_argv(command: str) -> list[str]:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid command quoting: {exc}") from exc
    if not argv:
        raise ValueError("Command is empty")
    return argv


def open_verified_ssh_client(server_config: dict, *, known_hosts_path: str | Path, timeout: int = 10):
    """Open an authenticated SSH client after strict host-key verification."""
    import paramiko

    hostname = server_config.get("hostname")
    port = int(server_config.get("port", 22))
    username = server_config.get("username")
    auth_type = server_config.get("auth_type", "password")
    if not hostname or not username:
        raise ValueError("Missing SSH hostname or username")

    ssh = create_verified_ssh_client(
        server_config,
        known_hosts_path=known_hosts_path,
        timeout=timeout,
    )
    kwargs = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if auth_type == "password":
        password = server_config.get("password")
        if not password:
            raise ValueError("Password required for password authentication")
        kwargs["password"] = password
    elif auth_type == "key":
        key_content = server_config.get("private_key")
        if not key_content:
            raise ValueError("Private key required for key authentication")
        kwargs["pkey"] = _load_private_key(
            paramiko,
            key_content,
            server_config.get("key_passphrase"),
        )
    elif auth_type == "2fa":
        password = server_config.get("password")
        totp_code = server_config.get("totp_code", "")
        if not password:
            raise ValueError("Password required for 2FA authentication")
        kwargs["password"] = f"{password}{totp_code}"
    else:
        raise ValueError(f"Unsupported auth type: {auth_type}")

    ssh.connect(**kwargs)
    return ssh


def _run_local(argv: list[str], *, check_exit_status: bool, return_stderr: bool, timeout: int):
    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Command timed out") from exc
    if check_exit_status and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {result.stderr or ''}")
    if return_stderr:
        return result.stdout, result.stderr
    return result.stdout


def _run_remote(
    server_config: dict,
    command: str,
    *,
    known_hosts_path: str | Path,
    check_exit_status: bool,
    return_stderr: bool,
    timeout: int,
):
    ssh = open_verified_ssh_client(
        server_config,
        known_hosts_path=known_hosts_path,
        timeout=min(timeout, 30),
    )
    try:
        _stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8")
        error_output = stderr.read().decode("utf-8")
    finally:
        ssh.close()

    if check_exit_status and exit_status != 0:
        raise RuntimeError(f"Command failed (exit {exit_status}): {error_output}")
    if return_stderr:
        return output, error_output
    return output


def execute_command(
    server_config: dict | None,
    command: str,
    *,
    ssh_available: bool,
    known_hosts_path: str | Path,
    check_exit_status: bool = True,
    return_stderr: bool = False,
    timeout: int = 300,
):
    """Execute one command locally without a shell, or remotely over verified SSH."""
    if server_config is None or server_config.get("id") == "local":
        return _run_local(
            _local_argv(command),
            check_exit_status=check_exit_status,
            return_stderr=return_stderr,
            timeout=timeout,
        )
    if not ssh_available:
        raise ImportError("SSH libraries not available")
    return _run_remote(
        server_config,
        command,
        known_hosts_path=known_hosts_path,
        check_exit_status=check_exit_status,
        return_stderr=return_stderr,
        timeout=timeout,
    )


def execute_script(
    server_config: dict | None,
    script: str,
    *,
    ssh_available: bool,
    known_hosts_path: str | Path,
    check_exit_status: bool = True,
    return_stderr: bool = False,
    timeout: int = 300,
):
    """Execute an internal shell script explicitly; callers must quote all interpolated data."""
    if not isinstance(script, str) or not script.strip():
        raise ValueError("Script is empty")
    if server_config is None or server_config.get("id") == "local":
        return _run_local(
            ["bash", "-lc", script],
            check_exit_status=check_exit_status,
            return_stderr=return_stderr,
            timeout=timeout,
        )
    if not ssh_available:
        raise ImportError("SSH libraries not available")
    # Paramiko exec requests are interpreted by the remote account's shell. This
    # API is intentionally separate from execute_command so only audited,
    # internally-built scripts may use shell syntax.
    return _run_remote(
        server_config,
        script,
        known_hosts_path=known_hosts_path,
        check_exit_status=check_exit_status,
        return_stderr=return_stderr,
        timeout=timeout,
    )


def build_docker_command(docker_command: str, *, use_sudo: bool = False) -> str:
    """Parse Docker arguments and rebuild a shell-safe remote command."""
    args = _local_argv(docker_command)
    prefix = ["sudo", "docker"] if use_sudo else ["docker"]
    return shlex.join([*prefix, *args])
