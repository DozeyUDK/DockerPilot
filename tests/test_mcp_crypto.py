from pathlib import Path

import pytest

pytest.importorskip("cryptography")


def test_encrypt_decrypt_roundtrip(tmp_path, monkeypatch):
    from dockerpilot.mcp.crypto import EncryptionSettings, decrypt_file, encrypt_file

    plain = tmp_path / "plain.bin"
    enc = tmp_path / "plain.bin.enc"
    dec = tmp_path / "plain.bin.dec"

    plain.write_bytes(b"hello secret world\n" * 1000)
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATION_PASSPHRASE", "test-passphrase")

    settings = EncryptionSettings(enabled=True, passphrase="test-passphrase", passphrase_file=None, chunk_size=1024)
    encrypt_file(in_path=plain, out_path=enc, settings=settings)
    decrypt_file(in_path=enc, out_path=dec, settings=settings)

    assert dec.read_bytes() == plain.read_bytes()


def test_decrypt_wrong_passphrase_fails(tmp_path):
    from dockerpilot.mcp.crypto import EncryptionSettings, CryptoError, decrypt_file, encrypt_file

    plain = tmp_path / "plain.bin"
    enc = tmp_path / "plain.bin.enc"
    dec = tmp_path / "plain.bin.dec"

    plain.write_bytes(b"x" * 4096)
    settings_ok = EncryptionSettings(enabled=True, passphrase="ok", passphrase_file=None, chunk_size=1024)
    settings_bad = EncryptionSettings(enabled=True, passphrase="bad", passphrase_file=None, chunk_size=1024)

    encrypt_file(in_path=plain, out_path=enc, settings=settings_ok)
    with pytest.raises(CryptoError):
        decrypt_file(in_path=enc, out_path=dec, settings=settings_bad)
