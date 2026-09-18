"""Characterization tests for the synchronous migration runner seam."""

from datetime import datetime
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.resources.migration import create_migration_resource
from backend.resources.promotion import create_promotion_resources
from backend.services.migration_runner import (
    MigrationResult,
    MigrationSpec,
    SynchronousMigrationRunner,
)


class _Resource:
    """Minimal stand-in for ``flask_restful.Resource``."""


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self):
        return self.payload


class _App:
    def __init__(self):
        self.logger = logging.getLogger("dockerpilot.tests.migration_runner")
        self.config = {"CONFIG_DIR": Path("/unused")}

    def test_request_context(self, *_args, **_kwargs):
        raise AssertionError("promotion must not create a Flask request context")


def test_synchronous_runner_normalizes_legacy_resource_results_without_flask():
    calls = []

    def operation(payload):
        calls.append(payload)
        return {"success": True, "message": "done"}

    result = SynchronousMigrationRunner(operation).run_inline(
        MigrationSpec(
            container_name="api",
            source_server_id="dev",
            target_server_id="prod",
            include_data=True,
            stop_source=False,
        )
    )

    assert calls == [
        {
            "container_name": "api",
            "source_server_id": "dev",
            "target_server_id": "prod",
            "include_data": True,
            "stop_source": False,
        }
    ]
    assert result.body == {"success": True, "message": "done"}
    assert result.status == 200
    assert result.completed_successfully is True
    assert result.to_response() == result.body


def test_accepted_response_is_not_a_completed_synchronous_migration():
    result = SynchronousMigrationRunner(
        lambda _payload: ({"success": True, "migration_id": "job-1"}, 202)
    ).run_inline(MigrationSpec("api", "dev", "prod"))

    assert result.status == 202
    assert result.is_terminal is False
    assert result.completed_successfully is False
    assert result.to_response() == (result.body, 202)


def test_runner_preserves_legacy_response_headers():
    headers = {"X-Migration": "legacy"}
    result = SynchronousMigrationRunner(
        lambda _payload: ({"error": "nope"}, 409, headers)
    ).run_inline(MigrationSpec("api", "dev", "prod"))

    assert result.to_response() == ({"error": "nope"}, 409, headers)


def test_runner_normalizes_executor_exceptions_and_redacts_secrets():
    def operation(_payload):
        raise RuntimeError("docker failed --env API_TOKEN=do-not-leak")

    result = SynchronousMigrationRunner(operation).run_inline(
        MigrationSpec("api", "dev", "prod")
    )

    assert result.status == 500
    assert result.explicit_status is True
    assert "do-not-leak" not in result.body["error"]
    assert result.completed_successfully is False


def test_success_requires_status_200_and_explicit_success_true():
    assert MigrationResult({"success": False}, 200).completed_successfully is False
    assert MigrationResult({"message": "done"}, 200).completed_successfully is False
    assert MigrationResult({"success": True}, 201).completed_successfully is False


def test_endpoint_treats_null_json_as_missing_required_fields():
    resource = create_migration_resource(
        Resource=_Resource,
        app=_App(),
        request=_Request(None),
        datetime_cls=datetime,
        migration_progress={},
        migration_cancel_flags={},
        load_servers_config=lambda: (_ for _ in ()).throw(
            AssertionError("invalid input must not load configuration")
        ),
        get_dockerpilot=lambda: (_ for _ in ()).throw(
            AssertionError("invalid input must not touch Docker")
        ),
        execute_command_via_ssh=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not use SSH")
        ),
        execute_docker_command_via_ssh=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not use SSH")
        ),
        save_deployment_config=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not save configuration")
        ),
        infer_port_mapping_for_host_network=lambda *_args, **_kwargs: {},
    )

    assert resource().post() == (
        {"error": "container_name and target_server_id are required"},
        400,
    )


@pytest.mark.parametrize("payload", [["not", "an", "object"], "not-an-object", 42])
def test_endpoint_treats_non_object_json_as_missing_required_fields(payload):
    resource = create_migration_resource(
        Resource=_Resource,
        app=_App(),
        request=_Request(payload),
        datetime_cls=datetime,
        migration_progress={},
        migration_cancel_flags={},
        load_servers_config=lambda: (_ for _ in ()).throw(
            AssertionError("invalid input must not load configuration")
        ),
        get_dockerpilot=lambda: (_ for _ in ()).throw(
            AssertionError("invalid input must not touch Docker")
        ),
        execute_command_via_ssh=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not use SSH")
        ),
        execute_docker_command_via_ssh=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not use SSH")
        ),
        save_deployment_config=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid input must not save configuration")
        ),
        infer_port_mapping_for_host_network=lambda *_args, **_kwargs: {},
    )

    assert resource().post() == (
        {"error": "container_name and target_server_id are required"},
        400,
    )


def _promotion_resource(monkeypatch, migration_result, *, binding_error=None):
    request = _Request(
        {
            "from_env": "dev",
            "to_env": "prod",
            "container_name": "api",
            "include_data": True,
            "stop_source": False,
        }
    )
    runner_calls = []
    moved = []

    class _Runner:
        def run_inline(self, spec):
            runner_calls.append(spec)
            return migration_result

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("backend.resources.promotion.threading.Thread", _NoopThread)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def move_binding(*args):
        if binding_error is not None:
            raise binding_error
        moved.append(args)

    resources = create_promotion_resources(
        Resource=_Resource,
        app=_App(),
        request=request,
        session={},
        datetime_cls=datetime,
        deployment_progress={},
        get_dockerpilot=lambda: SimpleNamespace(_sudo_password=None),
        consume_elevation_token=lambda *_args, **_kwargs: (False, "unused", None),
        find_all_deployment_configs_for_env=lambda _env: [],
        resolve_server_id_for_env=lambda env: {"dev": "source-1", "prod": "target-1"}[env],
        promote_config_to_server=lambda *_args, **_kwargs: True,
        move_many_container_bindings=lambda *_args, **_kwargs: None,
        move_container_binding=move_binding,
        format_env_name=lambda env: env,
        find_active_deployment_dir=lambda _name: None,
        migration_runner=_Runner(),
    )
    return resources[-1], runner_calls, moved


def test_cross_server_promotion_waits_for_runner_success_before_moving_binding(monkeypatch):
    resource, runner_calls, moved = _promotion_resource(
        monkeypatch,
        MigrationResult({"success": True, "message": "migrated"}, 200, False),
    )

    body = resource().post()

    assert body["success"] is True
    assert runner_calls == [MigrationSpec("api", "source-1", "target-1", True, False)]
    assert moved == [("api", "dev", "prod")]


def test_cross_server_promotion_does_not_treat_accepted_job_as_completed(monkeypatch):
    resource, runner_calls, moved = _promotion_resource(
        monkeypatch,
        MigrationResult({"migration_id": "job-1"}, 202, True),
    )

    body, status = resource().post()

    assert status == 500
    assert body["success"] is False
    assert "not completed" in body["error"].lower()
    assert len(runner_calls) == 1
    assert moved == []


def test_cross_server_promotion_reports_binding_failure_as_partial(monkeypatch):
    resource, runner_calls, moved = _promotion_resource(
        monkeypatch,
        MigrationResult({"success": True, "message": "migrated"}, 200, False),
        binding_error=RuntimeError("binding storage unavailable"),
    )

    body, status = resource().post()

    assert status == 500
    assert body["success"] is False
    assert body["partial"] is True
    assert body["reconciliation_required"] is True
    assert len(runner_calls) == 1
    assert moved == []
