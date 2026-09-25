from pathlib import Path
import sys

import pytest

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.services import environment_state


def test_normalize_bindings_dedupes_and_trims():
    result = environment_state.normalize_env_container_bindings(
        {"env_containers": {"dev": [" api ", "api", "", 1], "prod": ["web"]}},
        now=lambda: type("T", (), {"isoformat": lambda self: "now"})(),
    )
    assert result["env_containers"] == {"dev": ["api"], "staging": [], "prod": ["web"]}
    assert result["updated_at"] == "now"


def test_move_many_bindings_invalidates_after_save():
    saved = []
    invalidated = []
    ok = environment_state.move_many_container_bindings(
        ["api", "api", " web "],
        "dev",
        "prod",
        load_bindings=lambda: {"env_containers": {"dev": ["api", "web"], "staging": [], "prod": []}},
        save_bindings=lambda value: saved.append(value) or True,
        invalidate_cache=lambda: invalidated.append(True),
    )
    assert ok is True
    assert saved[-1]["env_containers"] == {"dev": [], "staging": [], "prod": ["api", "web"]}
    assert invalidated == [True]


def test_history_falls_back_to_legacy_signature():
    class Store:
        def get_deployment_history(self, *args, **kwargs):
            if kwargs:
                raise TypeError("legacy")
            return [1, 2, 3]
    assert environment_state.get_deployment_history_data(
        get_state_store=lambda: Store(), limit=2
    ) == [2, 3]


def _legacy_source(tmp_path: Path):
    class Source:
        def __init__(self):
            self.config_dir = tmp_path

        def load_servers_config(self):
            return {
                "servers": [
                    {
                        "id": "x",
                        "name": "node",
                        "hostname": "node.example",
                        "username": "dawid",
                        "auth_type": "key",
                        "password": "legacy-password",
                        "private_key": "LEGACY PRIVATE KEY",
                        "key_passphrase": "legacy-passphrase",
                        "totp_secret": "LEGACYTOTP",
                    }
                ],
                "default_server": "x",
            }

        def load_env_servers_config(self):
            return {"env_servers": {"prod": "x"}}

        def get_deployment_history(self):
            return [{"id": 1}]

        def load_env_container_bindings(self):
            return {"env_containers": {"dev": ["a"], "prod": ["b"]}}

    return Source


class Target:
    def __init__(self):
        self.calls = {}

    def save_servers_config(self, v):
        self.calls["servers"] = v

    def save_env_servers_config(self, v):
        self.calls["env"] = v

    def replace_deployment_history(self, v, max_entries=50):
        self.calls["history"] = v

    def save_env_container_bindings(self, v):
        self.calls["bindings"] = v


def test_migrate_legacy_snapshot_protects_servers_before_target_save(tmp_path: Path):
    def protect(config):
        protected = {**config, "servers": []}
        for item in config.get("servers", []):
            server = dict(item)
            for field in ("password", "private_key", "key_passphrase", "totp_secret"):
                value = server.get(field)
                if value:
                    server[field] = f"enc:v1:test:{field}"
            protected["servers"].append(server)
        return protected

    target = Target()
    result = environment_state.migrate_legacy_file_state_to_store(
        target_store=target,
        file_store_factory=_legacy_source(tmp_path),
        protect_servers_config=protect,
    )

    assert result == {"servers": 1, "env_mappings": 1, "history_entries": 1, "env_container_bindings": 2}
    assert target.calls["bindings"]["env_containers"]["staging"] == []

    stored_server = target.calls["servers"]["servers"][0]
    for field in ("password", "private_key", "key_passphrase", "totp_secret"):
        assert stored_server[field] == f"enc:v1:test:{field}"
    assert "legacy-password" not in repr(target.calls["servers"])
    assert "LEGACY PRIVATE KEY" not in repr(target.calls["servers"])
    assert "legacy-passphrase" not in repr(target.calls["servers"])
    assert "LEGACYTOTP" not in repr(target.calls["servers"])


def test_migrate_legacy_snapshot_default_protector_encrypts_when_available(tmp_path: Path):
    pytest.importorskip("cryptography.fernet")
    target = Target()

    environment_state.migrate_legacy_file_state_to_store(
        target_store=target,
        file_store_factory=_legacy_source(tmp_path),
    )

    stored_server = target.calls["servers"]["servers"][0]
    for field in ("password", "private_key", "key_passphrase", "totp_secret"):
        assert stored_server[field].startswith("enc:v1:")
    assert (tmp_path / ".secrets.key").exists()
