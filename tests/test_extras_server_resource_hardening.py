from pathlib import Path
import importlib.util
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SERVERS_PATH = ROOT / "DockerPilotExtras" / "backend" / "resources" / "servers.py"
spec = importlib.util.spec_from_file_location("extras_servers_resource_under_test", SERVERS_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
create_server_resources = module.create_server_resources


class Resource:
    pass


class Request:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def get_json(self):
        return dict(self.payload)


class Session(dict):
    permanent = False


def _server_update(config, payload):
    saved = []
    app = SimpleNamespace(
        logger=SimpleNamespace(
            error=lambda *_a, **_k: None,
            info=lambda *_a, **_k: None,
            debug=lambda *_a, **_k: None,
        )
    )
    classes = create_server_resources(
        Resource=Resource,
        app=app,
        request=Request(payload),
        session=Session(),
        ssh_available=True,
        load_servers_config=lambda: config,
        save_servers_config=lambda value: saved.append(value) or True,
        test_ssh_connection=lambda _cfg: {"success": True},
    )
    response = classes[2]().put("srv-1")
    return response, saved[-1]["servers"][0]


def test_server_update_preserves_existing_secrets_when_form_sends_blanks():
    config = {
        "servers": [
            {
                "id": "srv-1",
                "name": "node",
                "hostname": "node.example",
                "port": 22,
                "username": "dawid",
                "auth_type": "key",
                "private_key": "PRIVATE",
                "key_passphrase": "old-passphrase",
                "host_key_fingerprint": "SHA256:old",
            }
        ],
        "default_server": "local",
    }

    response, server = _server_update(
        config,
        {
            "name": "node-renamed",
            "private_key": "",
            "key_passphrase": "",
            "password": "",
            "totp_secret": "",
            "host_key_fingerprint": "SHA256:new",
        },
    )

    assert response["success"] is True
    assert server["name"] == "node-renamed"
    assert server["private_key"] == "PRIVATE"
    assert server["key_passphrase"] == "old-passphrase"
    assert server["host_key_fingerprint"] == "SHA256:new"


def test_server_update_replacement_key_drops_stale_passphrase():
    config = {
        "servers": [
            {
                "id": "srv-1",
                "name": "node",
                "hostname": "node.example",
                "port": 22,
                "username": "dawid",
                "auth_type": "key",
                "private_key": "OLD PRIVATE KEY",
                "key_passphrase": "old-passphrase",
            }
        ],
        "default_server": "local",
    }

    response, server = _server_update(
        config,
        {
            "private_key": "NEW UNENCRYPTED PRIVATE KEY",
            "key_passphrase": "",
        },
    )

    assert response["success"] is True
    assert server["private_key"] == "NEW UNENCRYPTED PRIVATE KEY"
    assert "key_passphrase" not in server


def test_server_update_replacement_key_uses_new_passphrase():
    config = {
        "servers": [
            {
                "id": "srv-1",
                "name": "node",
                "hostname": "node.example",
                "port": 22,
                "username": "dawid",
                "auth_type": "key",
                "private_key": "OLD PRIVATE KEY",
                "key_passphrase": "old-passphrase",
            }
        ],
        "default_server": "local",
    }

    response, server = _server_update(
        config,
        {
            "private_key": "NEW ENCRYPTED PRIVATE KEY",
            "key_passphrase": "new-passphrase",
        },
    )

    assert response["success"] is True
    assert server["private_key"] == "NEW ENCRYPTED PRIVATE KEY"
    assert server["key_passphrase"] == "new-passphrase"


def test_server_update_can_explicitly_clear_key_passphrase_without_replacing_key():
    config = {
        "servers": [
            {
                "id": "srv-1",
                "name": "node",
                "hostname": "node.example",
                "port": 22,
                "username": "dawid",
                "auth_type": "key",
                "private_key": "PRIVATE",
                "key_passphrase": "old-passphrase",
            }
        ],
        "default_server": "local",
    }

    response, server = _server_update(
        config,
        {
            "private_key": "",
            "key_passphrase": "",
            "clear_key_passphrase": True,
        },
    )

    assert response["success"] is True
    assert server["private_key"] == "PRIVATE"
    assert "key_passphrase" not in server


def test_server_list_exposes_fingerprint_but_never_secret_fields():
    config = {
        "servers": [
            {
                "id": "srv-1",
                "name": "node",
                "hostname": "node.example",
                "username": "dawid",
                "auth_type": "password",
                "password": "secret",
                "private_key": "PRIVATE",
                "host_key_fingerprint": "SHA256:key",
            }
        ]
    }
    app = SimpleNamespace(logger=SimpleNamespace(error=lambda *_a, **_k: None, info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None))
    classes = create_server_resources(
        Resource=Resource,
        app=app,
        request=Request(),
        session=Session(),
        ssh_available=True,
        load_servers_config=lambda: config,
        save_servers_config=lambda _value: True,
        test_ssh_connection=lambda _cfg: {"success": True},
    )
    ServerList = classes[0]

    payload = ServerList().get()
    server = payload["servers"][0]

    assert server["host_key_fingerprint"] == "SHA256:key"
    assert "password" not in server
    assert "private_key" not in server


def test_server_test_forwards_trusted_fingerprint_before_save():
    observed = []
    request = Request(
        {
            "hostname": "new.example",
            "port": 2222,
            "username": "dawid",
            "auth_type": "password",
            "password": "secret",
            "host_key_fingerprint": "  SHA256:trusted-key  ",
        }
    )
    app = SimpleNamespace(logger=SimpleNamespace(error=lambda *_a, **_k: None, info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None))
    classes = create_server_resources(
        Resource=Resource,
        app=app,
        request=request,
        session=Session(),
        ssh_available=True,
        load_servers_config=lambda: {"servers": []},
        save_servers_config=lambda _value: True,
        test_ssh_connection=lambda cfg: observed.append(cfg) or {"success": True},
    )
    ServerTest = classes[4]

    response = ServerTest().post()

    assert response["success"] is True
    assert observed[-1]["host_key_fingerprint"] == "SHA256:trusted-key"
    assert observed[-1]["hostname"] == "new.example"
    assert observed[-1]["port"] == 2222
