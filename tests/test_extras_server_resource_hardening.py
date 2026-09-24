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
    saved = []
    request = Request(
        {
            "name": "node-renamed",
            "private_key": "",
            "key_passphrase": "",
            "password": "",
            "totp_secret": "",
            "host_key_fingerprint": "SHA256:new",
        }
    )
    app = SimpleNamespace(logger=SimpleNamespace(error=lambda *_a, **_k: None, info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None))
    classes = create_server_resources(
        Resource=Resource,
        app=app,
        request=request,
        session=Session(),
        ssh_available=True,
        load_servers_config=lambda: config,
        save_servers_config=lambda value: saved.append(value) or True,
        test_ssh_connection=lambda _cfg: {"success": True},
    )
    ServerUpdate = classes[2]

    response = ServerUpdate().put("srv-1")

    assert response["success"] is True
    server = saved[-1]["servers"][0]
    assert server["name"] == "node-renamed"
    assert server["private_key"] == "PRIVATE"
    assert server["key_passphrase"] == "old-passphrase"
    assert server["host_key_fingerprint"] == "SHA256:new"


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
