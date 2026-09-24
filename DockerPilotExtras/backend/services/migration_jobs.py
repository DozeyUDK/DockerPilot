"""Thread-safe, in-memory registry for container migration jobs.

The registry deliberately has no knowledge of Flask, Docker, or workers.  A
caller reserves a container before starting work and subsequently uses the job
id to publish progress.  This makes the registry safe to use from request and
worker threads while keeping its lifecycle explicit.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Deque, Dict, Iterable, Optional, Union
from uuid import UUID, uuid4


JobId = Union[str, UUID]
Clock = Callable[[], datetime]
IdFactory = Callable[[], JobId]

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
NONTERMINAL_STATUSES = frozenset({"queued", "running", "cancelling", "finalizing"})
RESERVABLE_STATUSES = frozenset({"queued", "running"})


class MigrationJobError(RuntimeError):
    """Base class for registry errors."""


class MigrationJobConflict(MigrationJobError):
    """Raised when a container already has a reserved migration job."""

    def __init__(self, container_name: str, job_id: str) -> None:
        self.container_name = container_name
        self.job_id = job_id
        super().__init__(
            f"Container {container_name!r} is already reserved by migration job {job_id!r}"
        )


class MigrationJobNotFound(MigrationJobError):
    """Raised when a job id no longer exists."""

    def __init__(self, job_id: JobId) -> None:
        self.job_id = str(job_id)
        super().__init__(f"Migration job {self.job_id!r} was not found")


class MigrationJobCapacityExceeded(MigrationJobError):
    """Raised when retaining another job would exceed the configured capacity."""


class InvalidMigrationJobTransition(MigrationJobError):
    """Raised when an operation would alter a terminal job."""


class MigrationCancellationTooLate(MigrationJobError):
    """Raised when cancellation races with an already-started finalization."""


@dataclass
class _Job:
    job_id: str
    container_name: str
    status: str
    stage: str
    progress: float
    message: Optional[str]
    metadata: Dict[str, Any]
    cancel_requested: bool
    finalizing: bool
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None
    events: Deque[Dict[str, Any]] = field(default_factory=deque)
    dropped_event_count: int = 0


class MigrationJobRegistry:
    """Own and expose short-lived migration-job state.

    All public methods return independent ``dict`` snapshots.  Mutating a
    returned snapshot (including nested metadata or events) can therefore
    never mutate registry state.  A terminal job releases its active container
    reservation immediately, while remaining available as that container's
    latest job until it is explicitly released or its ``terminal_ttl`` expires.
    Jobs still running are never expired by the registry.
    """

    def __init__(
        self,
        *,
        id_factory: IdFactory = uuid4,
        clock: Optional[Clock] = None,
        max_events: int = 300,
        max_jobs: int = 1_000,
        terminal_ttl: Optional[timedelta] = timedelta(minutes=5),
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        if terminal_ttl is not None and terminal_ttl.total_seconds() < 0:
            raise ValueError("terminal_ttl cannot be negative")

        self._id_factory = id_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_events = max_events
        self._max_jobs = max_jobs
        self._terminal_ttl = terminal_ttl
        self._lock = RLock()
        self._jobs: Dict[str, _Job] = {}
        self._active_for_container: Dict[str, _Job] = {}
        self._latest_for_container: Dict[str, _Job] = {}
        self._next_seq = 0

    def reserve(
        self,
        container_name: str,
        *,
        status: str = "running",
        stage: str = "initializing",
        progress: float = 0,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Atomically reserve ``container_name`` and create its job.

        The injected ``id_factory`` must produce globally unique IDs; the
        production default is ``uuid4``. Request correlation belongs in
        metadata and must not replace the migration identity. Reservation
        conflicts are typed for deterministic API responses.
        """
        self._validate_container_name(container_name)
        self._validate_nonterminal_status(status)
        self._validate_stage(stage)
        self._validate_progress(progress)
        actual_id = self._normalise_id(self._id_factory())

        with self._lock:
            now = self._now()
            self._prune_locked(now)
            existing = self._active_for_container.get(container_name)
            if existing is not None:
                raise MigrationJobConflict(container_name, existing.job_id)
            if actual_id in self._jobs:
                raise ValueError(f"Migration job id {actual_id!r} is already in use")
            if len(self._jobs) >= self._max_jobs:
                raise MigrationJobCapacityExceeded(
                    f"Migration job capacity ({self._max_jobs}) has been reached"
                )

            job = _Job(
                job_id=actual_id,
                container_name=container_name,
                status=status,
                stage=stage,
                progress=float(progress),
                message=message,
                metadata=deepcopy(metadata) if metadata is not None else {},
                cancel_requested=False,
                finalizing=False,
                created_at=now,
                updated_at=now,
                events=deque(maxlen=self._max_events),
            )
            self._jobs[actual_id] = job
            self._active_for_container[container_name] = job
            self._latest_for_container[container_name] = job
            self._append_event_locked(job, now, "reserved")
            return self._snapshot_locked(job)

    def snapshot(self, job_id: JobId) -> Dict[str, Any]:
        """Return a stable, deep-copied snapshot for a job."""
        with self._lock:
            self._prune_locked(self._now())
            return self._snapshot_locked(self._job_locked(job_id))

    get = snapshot

    def snapshot_for_container(self, container_name: str) -> Optional[Dict[str, Any]]:
        """Return the current container job, or ``None`` when it is absent."""
        with self._lock:
            self._prune_locked(self._now())
            job = self._latest_for_container.get(container_name)
            return self._snapshot_locked(job) if job is not None else None

    def snapshots(self, *, active_only: bool = False) -> Iterable[Dict[str, Any]]:
        """Return independent snapshots in creation order."""
        with self._lock:
            self._prune_locked(self._now())
            jobs = self._jobs.values()
            if active_only:
                jobs = (job for job in jobs if job.status not in TERMINAL_STATUSES)
            return [self._snapshot_locked(job) for job in jobs]

    def update(
        self,
        job_id: JobId,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Publish non-terminal job state, recording one event if it changed."""
        if status is not None:
            self._validate_status(status)
        if stage is not None:
            self._validate_stage(stage)
        if progress is not None:
            self._validate_progress(progress)

        with self._lock:
            now = self._now()
            self._prune_locked(now)
            job = self._job_locked(job_id)
            if job.status in TERMINAL_STATUSES:
                if any(value is not None for value in (status, stage, progress, message, metadata)):
                    raise InvalidMigrationJobTransition(
                        f"Terminal migration job {job.job_id!r} cannot be updated"
                    )
                return self._snapshot_locked(job)
            if status in TERMINAL_STATUSES:
                return self._finish_locked(job, status, stage, progress, message, metadata, now)
            if status in {"cancelling", "finalizing"}:
                raise InvalidMigrationJobTransition(
                    "Use request_cancel() or begin_finalization() for lifecycle boundaries"
                )
            if job.status == "running" and status == "queued":
                raise InvalidMigrationJobTransition(
                    f"Running migration job {job.job_id!r} cannot return to queued"
                )
            if job.cancel_requested and status not in (None, "cancelling"):
                raise InvalidMigrationJobTransition(
                    f"Cancelling migration job {job.job_id!r} cannot return to {status!r}"
                )
            if job.finalizing and status not in (None, "finalizing"):
                raise InvalidMigrationJobTransition(
                    f"Finalizing migration job {job.job_id!r} cannot return to {status!r}"
                )

            changed = False
            for attr, value in (
                ("status", status),
                ("stage", stage),
                ("progress", float(progress) if progress is not None else None),
                ("message", message),
            ):
                if value is not None and getattr(job, attr) != value:
                    setattr(job, attr, value)
                    changed = True
            if metadata is not None and job.metadata != metadata:
                job.metadata = deepcopy(metadata)
                changed = True
            if changed:
                job.updated_at = now
                self._append_event_locked(job, now, "updated")
            return self._snapshot_locked(job)

    def finish(
        self,
        job_id: JobId,
        status: str,
        *,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transition a job once to ``completed``, ``failed``, or ``cancelled``."""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"status must be terminal, got {status!r}")
        if stage is not None:
            self._validate_stage(stage)
        if progress is not None:
            self._validate_progress(progress)
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            job = self._job_locked(job_id)
            if job.status in TERMINAL_STATUSES:
                if job.status == status:
                    return self._snapshot_locked(job)
                raise InvalidMigrationJobTransition(
                    f"Migration job {job.job_id!r} is already terminal ({job.status})"
                )
            return self._finish_locked(job, status, stage, progress, message, metadata, now)

    def request_cancel(self, job_id: JobId, *, message: Optional[str] = None) -> Dict[str, Any]:
        """Mark a job for cooperative cancellation; repeated calls are no-ops."""
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            job = self._job_locked(job_id)
            if job.status in TERMINAL_STATUSES or job.cancel_requested:
                return self._snapshot_locked(job)
            if job.finalizing:
                raise MigrationCancellationTooLate(
                    f"Migration job {job.job_id!r} is already finalizing"
                )
            job.cancel_requested = True
            job.status = "cancelling"
            if message is not None:
                job.message = message
            job.updated_at = now
            self._append_event_locked(job, now, "cancel_requested")
            return self._snapshot_locked(job)

    def begin_finalization(self, job_id: JobId) -> Optional[Dict[str, Any]]:
        """Atomically cross the cancellation boundary.

        ``None`` means cancellation was already requested and the worker must
        finish as cancelled instead of committing final side effects.
        """
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            job = self._job_locked(job_id)
            if job.status in TERMINAL_STATUSES:
                return None
            if job.finalizing:
                return self._snapshot_locked(job)
            if job.cancel_requested:
                return None
            job.finalizing = True
            job.status = "finalizing"
            job.updated_at = now
            self._append_event_locked(job, now, "finalization_started")
            return self._snapshot_locked(job)

    def release(self, job_id: JobId) -> bool:
        """Remove one job and its reservation, without releasing a newer job.

        The container mapping is removed only when it still points to the exact
        same internal job object.  Consequently a delayed release for a stale
        job can never erase a newer reservation for the same container.
        """
        normalised_id = self._normalise_id(job_id)
        with self._lock:
            job = self._jobs.get(normalised_id)
            if job is None:
                return False
            if self._active_for_container.get(job.container_name) is job:
                del self._active_for_container[job.container_name]
            if self._latest_for_container.get(job.container_name) is job:
                del self._latest_for_container[job.container_name]
            if self._jobs.get(normalised_id) is job:
                del self._jobs[normalised_id]
            return True

    def prune_expired(self) -> int:
        """Lazily discard expired terminal jobs and return the removal count."""
        with self._lock:
            return self._prune_locked(self._now())

    def _finish_locked(
        self,
        job: _Job,
        status: str,
        stage: Optional[str],
        progress: Optional[float],
        message: Optional[str],
        metadata: Optional[Dict[str, Any]],
        now: datetime,
    ) -> Dict[str, Any]:
        job.status = status
        if stage is not None:
            job.stage = stage
        if progress is not None:
            job.progress = float(progress)
        if message is not None:
            job.message = message
        if metadata is not None:
            job.metadata = deepcopy(metadata)
        job.updated_at = now
        job.finished_at = now
        if self._active_for_container.get(job.container_name) is job:
            del self._active_for_container[job.container_name]
        self._append_event_locked(job, now, "finished")
        return self._snapshot_locked(job)

    def _append_event_locked(self, job: _Job, timestamp: datetime, event_type: str) -> None:
        self._next_seq += 1
        if len(job.events) == self._max_events:
            job.dropped_event_count += 1
        job.events.append(
            {
                "seq": self._next_seq,
                "type": event_type,
                "timestamp": self._timestamp(timestamp),
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "message": job.message,
            }
        )

    def _snapshot_locked(self, job: _Job) -> Dict[str, Any]:
        return deepcopy(
            {
                "id": job.job_id,
                "container_name": job.container_name,
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "message": job.message,
                "cancel_requested": job.cancel_requested,
                "finalizing": job.finalizing,
                "metadata": job.metadata,
                "created_at": self._timestamp(job.created_at),
                "updated_at": self._timestamp(job.updated_at),
                "finished_at": self._timestamp(job.finished_at) if job.finished_at else None,
                "events": list(job.events),
                "dropped_event_count": job.dropped_event_count,
            }
        )

    def _prune_locked(self, now: datetime) -> int:
        if self._terminal_ttl is None:
            return 0
        stale = [
            job
            for job in self._jobs.values()
            if job.status in TERMINAL_STATUSES
            and job.finished_at is not None
            and now - job.finished_at >= self._terminal_ttl
        ]
        for job in stale:
            if self._active_for_container.get(job.container_name) is job:
                del self._active_for_container[job.container_name]
            if self._latest_for_container.get(job.container_name) is job:
                del self._latest_for_container[job.container_name]
            if self._jobs.get(job.job_id) is job:
                del self._jobs[job.job_id]
        return len(stale)

    def _job_locked(self, job_id: JobId) -> _Job:
        normalised_id = self._normalise_id(job_id)
        job = self._jobs.get(normalised_id)
        if job is None:
            raise MigrationJobNotFound(normalised_id)
        return job

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("clock must return datetime")
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _normalise_id(job_id: JobId) -> str:
        value = str(job_id)
        if not value:
            raise ValueError("job id cannot be empty")
        return value

    @staticmethod
    def _validate_container_name(container_name: str) -> None:
        if not isinstance(container_name, str) or not container_name:
            raise ValueError("container_name must be a non-empty string")

    @staticmethod
    def _validate_status(status: str) -> None:
        if not isinstance(status, str) or not status:
            raise ValueError("status must be a non-empty string")
        if status not in NONTERMINAL_STATUSES and status not in TERMINAL_STATUSES:
            raise ValueError(f"unsupported migration job status: {status!r}")

    @classmethod
    def _validate_nonterminal_status(cls, status: str) -> None:
        cls._validate_status(status)
        if status not in RESERVABLE_STATUSES:
            raise ValueError("reserve() status must be 'queued' or 'running'")

    @staticmethod
    def _validate_stage(stage: str) -> None:
        if not isinstance(stage, str) or not stage:
            raise ValueError("stage must be a non-empty string")

    @staticmethod
    def _validate_progress(progress: float) -> None:
        if not isinstance(progress, (int, float)) or isinstance(progress, bool):
            raise ValueError("progress must be a number between 0 and 100")
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
