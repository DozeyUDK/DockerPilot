from pathlib import Path
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.secret_store import EncryptedSecretStore


def test_secret_store_encrypts_server_credentials_and_round_trips(tmp_path: Path):
    store = EncryptedSecretStore(Fernet.generate_key())
    source = {
        "servers": [
            {
                "id": "srv-1",
                "hostname": "example.invalid",
                "password": "p@ssword",
                "private_key": "PRIVATE KEY",
                "key_passphrase": "key-pass",
                "totp_secret": "ABCDEF",
            }
        ],
        "default_server": "srv-1",
    }

    protected = store.protect_config(source)
    stored = protected["servers"][0]
    assert stored["password"].startswith("enc:v1:")
    assert stored["private_key"].startswith("enc:v1:")
    assert "p@ssword" not in repr(protected)
    assert "PRIVATE KEY" not in repr(protected)
    assert store.reveal_config(protected) == source


def test_secret_store_detects_plaintext_for_migration():
    store = EncryptedSecretStore(Fernet.generate_key())
    plain = {"servers": [{"id": "srv", "password": "secret"}]}
    protected = store.protect_config(plain)
    assert store.has_plaintext_secrets(plain)
    assert not store.has_plaintext_secrets(protected)


def test_secret_store_key_file_is_persistent_and_mode_600(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DOCKERPILOT_EXTRAS_SECRET_KEY", raising=False)
    first = EncryptedSecretStore.from_config_dir(tmp_path)
    protected = first.protect_config({"servers": [{"password": "secret"}]})

    second = EncryptedSecretStore.from_config_dir(tmp_path)
    assert second.reveal_config(protected)["servers"][0]["password"] == "secret"
    assert (tmp_path / ".secrets.key").stat().st_mode & 0o777 == 0o600
