from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
EXTRAS_DIR = ROOT / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.resources.promotion import create_promotion_resources


class Resource:
    pass


class Request:
    def __init__(self, payload):
        self.payload = dict(payload)

    def get_json(self):
        return dict(self.payload)


class StopAfterElevation(RuntimeError):
    pass


def _build_promote_single(*, payload, consume, captured_passwords):
    logger = SimpleNamespace(
        info=lambda *_a, **_k: None,
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
    )

    def execution_context_factory(_get_dockerpilot, *, sudo_password=None):
        captured_passwords.append(sudo_password)
        raise StopAfterElevation("stop-after-elevation")

    classes = create_promotion_resources(
        Resource=Resource,
        app=SimpleNamespace(logger=logger, config={"CONFIG_DIR": ROOT}),
        request=Request(payload),
        datetime_cls=SimpleNamespace(now=lambda: SimpleNamespace(isoformat=lambda: "now")),
        deployment_progress={},
        get_dockerpilot=lambda: None,
        consume_elevation_token=consume,
        find_all_deployment_configs_for_env=lambda _env: [],
        resolve_server_id_for_env=lambda _env: "srv",
        promote_config_to_server=lambda *_a, **_k: True,
        move_many_container_bindings=lambda *_a, **_k: None,
        move_container_binding=lambda *_a, **_k: None,
        format_env_name=lambda value: value,
        find_active_deployment_dir=lambda _name: None,
        migration_runner=SimpleNamespace(),
        execution_context_factory=execution_context_factory,
    )
    return classes[-1]


def _base_payload(**extra):
    payload = {
        "from_env": "dev",
        "to_env": "staging",
        "container_name": "web",
    }
    payload.update(extra)
    return payload


def test_promotion_scope_mismatch_has_no_legacy_fallback():
    calls = []
    passwords = []

    def consume(token, *, expected_action=None, expected_scope=None):
        calls.append((token, expected_action, expected_scope))
        return False, "Elevation token scope mismatch (container_name)", None

    PromoteSingle = _build_promote_single(
        payload=_base_payload(elevation_token="strict-token"),
        consume=consume,
        captured_passwords=passwords,
    )

    response = PromoteSingle().post()

    assert response == ({"error": "Elevation token scope mismatch (container_name)"}, 403)
    assert passwords == []
    assert calls == [
        (
            "strict-token",
            "environment.promote_single",
            {
                "container_name": "web",
                "from_env": "dev",
                "to_env": "staging",
            },
        )
    ]


def test_promotion_without_token_does_not_consume_hidden_compatibility_token():
    calls = []
    passwords = []

    def consume(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("no token should be consumed")

    PromoteSingle = _build_promote_single(
        payload=_base_payload(),
        consume=consume,
        captured_passwords=passwords,
    )

    response = PromoteSingle().post()

    assert response[1] == 500
    assert response[0]["error"] == "stop-after-elevation"
    assert calls == []
    assert passwords == [None]


def test_legacy_sudo_password_surface_is_absent():
    auth_source = (EXTRAS_DIR / "backend" / "resources" / "auth.py").read_text(encoding="utf-8")
    promotion_source = (EXTRAS_DIR / "backend" / "resources" / "promotion.py").read_text(encoding="utf-8")
    api_source = (EXTRAS_DIR / "backend" / "api.py").read_text(encoding="utf-8")
    frontend_api = (EXTRAS_DIR / "frontend" / "src" / "services" / "api.js").read_text(encoding="utf-8")

    for source in (auth_source, promotion_source, api_source, frontend_api):
        assert "legacy.sudo_password" not in source
        assert "legacy_elevation_token" not in source

    assert '"/api/environment/sudo-password"' not in api_source
    assert "/environment/sudo-password" not in frontend_api
    assert "class SudoPassword" not in auth_source
    assert "clearSudoPassword" not in frontend_api
    assert "setSudoPassword:" not in frontend_api
