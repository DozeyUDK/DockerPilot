"""Encrypted-at-rest storage helpers for DockerPilot Extras server credentials."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


_ENCRYPTED_PREFIX = "enc:v1:"
_SERVER_SECRET_FIELDS = frozenset({"password", "private_key", "key_passphrase", "totp_secret"})


class SecretStoreError(RuntimeError):
    """Raised when encrypted secret material cannot be loaded safely."""


class EncryptedSecretStore:
    """Encrypt/decrypt server credentials using a persistent Fernet master key."""

    def __init__(self, key: bytes | str):
        raw = key.encode("ascii") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(raw)
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError("Invalid DockerPilot Extras secret-store key") from exc

    @classmethod
    def from_config_dir(
        cls,
        config_dir: str | Path,
        *,
        env_var: str = "DOCKERPILOT_EXTRAS_SECRET_KEY",
        filename: str = ".secrets.key",
    ) -> "EncryptedSecretStore":
        """Load a master key from env or a mode-0600 file, generating it once if absent."""
        configured = (os.environ.get(env_var) or "").strip()
        if configured:
            return cls(configured)

        config_path = Path(config_dir)
        config_path.mkdir(parents=True, exist_ok=True)
        key_path = config_path / filename
        if key_path.exists():
            key = key_path.read_bytes().strip()
            if not key:
                raise SecretStoreError(f"Secret-store key file is empty: {key_path}")
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return cls(key)

        key = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(key_path, flags, 0o600)
        except FileExistsError:
            return cls(key_path.read_bytes().strip())
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return cls(key)

    @staticmethod
    def is_encrypted(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(_ENCRYPTED_PREFIX)

    def encrypt(self, value: str) -> str:
        if self.is_encrypted(value):
            return value
        token = self._fernet.encrypt(str(value).encode("utf-8")).decode("ascii")
        return f"{_ENCRYPTED_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        if not self.is_encrypted(value):
            return value
        token = value[len(_ENCRYPTED_PREFIX) :]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise SecretStoreError("Unable to decrypt stored server credential") from exc

    def protect_server(self, server: dict[str, Any]) -> dict[str, Any]:
        protected = copy.deepcopy(server)
        for field in _SERVER_SECRET_FIELDS:
            value = protected.get(field)
            if isinstance(value, str) and value:
                protected[field] = self.encrypt(value)
        return protected

    def reveal_server(self, server: dict[str, Any]) -> dict[str, Any]:
        revealed = copy.deepcopy(server)
        for field in _SERVER_SECRET_FIELDS:
            value = revealed.get(field)
            if isinstance(value, str) and value:
                revealed[field] = self.decrypt(value)
        return revealed

    def protect_config(self, config: dict[str, Any]) -> dict[str, Any]:
        protected = copy.deepcopy(config or {})
        protected["servers"] = [
            self.protect_server(item)
            for item in protected.get("servers", [])
            if isinstance(item, dict)
        ]
        protected.setdefault("default_server", "local")
        return protected

    def reveal_config(self, config: dict[str, Any]) -> dict[str, Any]:
        revealed = copy.deepcopy(config or {})
        revealed["servers"] = [
            self.reveal_server(item)
            for item in revealed.get("servers", [])
            if isinstance(item, dict)
        ]
        revealed.setdefault("default_server", "local")
        return revealed

    def has_plaintext_secrets(self, config: dict[str, Any]) -> bool:
        for server in (config or {}).get("servers", []):
            if not isinstance(server, dict):
                continue
            for field in _SERVER_SECRET_FIELDS:
                value = server.get(field)
                if isinstance(value, str) and value and not self.is_encrypted(value):
                    return True
        return False
