"""Server configuration and SSH validation helpers."""

from __future__ import annotations

from pathlib import Path

from .ssh_security import (
    SSHHostKeyMismatch,
    SSHHostKeyRequired,
    create_verified_ssh_client,
)


def get_servers_config_path(servers_dir) -> Path:
    """Get path to servers configuration file."""
    return Path(servers_dir) / "servers.json"


def load_servers_config(get_state_store, logger=None):
    """Load servers configuration via configured state store."""
    try:
        config = get_state_store().load_servers_config() or {}
        config.setdefault("servers", [])
        config.setdefault("default_server", "local")
        return config
    except Exception as exc:
        if logger:
            logger.error(f"Failed to load servers config: {exc}")
        return {"servers": [], "default_server": "local"}


def save_servers_config(config, get_state_store, logger=None):
    """Save servers configuration via configured state store."""
    try:
        return bool(get_state_store().save_servers_config(config))
    except Exception as exc:
        if logger:
            logger.error(f"Failed to save servers config: {exc}")
        return False


def convert_putty_key_to_openssh(ppk_content, passphrase=None):
    """Convert PuTTY private key (.ppk) to OpenSSH format."""
    try:
        lines = ppk_content.strip().split("\n")
        if "PuTTY-User-Key-File" not in lines[0]:
            raise ValueError("Not a valid PuTTY key file")

        raise NotImplementedError(
            "Full PuTTY key conversion requires additional library. "
            "Please convert .ppk to OpenSSH format using PuTTYgen or use OpenSSH key format."
        )
    except Exception as exc:
        raise ValueError(f"Failed to convert PuTTY key: {str(exc)}")


def _load_private_key(paramiko, key_content: str, key_passphrase: str | None):
    from io import StringIO

    if key_content.strip().startswith("PuTTY-User-Key-File"):
        raise NotImplementedError("PuTTY key conversion required")

    loaders = [paramiko.RSAKey, getattr(paramiko, "Ed25519Key", None), paramiko.ECDSAKey]
    dss = getattr(paramiko, "DSSKey", None)
    if dss is not None:
        loaders.append(dss)

    last_error = None
    for key_cls in loaders:
        if key_cls is None:
            continue
        try:
            return key_cls.from_private_key(
                StringIO(key_content),
                password=key_passphrase if key_passphrase else None,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"Failed to load private key: {last_error}")


def test_ssh_connection(server_config, ssh_available: bool, known_hosts_path=None):
    """Test SSH connection with strict host-key verification."""
    if not ssh_available:
        return {"success": False, "error": "SSH libraries not available"}

    try:
        import paramiko

        hostname = server_config.get("hostname")
        port = int(server_config.get("port", 22))
        username = server_config.get("username")
        auth_type = server_config.get("auth_type", "password")

        if not hostname or not username:
            return {"success": False, "error": "Missing hostname or username"}

        trust_path = Path(known_hosts_path or (Path.home() / ".dockerpilot_extras" / "known_hosts"))
        client = create_verified_ssh_client(
            server_config,
            known_hosts_path=trust_path,
            timeout=10,
        )

        connect_kwargs = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if auth_type == "password":
            password = server_config.get("password")
            if not password:
                return {"success": False, "error": "Password required for password authentication"}
            connect_kwargs["password"] = password
        elif auth_type == "key":
            key_content = server_config.get("private_key")
            key_passphrase = server_config.get("key_passphrase")
            if not key_content:
                return {"success": False, "error": "Private key required for key authentication"}
            connect_kwargs["pkey"] = _load_private_key(paramiko, key_content, key_passphrase)
        elif auth_type == "2fa":
            password = server_config.get("password")
            totp_code = server_config.get("totp_code")
            if not password:
                return {"success": False, "error": "Password required for 2FA authentication"}
            if not totp_code:
                return {"success": False, "error": "TOTP code required for 2FA authentication"}
            connect_kwargs["password"] = password + totp_code
        else:
            return {"success": False, "error": f"Unknown authentication type: {auth_type}"}

        client.connect(**connect_kwargs)
        _stdin, stdout, _stderr = client.exec_command('echo "test"')
        exit_status = stdout.channel.recv_exit_status()
        client.close()

        if exit_status == 0:
            return {"success": True, "message": "Connection successful; SSH host key verified"}
        return {"success": False, "error": f"Command execution failed with status {exit_status}"}

    except SSHHostKeyRequired as exc:
        return {
            "success": False,
            "error": str(exc),
            "host_key_required": True,
            "host_key_fingerprint": exc.fingerprint,
            "host_key_type": exc.key_type,
        }
    except SSHHostKeyMismatch as exc:
        return {
            "success": False,
            "error": str(exc),
            "host_key_mismatch": True,
        }
    except Exception as exc:
        err_text = str(exc)
        import paramiko

        if isinstance(exc, paramiko.AuthenticationException):
            return {"success": False, "error": "Authentication failed - check credentials"}
        if isinstance(exc, paramiko.SSHException):
            return {"success": False, "error": f"SSH error: {err_text}"}
        return {"success": False, "error": f"Connection failed: {err_text}"}
