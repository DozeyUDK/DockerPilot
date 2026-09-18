"""Contract tests for bounded asynchronous container migrations."""

from __future__ import annotations

from pathlib import Path
import importlib.util
import sys

import pytest


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.migration_jobs import MigrationJobRegistry
from backend.services.migration_runner import MigrationResult, MigrationSpec
from backend.services.migration_service import (
    MigrationQueueFull,
    MigrationService,
)

_ASYNC_RESOURCE_SPEC = importlib.util.spec_from_file_location(
    "dockerpilot_extras_migration_async_for_tests",
    EXTRAS_DIR / "backend" / "resources" / "migration_async.py",
)
assert _ASYNC_RESOURCE_SPEC is not None and _ASYNC_RESOURCE_SPEC.loader is not None
_ASYNC_RESOURCE_MODULE = importlib.util.module_from_spec(_ASYNC_RESOURCE_SPEC)
_ASYNC_RESOURCE_SPEC.loader.exec_module(_ASYNC_RESOURCE_MODULE)
create_async_migration_resources = _ASYNC_RESOURCE_MODULE.create_async_migration_resources


class _Ids:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"job-{self._value}"


class _Runner:
    def __init__(self, *results: MigrationResult) -> None:
        self.results = list(results)
        self.calls = []

    def run_inline(self, spec, *, execution_context=None, operation_context=None):
        self.calls.append(spec)
        result = self.results.pop(0)
        if result.completed_successfully:
            operation_context.begin_finalization()
        return result


def _service(*results, queue_capacity=2, cancel_hook=None):
    registry = MigrationJobRegistry(id_factory=_Ids(), terminal_ttl=None)
    runner = _Runner(*results)
    service = MigrationService(
        runner,
        registry=registry,
        queue_capacity=queue_capacity,
        auto_start=False,
        start_on_submit=False,
        cancel_hook=cancel_hook,
    )
    return service, registry, runner


def test_submit_is_non_blocking_and_worker_finishes_success_without_storing_body():
    secret = "must-not-enter-registry"
    service, registry, runner = _service(
        MigrationResult({"success": True, "token": secret}, 200)
    )

    accepted = service.submit(MigrationSpec("api", "dev", "prod", True, False))

    assert accepted["status"] == "queued"
    assert accepted["metadata"] == {
        "mode": "async",
        "source_server_id": "dev",
        "target_server_id": "prod",
        "include_data": True,
        "stop_source": False,
    }
    assert runner.calls == []

    completed = service.execute_next()

    assert runner.calls == [MigrationSpec("api", "dev", "prod", True, False)]
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["metadata"]["http_status"] == 200
    assert secret not in repr(registry.snapshot(completed["id"]))


def test_failed_result_is_terminal_and_only_records_bounded_safe_metadata():
    service, registry, _runner = _service(
        MigrationResult(
            {"error": "docker failed --env API_TOKEN=top-secret", "debug": "raw-output"},
            500,
            True,
        )
    )
    job = service.submit(MigrationSpec("api", "dev", "prod"))

    failed = service.execute_next()

    assert failed["status"] == "failed"
    assert failed["metadata"]["http_status"] == 500
    assert "error" not in failed["metadata"]
    assert "top-secret" not in repr(registry.snapshot(job["id"]))
    assert "raw-output" not in repr(registry.snapshot(job["id"]))


def test_queue_capacity_is_bounded_and_failed_submission_releases_reservation():
    service, registry, _runner = _service(
        MigrationResult({"success": True}, 200), queue_capacity=1
    )
    service.submit(MigrationSpec("api", "dev", "prod"))

    with pytest.raises(MigrationQueueFull):
        service.submit(MigrationSpec("worker", "dev", "prod"))

    assert registry.snapshot_for_container("worker") is None


def test_queued_cancel_never_invokes_runner_and_finishes_cancelled():
    cancelled = []
    service, _registry, runner = _service(
        MigrationResult({"success": True}, 200),
        cancel_hook=lambda job: cancelled.append(job["container_name"]),
    )
    job = service.submit(MigrationSpec("api", "dev", "prod"))

    cancelling = service.cancel(job["id"])
    finished = service.execute_next()

    assert cancelling["status"] == "cancelling"
    assert finished["status"] == "cancelled"
    assert runner.calls == []
    assert cancelled == ["api"]


