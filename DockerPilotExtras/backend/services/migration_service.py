"""Bounded, process-local execution service for container migrations.

The service deliberately uses an in-memory registry and queue.  It is suitable
for a single backend process only: jobs are not durable across restarts and
must not be expected to appear in another WSGI worker.  Deployments needing
multi-process or durable execution should replace this service with a shared
queue and job store while retaining the public lifecycle contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Callable, Optional

from backend.services.migration_jobs import (
    InvalidMigrationJobTransition,
    MigrationCancellationTooLate,
    MigrationJobCapacityExceeded,
    MigrationJobConflict,
    MigrationJobNotFound,
    MigrationJobRegistry,
)
from backend.services.migration_runner import MigrationResult, MigrationSpec


JobHook = Callable[[dict], None]
ExecutionContextFactory = Callable[[dict], Any]


class MigrationQueueFull(RuntimeError):
    """Raised when the bounded pending-work queue has no free slot."""


class MigrationCancelled(RuntimeError):
    """Raised at an executor checkpoint after cancellation was requested."""


@dataclass(frozen=True)
class _Task:
    job_id: str
    spec: MigrationSpec


class MigrationOperationContext:
    """Expose registry-backed progress and lifecycle gates to the executor."""

    def __init__(self, registry: MigrationJobRegistry, job_id: str) -> None:
        self._registry = registry
        self._job_id = job_id

    def update_progress(self, stage: str, progress: float, message: str) -> None:
        current = self._registry.snapshot(self._job_id)
        if current["cancel_requested"]:
            return
        self._registry.update(
            self._job_id,
            stage=stage,
            progress=progress,
            message=message,
        )

    def checkpoint(self) -> None:
        if self.cancelled():
            raise MigrationCancelled("Migration was cancelled by user")

    def cancelled(self) -> bool:
        return bool(self._registry.snapshot(self._job_id)["cancel_requested"])

    def begin_finalization(self) -> None:
        if self._registry.begin_finalization(self._job_id) is None:
            raise MigrationCancelled("Migration was cancelled by user")


class MigrationService:
    """Submit migrations to a bounded worker queue and publish safe state."""

    def __init__(
        self,
        runner,
        *,
        registry: Optional[MigrationJobRegistry] = None,
        max_workers: int = 1,
        queue_capacity: int = 2,
        auto_start: bool = False,
        start_on_submit: bool = True,
        start_hook: Optional[JobHook] = None,
        cancel_hook: Optional[JobHook] = None,
        execution_context_factory: Optional[ExecutionContextFactory] = None,
    ) -> None:
        if max_workers != 1:
            raise ValueError("Migration service requires exactly one worker")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")

        self.registry = registry or MigrationJobRegistry()
        self._runner = runner
        self._queue: Queue[_Task] = Queue(maxsize=queue_capacity)
        self._max_workers = max_workers
        self._start_on_submit = start_on_submit
        self._start_hook = start_hook
        self._cancel_hook = cancel_hook
        self._execution_context_factory = execution_context_factory
        self._stopping = Event()
        self._lifecycle_lock = Lock()
        self._threads: list[Thread] = []
        self._started = False
        self._closed = False
        if auto_start:
            self.start()

    def start(self) -> None:
        """Start worker threads once."""

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Migration service has been shut down")
            self._start_locked()

    def submit(self, spec: MigrationSpec) -> dict:
        """Reserve a container and enqueue its migration without blocking."""

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Migration service has been shut down")
            if self._start_on_submit:
                self._start_locked()

            # Registry metadata is intentionally allowlisted.  Never retain
            # executor responses, credentials, commands, or arbitrary input.
            job = self.registry.reserve(
                spec.container_name,
                status="queued",
                stage="queued",
                progress=0,
                message="Migration queued",
                metadata={
                    "mode": "async",
                    "source_server_id": spec.source_server_id,
                    "target_server_id": spec.target_server_id,
                    "include_data": bool(spec.include_data),
                    "stop_source": bool(spec.stop_source),
                },
            )
            try:
                self._queue.put_nowait(_Task(job["id"], spec))
            except Full as exc:
                self.registry.release(job["id"])
                raise MigrationQueueFull("Migration queue is full") from exc
        return job

    def _start_locked(self) -> None:
        if self._started:
            return
        self._started = True
        thread = Thread(
            target=self._worker_loop,
            name="migration-worker-1",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def get(self, job_id: str) -> dict:
        return self.registry.snapshot(job_id)

    def get_async(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["metadata"].get("mode") != "async":
            raise MigrationJobNotFound(job_id)
        return job

    def get_for_container(self, container_name: str) -> Optional[dict]:
        return self.registry.snapshot_for_container(container_name)

    def active(self) -> list[dict]:
        return list(self.registry.snapshots(active_only=True))

    def active_async(self) -> list[dict]:
        return [
            job
            for job in self.active()
            if job["metadata"].get("mode") == "async"
        ]

    def cancel(self, job_id: str) -> dict:
        """Request cooperative cancellation by stable migration id."""

        job = self.registry.request_cancel(
            job_id,
            message="Migration cancellation requested",
        )
        if self._cancel_hook is not None and job["status"] == "cancelling":
            self._cancel_hook(job)
        return job

    def cancel_async(self, job_id: str) -> dict:
        self.get_async(job_id)
        return self.cancel(job_id)

    def run_inline(
        self,
        spec: MigrationSpec,
        *,
        execution_context: Any = None,
        operation_context: Any = None,
    ) -> MigrationResult:
        """Serialize legacy/promotion work through the same reservation map."""

        if not isinstance(spec.container_name, str) or not spec.container_name:
            return self._runner.run_inline(
                spec,
                execution_context=execution_context,
                operation_context=operation_context,
            )
        try:
            job = self.registry.reserve(
                spec.container_name,
                status="running",
                stage="running",
                progress=0,
                message="Inline migration started",
                metadata={"mode": "inline"},
            )
        except MigrationJobConflict as exc:
            body = {
                "error": "A migration is already active for this container",
                "code": "migration_conflict",
            }
            try:
                self.get_async(exc.job_id)
            except MigrationJobNotFound:
                pass
            else:
                body["migration_id"] = exc.job_id
            return MigrationResult(
                body,
                status=409,
                explicit_status=True,
            )
        except MigrationJobCapacityExceeded:
            return MigrationResult(
                {
                    "error": "Migration capacity is currently exhausted",
                    "code": "migration_capacity_exceeded",
                },
                status=503,
                explicit_status=True,
                response_tail=({"Retry-After": "1"},),
            )

        try:
            result = self._runner.run_inline(
                spec,
                execution_context=execution_context,
                operation_context=operation_context,
            )
        except Exception:
            self.registry.finish(
                job["id"],
                "failed",
                stage="failed",
                message="Inline migration failed",
                metadata={"mode": "inline", "http_status": 500},
            )
            return MigrationResult(
                {"error": "Migration failed"},
                status=500,
                explicit_status=True,
            )
        metadata = {"mode": "inline", "http_status": result.status}
        self.registry.finish(
            job["id"],
            "completed" if result.completed_successfully else "failed",
            stage="completed" if result.completed_successfully else "failed",
            progress=100 if result.completed_successfully else 0,
            message=(
                "Inline migration completed"
                if result.completed_successfully
                else "Inline migration failed"
            ),
            metadata=metadata,
        )
        return result

    def execute_next(self) -> Optional[dict]:
        """Execute one queued task synchronously (deterministic test seam)."""

        try:
            task = self._queue.get_nowait()
        except Empty:
            return None
        try:
            return self._execute(task)
        except Exception:
            return self._terminalize_unexpected_failure(task.job_id)
        finally:
            self._queue.task_done()

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_pending: bool = True,
        timeout: float = 5.0,
    ) -> bool:
        """Stop accepting work and optionally cancel tasks not yet started."""

        with self._lifecycle_lock:
            if self._closed:
                return not any(thread.is_alive() for thread in self._threads)
            self._closed = True
            self._stopping.set()

        if cancel_pending:
            while True:
                try:
                    task = self._queue.get_nowait()
                except Empty:
                    break
                try:
                    self._finish_cancelled(task.job_id)
                finally:
                    self._queue.task_done()

            for job in self.active():
                if job["finalizing"]:
                    continue
                try:
                    self.cancel(job["id"])
                except (MigrationCancellationTooLate, InvalidMigrationJobTransition):
                    pass

        if wait:
            for thread in self._threads:
                thread.join(timeout=max(0.0, timeout))
        return not any(thread.is_alive() for thread in self._threads)

    def _worker_loop(self) -> None:
        while True:
            if self._stopping.is_set() and self._queue.empty():
                return
            try:
                task = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._execute(task)
            except Exception:
                # A worker must never leave a retained non-terminal job after
                # an unexpected adapter/hook failure.
                self._terminalize_unexpected_failure(task.job_id)
            finally:
                self._queue.task_done()

    def _execute(self, task: _Task) -> dict:
        current = self.registry.snapshot(task.job_id)
        if current["cancel_requested"]:
            return self._finish_cancelled(task.job_id)

        if self._start_hook is not None:
            self._start_hook(current)
        self.registry.update(
            task.job_id,
            status="running",
            stage="running",
            progress=1,
            message="Migration started",
        )
        operation_context = MigrationOperationContext(self.registry, task.job_id)
        execution_context = (
            self._execution_context_factory(current)
            if self._execution_context_factory is not None
            else None
        )
        result = self._runner.run_inline(
            task.spec,
            execution_context=execution_context,
            operation_context=operation_context,
        )

        finalizing = self.registry.snapshot(task.job_id)
        if finalizing["cancel_requested"] and not finalizing["finalizing"]:
            return self._finish_cancelled(task.job_id)

        metadata = dict(finalizing["metadata"])
        metadata["http_status"] = result.status
        if result.completed_successfully:
            if not finalizing["finalizing"]:
                return self.registry.finish(
                    task.job_id,
                    "failed",
                    stage="failed",
                    message="Migration finalization invariant failed",
                    metadata=metadata,
                )
            return self.registry.finish(
                task.job_id,
                "completed",
                stage="completed",
                progress=100,
                message="Migration completed successfully",
                metadata=metadata,
            )
        return self.registry.finish(
            task.job_id,
            "failed",
            stage="failed",
            message="Migration failed",
            metadata=metadata,
        )

    def _terminalize_unexpected_failure(self, job_id: str) -> dict:
        current = self.registry.snapshot(job_id)
        if current["status"] in {"completed", "failed", "cancelled"}:
            return current
        if current["cancel_requested"]:
            return self._finish_cancelled(job_id)
        return self.registry.finish(
            job_id,
            "failed",
            stage="failed",
            message="Migration failed",
        )

    def _finish_cancelled(self, job_id: str) -> dict:
        current = self.registry.snapshot(job_id)
        if current["status"] in {"completed", "failed", "cancelled"}:
            return current
        if current["status"] != "cancelling":
            try:
                self.registry.request_cancel(job_id)
            except MigrationCancellationTooLate:
                return self.registry.snapshot(job_id)
        return self.registry.finish(
            job_id,
            "cancelled",
            stage="cancelled",
            message="Migration cancelled",
        )
