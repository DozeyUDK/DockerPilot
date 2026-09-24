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
from backend.services.migration_jobs import MigrationJobRegistry
from backend.services.migration_runner import (
    MigrationResult,
    MigrationSpec,
    SynchronousMigrationRunner,
)
from backend.services.migration_service import MigrationService


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


def test_runner_activates_and_closes_execution_context_at_executor_boundary():
    events = []

    class _ExecutionContext:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")

    def operation(_payload):
        events.append("execute")
        return {"success": True}

    result = SynchronousMigrationRunner(operation).run_inline(
        MigrationSpec("api", "dev", "prod"),
        execution_context=_ExecutionContext(),
    )

    assert result.completed_successfully is True
    assert events == ["enter", "execute", "exit"]


def test_runner_closes_execution_context_when_executor_raises():
    events = []

    class _ExecutionContext:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, *_args):
            events.append(("exit", exc_type))

    def operation(_payload):
        events.append("execute")
        raise RuntimeError("executor failed")

    result = SynchronousMigrationRunner(operation).run_inline(
        MigrationSpec("api", "dev", "prod"),
        execution_context=_ExecutionContext(),
    )

    assert result.status == 500
    assert events == ["enter", "execute", ("exit", RuntimeError)]


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
        def run_inline(
            self,
            spec,
            *,
            execution_context=None,
            on_reserved=None,
        ):
            runner_calls.append(spec)
            if migration_result.status not in {409, 503} and on_reserved is not None:
                on_reserved()
            if execution_context is None:
                return migration_result
            with execution_context:
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

    deployment_progress = {}
    resources = create_promotion_resources(
        Resource=_Resource,
        app=_App(),
        request=request,
        session={},
        datetime_cls=datetime,
        deployment_progress=deployment_progress,
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
    return resources[-1], runner_calls, moved, deployment_progress


def test_cross_server_promotion_waits_for_runner_success_before_moving_binding(monkeypatch):
    resource, runner_calls, moved, _progress = _promotion_resource(
        monkeypatch,
        MigrationResult({"success": True, "message": "migrated"}, 200, False),
    )

    body = resource().post()

    assert body["success"] is True
    assert runner_calls == [MigrationSpec("api", "source-1", "target-1", True, False)]
    assert moved == [("api", "dev", "prod")]


def test_cross_server_promotion_does_not_treat_accepted_job_as_completed(monkeypatch):
    resource, runner_calls, moved, _progress = _promotion_resource(
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
    resource, runner_calls, moved, _progress = _promotion_resource(
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


@pytest.mark.parametrize("status", [409, 503])
def test_cross_server_promotion_preserves_retryable_status_before_progress_starts(
    monkeypatch,
    status,
):
    headers = ({"Retry-After": "1"},) if status == 503 else ()
    resource, runner_calls, moved, progress = _promotion_resource(
        monkeypatch,
        MigrationResult(
            {"error": "busy", "code": "migration_conflict"},
            status,
            True,
            headers,
        ),
    )

    response = resource().post()

    assert response[1] == status
    if status == 503:
        assert response[2] == {"Retry-After": "1"}
    assert len(runner_calls) == 1
    assert moved == []
    assert progress == {}


@pytest.mark.parametrize("promotion_outcome", [True, False, RuntimeError("deploy failed")])
def test_same_server_promotion_scopes_execution_context_and_moves_only_on_success(
    monkeypatch,
    tmp_path,
    promotion_outcome,
):
    (tmp_path / "deployment-dev.yml").write_text("deployment: {}\n", encoding="utf-8")
    events = []
    moved = []

    class _ExecutionContext:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, *_args):
            events.append(("exit", exc_type))

    def promote(*_args, **_kwargs):
        events.append("promote")
        if isinstance(promotion_outcome, Exception):
            raise promotion_outcome
        return promotion_outcome

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("backend.resources.promotion.threading.Thread", _NoopThread)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    request = _Request(
        {"from_env": "dev", "to_env": "prod", "container_name": "api"}
    )
    registry = MigrationJobRegistry(terminal_ttl=None)
    migration_service = MigrationService(
        SynchronousMigrationRunner(
            lambda _payload: (_ for _ in ()).throw(
                AssertionError("same-server promotion must use run_operation")
            )
        ),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    resources = create_promotion_resources(
        Resource=_Resource,
        app=_App(),
        request=request,
        session={},
        datetime_cls=datetime,
        deployment_progress={},
        get_dockerpilot=lambda: SimpleNamespace(),
        consume_elevation_token=lambda *_args, **_kwargs: (False, "unused", None),
        find_all_deployment_configs_for_env=lambda _env: [],
        resolve_server_id_for_env=lambda _env: "local",
        promote_config_to_server=promote,
        move_many_container_bindings=lambda *_args, **_kwargs: None,
        move_container_binding=lambda *args: moved.append(args),
        format_env_name=lambda env: env,
        find_active_deployment_dir=lambda _name: tmp_path,
        migration_runner=migration_service,
        execution_context_factory=lambda *_args, **_kwargs: _ExecutionContext(),
    )

    response = resources[-1]().post()

    expected_error = RuntimeError if isinstance(promotion_outcome, Exception) else None
    assert events == ["enter", "promote", ("exit", expected_error)]
    assert registry.snapshot_for_container("api")["status"] == (
        "completed" if promotion_outcome is True else "failed"
    )
    if promotion_outcome is True:
        assert response["success"] is True
        assert moved == [("api", "dev", "prod")]
    else:
        body, status = response
        assert status == 500
        assert body.get("success") is not True
        if promotion_outcome is False:
            assert body["error"] == "Failed to promote api"
        assert moved == []


def test_same_server_promotion_conflicts_before_context_or_filesystem_access(monkeypatch):
    events = []

    class _ExecutionContext:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *_args):
            events.append("exit")

    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("backend.resources.promotion.threading.Thread", _NoopThread)
    request = _Request(
        {"from_env": "dev", "to_env": "prod", "container_name": "api"}
    )
    registry = MigrationJobRegistry(terminal_ttl=None)
    active = registry.reserve("api", metadata={"mode": "async"})
    active_progress = {
        "stage": "deploying",
        "progress": 55,
        "message": "First promotion is still running",
    }
    deployment_progress = {"api": active_progress.copy()}
    migration_service = MigrationService(
        SynchronousMigrationRunner(lambda _payload: {"success": True}),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    resources = create_promotion_resources(
        Resource=_Resource,
        app=_App(),
        request=request,
        session={},
        datetime_cls=datetime,
        deployment_progress=deployment_progress,
        get_dockerpilot=lambda: SimpleNamespace(),
        consume_elevation_token=lambda *_args, **_kwargs: (False, "unused", None),
        find_all_deployment_configs_for_env=lambda _env: [],
        resolve_server_id_for_env=lambda _env: "local",
        promote_config_to_server=lambda *_args, **_kwargs: events.append("promote"),
        move_many_container_bindings=lambda *_args, **_kwargs: None,
        move_container_binding=lambda *_args, **_kwargs: events.append("move"),
        format_env_name=lambda env: env,
        find_active_deployment_dir=lambda _name: events.append("find-config"),
        migration_runner=migration_service,
        execution_context_factory=lambda *_args, **_kwargs: _ExecutionContext(),
    )

    body, status = resources[-1]().post()

    assert status == 409
    assert body["code"] == "migration_conflict"
    assert body["migration_id"] == active["id"]
    assert events == []
    assert deployment_progress == {"api": active_progress}