def test_cancel_wins_before_atomic_finalization_boundary():
    registry = MigrationJobRegistry(id_factory=_Ids(), terminal_ttl=None)
    job_id = None

    class _CancellingRunner:
        def run_inline(self, _spec, *, execution_context=None, operation_context=None):
            registry.request_cancel(job_id)
            operation_context.begin_finalization()
            return MigrationResult({"success": True}, 200)

    service = MigrationService(
        _CancellingRunner(),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    job_id = service.submit(MigrationSpec("api", "dev", "prod"))["id"]

    result = service.execute_next()

    assert result["status"] == "cancelled"
    assert result["finalizing"] is False


def test_target_replacement_happens_only_after_finalization_boundary():
    source = (
        EXTRAS_DIR / "backend" / "resources" / "migration.py"
    ).read_text(encoding="utf-8")
    preparation = source.index("# Step 4: Check if container exists on target")
    gate = source.index("operation_context.begin_finalization()", preparation)
    target_stop = source.index('f"stop {container_name}"', preparation)
    mount_copy = source.index("self._migrate_mount_data_between_servers(", preparation)

    assert gate < target_stop
    assert gate < mount_copy


def test_shutdown_cancels_pending_work_and_is_idempotent():
    service, registry, runner = _service(MigrationResult({"success": True}, 200))
    job = service.submit(MigrationSpec("api", "dev", "prod"))

    service.shutdown(cancel_pending=True)
    service.shutdown(cancel_pending=True)

    assert registry.snapshot(job["id"])["status"] == "cancelled"
    assert runner.calls == []
    with pytest.raises(RuntimeError, match="shut down"):
        service.submit(MigrationSpec("worker", "dev", "prod"))


def test_service_rejects_more_than_one_worker():
    with pytest.raises(ValueError, match="exactly one worker"):
        MigrationService(_Runner(), max_workers=2)


def test_operation_context_publishes_progress_and_events():
    class _ProgressRunner:
        def run_inline(self, _spec, *, execution_context=None, operation_context=None):
            operation_context.update_progress("copying", 42, "Copying image")
            operation_context.begin_finalization()
            operation_context.update_progress("replacing", 90, "Replacing target")
            return MigrationResult({"success": True}, 200)

    registry = MigrationJobRegistry(id_factory=_Ids(), terminal_ttl=None)
    service = MigrationService(
        _ProgressRunner(),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    job = service.submit(MigrationSpec("api", "dev", "prod"))

    completed = service.execute_next()

    copying_events = [
        event
        for event in completed["events"]
        if event["stage"] == "copying" and event["progress"] == 42
    ]
    finalizing_events = [
        event
        for event in completed["events"]
        if event["stage"] == "replacing" and event["progress"] == 90
    ]
    assert copying_events
    assert finalizing_events
    assert registry.snapshot(job["id"])["status"] == "completed"


def test_success_without_finalization_gate_is_failed_closed():
    class _UngatedRunner:
        def run_inline(self, _spec, *, execution_context=None, operation_context=None):
            return MigrationResult({"success": True}, 200)

    registry = MigrationJobRegistry(id_factory=_Ids(), terminal_ttl=None)
    service = MigrationService(
        _UngatedRunner(),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    service.submit(MigrationSpec("api", "dev", "prod"))

    failed = service.execute_next()

    assert failed["status"] == "failed"
    assert failed["message"] == "Migration finalization invariant failed"


def test_unexpected_runner_failure_terminalizes_job():
    class _ExplodingRunner:
        def run_inline(self, _spec, *, execution_context=None, operation_context=None):
            raise RuntimeError("adapter exploded")

    registry = MigrationJobRegistry(id_factory=_Ids(), terminal_ttl=None)
    service = MigrationService(
        _ExplodingRunner(),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )
    job = service.submit(MigrationSpec("api", "dev", "prod"))

    failed = service.execute_next()

    assert failed["status"] == "failed"
    assert registry.snapshot_for_container("api")["id"] == job["id"]
    assert registry.snapshot_for_container("api")["status"] == "failed"


def test_inline_and_async_paths_share_container_reservation_without_leaking_inline_id():
    service, registry, runner = _service(MigrationResult({"success": True}, 200))
    async_job = service.submit(MigrationSpec("api", "dev", "prod"))

    inline_conflict = service.run_inline(MigrationSpec("api", "dev", "prod"))

    assert inline_conflict.status == 409
    assert inline_conflict.body["migration_id"] == async_job["id"]
    assert runner.calls == []

    registry.release(async_job["id"])
    inline_job = registry.reserve("api", metadata={"mode": "inline"})
    request = _Request()
    request.payload = {
        "container_name": "api",
        "source_server_id": "dev",
        "target_server_id": "prod",
    }
    Collection, Job = _async_resources(service, request)

    conflict_body, conflict_status = Collection().post()
    hidden_body, hidden_status = Job().get(inline_job["id"])

    assert conflict_status == 409
    assert "migration_id" not in conflict_body
    assert hidden_status == 404
    assert hidden_body["code"] == "migration_not_found"


def test_inline_path_reports_registry_capacity_as_retryable_service_unavailable():
    registry = MigrationJobRegistry(
        id_factory=_Ids(),
        max_jobs=1,
        terminal_ttl=None,
    )
    registry.reserve("occupied", metadata={"mode": "async"})
    service = MigrationService(
        _Runner(),
        registry=registry,
        auto_start=False,
        start_on_submit=False,
    )

    result = service.run_inline(MigrationSpec("api", "dev", "prod"))

    assert result.status == 503
    assert result.body["code"] == "migration_capacity_exceeded"
    assert result.response_tail == ({"Retry-After": "1"},)


class _Resource:
    pass


class _Request:
    def __init__(self) -> None:
        self.args = {}
        self.payload = None

    def get_json(self):
        return self.payload


class _Logger:
    def error(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass


class _App:
    logger = _Logger()


def _async_resources(service, request):
    return create_async_migration_resources(
        Resource=_Resource,
        request=request,
        migration_service=service,
    )


def test_additive_api_accepts_and_gets_job_by_id():
    service, _registry, _runner = _service(MigrationResult({"success": True}, 200))
    request = _Request()
    request.payload = {
        "container_name": "api",
        "source_server_id": "dev",
        "target_server_id": "prod",
    }
    Collection, Job = _async_resources(service, request)

    accepted, accepted_status, accepted_headers = Collection().post()
    fetched = Job().get(accepted["migration_id"])

    assert accepted_status == 202
    assert accepted_headers["Location"] == accepted["status_url"]
    assert accepted["job"]["status"] == "queued"
    assert fetched["job"]["id"] == accepted["migration_id"]


def test_job_api_returns_deterministic_not_found_and_cancel_by_id():
    service, registry, _runner = _service(MigrationResult({"success": True}, 200))
    job = service.submit(MigrationSpec("api", "dev", "prod"))
    request = _Request()
    _Collection, Job = _async_resources(service, request)

    missing_body, missing_status = Job().get("missing")
    cancel_body, cancel_status = Job().delete(job["id"])

    assert missing_status == 404
    assert missing_body["code"] == "migration_not_found"
    assert cancel_status == 202
    assert cancel_body["job"]["status"] == "cancelling"
    assert registry.snapshot(job["id"])["cancel_requested"] is True


def test_api_reports_capacity_with_retry_after_header():
    service, _registry, _runner = _service(
        MigrationResult({"success": True}, 200), queue_capacity=1
    )
    service.submit(MigrationSpec("api", "dev", "prod"))
    request = _Request()
    request.payload = {
        "container_name": "worker",
        "source_server_id": "dev",
        "target_server_id": "prod",
    }
    Collection, _Job = _async_resources(service, request)

    body, status, headers = Collection().post()

    assert status == 503
    assert headers["Retry-After"] == "1"
    assert body["code"] == "migration_capacity_exceeded"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("include_data", "false"),
        ("include_data", []),
        ("stop_source", "false"),
        ("stop_source", {}),
    ],
)
def test_async_api_rejects_non_boolean_flags(field, value):
    service, _registry, runner = _service(MigrationResult({"success": True}, 200))
    request = _Request()
    request.payload = {
        "container_name": "api",
        "source_server_id": "dev",
        "target_server_id": "prod",
        field: value,
    }
    Collection, _Job = _async_resources(service, request)

    body, status = Collection().post()

    assert status == 400
    assert body["code"] == "invalid_migration_request"
    assert runner.calls == []


def test_job_api_rejects_late_cancel_and_is_idempotent_for_terminal_job():
    service, registry, _runner = _service(MigrationResult({"success": True}, 200))
    finalizing = service.submit(MigrationSpec("api", "dev", "prod"))
    registry.begin_finalization(finalizing["id"])
    request = _Request()
    _Collection, Job = _async_resources(service, request)

    late_body, late_status = Job().delete(finalizing["id"])

    assert late_status == 409
    assert late_body["code"] == "migration_cancellation_too_late"

    registry.finish(finalizing["id"], "completed")
    terminal_body, terminal_status = Job().delete(finalizing["id"])

    assert terminal_status == 200
    assert terminal_body["job"]["status"] == "completed"
