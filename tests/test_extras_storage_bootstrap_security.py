"""Security regressions for local PostgreSQL bootstrap credentials."""

from pathlib import Path
from types import SimpleNamespace
import sys

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.resources.storage import create_storage_resources


class Resource:
    pass


class Request:
    def __init__(self, payload):
        self.payload = dict(payload)

    def get_json(self):
        return dict(self.payload)


def _bootstrap_resource(payload, ensure_local_postgres_container):
    classes = create_storage_resources(
        Resource=Resource,
        app=SimpleNamespace(config={"CONFIG_DIR": Path("."), "SERVERS_DIR": Path(".")}),
        request=Request(payload),
        default_postgres_schema="DockerPilot",
        default_postgres_table_prefix="dp_",
        storage_error_cls=RuntimeError,
        get_storage_status=lambda: {},
        test_postgres_connection=lambda *_args, **_kwargs: {"success": True},
        discover_local_postgres=lambda **_kwargs: {"success": False},
        sanitize_postgres_config=lambda cfg: {**cfg, "password": "***"},
        ensure_local_postgres_container=ensure_local_postgres_container,
        create_store=lambda **_kwargs: object(),
        migrate_legacy_file_state_to_store=lambda _store: {},
        save_storage_config=lambda *_args, **_kwargs: None,
        init_state_store=lambda *_args, **_kwargs: None,
        build_postgres_dsn=lambda _cfg: "postgresql://sanitized",
    )
    return classes[3]


def test_bootstrap_rejects_missing_password_before_docker_mutation():
    calls = []
    Bootstrap = _bootstrap_resource({}, lambda **kwargs: calls.append(kwargs))

    body, status = Bootstrap().post()

    assert status == 400
    assert body["success"] is False
    assert "password is required" in body["error"]
    assert calls == []


def test_bootstrap_rejects_legacy_default_password_before_docker_mutation():
    calls = []
    Bootstrap = _bootstrap_resource(
        {"password": "dockerpilot_change_me"},
        lambda **kwargs: calls.append(kwargs),
    )

    body, status = Bootstrap().post()

    assert status == 400
    assert body["success"] is False
    assert "legacy default" in body["error"]
    assert calls == []


def test_bootstrap_forwards_explicit_password_without_echoing_it():
    captured = []
    container = SimpleNamespace(name="postgres-dozeyserver", status="running", id="abc")

    def ensure(**kwargs):
        captured.append(kwargs)
        return container, True

    Bootstrap = _bootstrap_resource(
        {
            "password": "correct-horse-battery-staple",
            "configure_storage": False,
            "migrate_from_file": False,
        },
        ensure,
    )

    body = Bootstrap().post()

    assert body["success"] is True
    assert captured[0]["password"] == "correct-horse-battery-staple"
    assert body["postgres"]["password"] == "***"
    assert "correct-horse-battery-staple" not in repr(body)


def test_bootstrap_maps_existing_container_credential_rejection_to_400():
    def reject(**_kwargs):
        raise ValueError("Existing PostgreSQL container password does not match requested password")

    Bootstrap = _bootstrap_resource(
        {"password": "new-secret"},
        reject,
    )

    body, status = Bootstrap().post()

    assert status == 400
    assert body["success"] is False
    assert "does not match requested password" in body["error"]
