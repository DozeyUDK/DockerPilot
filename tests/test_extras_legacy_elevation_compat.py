from pathlib import Path
import importlib.util
import sys
import types
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
EXTRAS_DIR = ROOT / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

# promotion.py imports migration execution seams, but these compatibility tests
# stop before any migration begins. Stub only those import-time dependencies and
# restore sys.modules immediately after loading the module so the rest of the
# pytest process still sees the real implementations.
_execution_name = "backend.services.migration_execution"
_runner_name = "backend.services.migration_runner"
_previous_execution = sys.modules.get(_execution_name)
_previous_runner = sys.modules.get(_runner_name)

migration_execution_stub = types.ModuleType(_execution_name)
migration_execution_stub.DockerPilotExecutionContext = object

migration_runner_stub = types.ModuleType(_runner_name)


class MigrationSpec:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


migration_runner_stub.MigrationSpec = MigrationSpec
sys.modules[_execution_name] = migration_execution_stub
sys.modules[_runner_name] = migration_runner_stub

try:
    PROMOTION_PATH = EXTRAS_DIR / "backend" / "resources" / "promotion.py"
    spec = importlib.util.spec_from_file_location("extras_promotion_resource_under_test", PROMOTION_PATH)
    promotion_module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(promotion_module)
    create_promotion_resources = promotion_module.create_promotion_resources
finally:
    if _previous_execution is None:
        sys.modules.pop(_execution_name, None)
    else:
        sys.modules[_execution_name] = _previous_execution
    if _previous_runner is None:
        sys.modules.pop(_runner_name, None)
    else:
        sys.modules[_runner_name] = _previous_runner


class Resource:
    pass


class Request:
    def __init__(self, payload):
        self.payload = dict(payload)

    def get_json(self):
        return dict(self.payload)


class Session(dict):
    permanent = False


class StopAfterElevation(RuntimeError):
    pass


def _build_promote_single(*, payload, session, consume, captured_passwords):
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
        session=session,
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


def test_old_client_can_consume_session_bound_legacy_token_without_forwarding_it():
    calls = []
    passwords = []
    session = Session({"legacy_elevation_token": "legacy-token"})

    def consume(token, *, expected_action=None, expected_scope=None):
        calls.append((token, expected_action, expected_scope))
        assert token == "legacy-token"
        assert expected_action == "legacy.sudo_password"
        assert expected_scope is None
        return True, "ok", "sudo-secret"

    PromoteSingle = _build_promote_single(
        payload=_base_payload(),
        session=session,
        consume=consume,
        captured_passwords=passwords,
    )

    response = PromoteSingle().post()

    assert response[1] == 500
    assert response[0]["error"] == "stop-after-elevation"
    assert passwords == ["sudo-secret"]
    assert calls == [("legacy-token", "legacy.sudo_password", None)]
    assert "legacy_elevation_token" not in session


def test_forwarded_legacy_token_falls_back_after_strict_validation_fails():
    calls = []
    passwords = []
    session = Session({"legacy_elevation_token": "legacy-token"})

    def consume(token, *, expected_action=None, expected_scope=None):
        calls.append((token, expected_action, expected_scope))
        if expected_action == "environment.promote_single":
            return False, "Elevation token scope mismatch (action)", None
        assert expected_action == "legacy.sudo_password"
        return True, "ok", "sudo-secret"

    PromoteSingle = _build_promote_single(
        payload=_base_payload(elevation_token="legacy-token"),
        session=session,
        consume=consume,
        captured_passwords=passwords,
    )

    response = PromoteSingle().post()

    assert response[1] == 500
    assert passwords == ["sudo-secret"]
    assert [call[1] for call in calls] == [
        "environment.promote_single",
        "legacy.sudo_password",
    ]
    assert calls[0][2] == {
        "container_name": "web",
        "from_env": "dev",
        "to_env": "staging",
    }
    assert "legacy_elevation_token" not in session


def test_strict_scope_mismatch_never_downgrades_to_legacy_compatibility():
    calls = []
    passwords = []

    def consume(token, *, expected_action=None, expected_scope=None):
        calls.append((token, expected_action, expected_scope))
        if expected_action == "environment.promote_single":
            return False, "Elevation token scope mismatch (container_name)", None
        return False, "Elevation token scope mismatch (action)", None

    PromoteSingle = _build_promote_single(
        payload=_base_payload(elevation_token="strict-token"),
        session=Session(),
        consume=consume,
        captured_passwords=passwords,
    )

    response = PromoteSingle().post()

    assert response == ({"error": "Elevation token scope mismatch (container_name)"}, 403)
    assert passwords == []
    assert [call[1] for call in calls] == [
        "environment.promote_single",
        "legacy.sudo_password",
    ]
