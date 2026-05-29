from __future__ import annotations

import base64
import json
import os
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional


class CryptoUnavailable(RuntimeError):
    pass


class CryptoError(RuntimeError):
    pass


MAGIC = b"DPMIGENC1"
TAG_LEN = 16


def _require_crypto():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        from cryptography.hazmat.backends import default_backend
    except Exception as exc:  # pragma: no cover
        raise CryptoUnavailable(
            "Encryption support requires cryptography. Install with: pip install -e '.[mcp-crypto]'"
        ) from exc
    return Cipher, algorithms, modes, Scrypt, default_backend


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class EncryptionSettings:
    enabled: bool
    passphrase: Optional[str] = None
    passphrase_file: Optional[Path] = None
    mode: str = "aesgcm"
    chunk_size: int = 1024 * 1024
    scrypt_n: int = 16384
    scrypt_r: int = 8
    scrypt_p: int = 1

    @classmethod
    def from_env(cls, environ: Optional[dict[str, str]] = None) -> "EncryptionSettings":
        env = os.environ if environ is None else environ
        raw = (env.get("DOCKERPILOT_MCP_MIGRATION_ENCRYPTION") or "").strip().lower()
        if raw in {"", "0", "false", "no", "off"}:
            return cls(enabled=False)
        mode = "aesgcm"
        if raw in {"1", "true", "yes", "on", "aesgcm"}:
            mode = "aesgcm"
        elif raw == "age":
            mode = "age"
        else:
            mode = raw
        passphrase = (env.get("DOCKERPILOT_MCP_MIGRATION_PASSPHRASE") or None)
        passphrase_file_raw = (env.get("DOCKERPILOT_MCP_MIGRATION_PASSPHRASE_FILE") or "").strip()
        passphrase_file = Path(passphrase_file_raw).expanduser() if passphrase_file_raw else None
        return cls(enabled=True, passphrase=passphrase, passphrase_file=passphrase_file, mode=mode)

    def resolve_passphrase(self) -> Optional[str]:
        if self.passphrase_file is not None:
            try:
                return self.passphrase_file.read_text(encoding="utf-8").strip()
            except Exception as exc:
                raise CryptoError(f"Unable to read passphrase file: {self.passphrase_file}") from exc
        return self.passphrase


def encrypt_file(*, in_path: Path, out_path: Path, settings: EncryptionSettings) -> dict:
    Cipher, algorithms, modes, Scrypt, default_backend = _require_crypto()
    passphrase = settings.resolve_passphrase()
    if not passphrase:
        raise CryptoError("Missing passphrase: set DOCKERPILOT_MCP_MIGRATION_PASSPHRASE or DOCKERPILOT_MCP_MIGRATION_PASSPHRASE_FILE.")

    salt = os.urandom(16)
    nonce = os.urandom(12)
    kdf = Scrypt(salt=salt, length=32, n=settings.scrypt_n, r=settings.scrypt_r, p=settings.scrypt_p)
    key = kdf.derive(passphrase.encode("utf-8"))

    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()

    header = {
        "format": "dockerpilot-migration-encrypted/v1",
        "created_at": _now_utc_iso(),
        "kdf": {"name": "scrypt", "salt": _b64e(salt), "n": settings.scrypt_n, "r": settings.scrypt_r, "p": settings.scrypt_p},
        "cipher": {"name": "aes-256-gcm", "nonce": _b64e(nonce), "tag_len": TAG_LEN},
        "chunk_size": settings.chunk_size,
    }
    header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
    if len(header_bytes) > 1_000_000:
        raise CryptoError("Encryption header too large.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open("rb") as fin, out_path.open("wb") as fout:
        fout.write(MAGIC)
        fout.write(struct.pack(">I", len(header_bytes)))
        fout.write(header_bytes)
        while True:
            chunk = fin.read(settings.chunk_size)
            if not chunk:
                break
            fout.write(encryptor.update(chunk))
        fout.write(encryptor.finalize())
        fout.write(encryptor.tag)

    return {"encrypted_path": str(out_path), "header": header}


def decrypt_file(*, in_path: Path, out_path: Path, settings: EncryptionSettings) -> dict:
    Cipher, algorithms, modes, Scrypt, default_backend = _require_crypto()
    passphrase = settings.resolve_passphrase()
    if not passphrase:
        raise CryptoError("Missing passphrase: set DOCKERPILOT_MCP_MIGRATION_PASSPHRASE or DOCKERPILOT_MCP_MIGRATION_PASSPHRASE_FILE.")

    with in_path.open("rb") as fin:
        magic = fin.read(len(MAGIC))
        if magic != MAGIC:
            raise CryptoError("Not an encrypted migration bundle (bad magic).")
        header_len_bytes = fin.read(4)
        if len(header_len_bytes) != 4:
            raise CryptoError("Corrupt encrypted bundle (missing header length).")
        (header_len,) = struct.unpack(">I", header_len_bytes)
        header_bytes = fin.read(header_len)
        if len(header_bytes) != header_len:
            raise CryptoError("Corrupt encrypted bundle (truncated header).")
        header = json.loads(header_bytes.decode("utf-8"))

        kdf_info = header.get("kdf") or {}
        cipher_info = header.get("cipher") or {}
        if (kdf_info.get("name") or "") != "scrypt":
            raise CryptoError("Unsupported KDF in bundle.")
        if (cipher_info.get("name") or "") != "aes-256-gcm":
            raise CryptoError("Unsupported cipher in bundle.")

        salt = _b64d(kdf_info["salt"])
        nonce = _b64d(cipher_info["nonce"])
        n = int(kdf_info.get("n", settings.scrypt_n))
        r = int(kdf_info.get("r", settings.scrypt_r))
        p = int(kdf_info.get("p", settings.scrypt_p))
        kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
        key = kdf.derive(passphrase.encode("utf-8"))

        file_size = in_path.stat().st_size
        # ciphertext begins at current offset; tag is last TAG_LEN bytes
        ct_start = fin.tell()
        if file_size < ct_start + TAG_LEN:
            raise CryptoError("Corrupt encrypted bundle (too small).")
        ct_len = file_size - ct_start - TAG_LEN

        fin.seek(ct_start + ct_len)
        tag = fin.read(TAG_LEN)
        if len(tag) != TAG_LEN:
            raise CryptoError("Corrupt encrypted bundle (missing tag).")
        fin.seek(ct_start)

        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
        decryptor = cipher.decryptor()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")
        try:
            with tmp_out.open("wb") as fout:
                remaining = ct_len
                chunk_size = int(header.get("chunk_size") or settings.chunk_size)
                if chunk_size < 1:
                    chunk_size = settings.chunk_size
                while remaining > 0:
                    chunk = fin.read(min(chunk_size, remaining))
                    if not chunk:
                        raise CryptoError("Corrupt encrypted bundle (truncated ciphertext).")
                    remaining -= len(chunk)
                    fout.write(decryptor.update(chunk))
                try:
                    fout.write(decryptor.finalize())
                except Exception as exc:
                    raise CryptoError("Decryption failed (bad passphrase or corrupted bundle).") from exc
            tmp_out.replace(out_path)
        finally:
            try:
                if tmp_out.exists():
                    tmp_out.unlink()
            except Exception:
                pass

    return {"decrypted_path": str(out_path), "header": header}
