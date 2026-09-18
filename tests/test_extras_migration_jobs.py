"""Deterministic unit tests for the standalone migration job registry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from threading import Barrier, Thread

import pytest


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.migration_jobs import (
    InvalidMigrationJobTransition,
    MigrationCancellationTooLate,
    MigrationJobCapacityExceeded,
    MigrationJobConflict,
    MigrationJobNotFound,
    MigrationJobRegistry,
)


class FakeClock:
    def __init__(self, instant: datetime | None = None) -> None:
        self.instant = instant or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.instant

    def advance(self, **delta: int) -> None:
        self.instant += timedelta(**delta)


class IdFactory:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"job-{self.value}"


class SequenceIdFactory:
    def __init__(self, *values: str) -> None:
        self.values = iter(values)

    def __call__(self) -> str:
        return next(self.values)


@pytest.fixture
def registry() -> MigrationJobRegistry:
    return MigrationJobRegistry(id_factory=IdFactory(), clock=FakeClock())


def test_reserve_uses_injected_ids_and_has_utc_timestamps(registry):
    first = registry.reserve("api")
    second = registry.reserve("worker")

    assert first["id"] == "job-1"
    assert second["id"] == "job-2"
    assert first["created_at"] == "2026-01-01T00:00:00+00:00"
    assert first["events"][0]["seq"] == 1
    assert second["events"][0]["seq"] == 2


def test_concurrent_reservations_admit_exactly_one_job():
    registry = MigrationJobRegistry(id_factory=IdFactory(), clock=FakeClock())
    start = Barrier(8)
    admitted = []
    conflicts = []

    def reserve() -> None:
        start.wait()
        try:
            admitted.append(registry.reserve("api")["id"])
        except MigrationJobConflict as error:
            conflicts.append(error)

    threads = [Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(admitted) == 1
    assert len(conflicts) == 7
    assert all(error.container_name == "api" for error in conflicts)


def test_snapshots_and_input_metadata_are_mutation_isolated(registry):
    metadata = {"ports": [8080], "nested": {"enabled": True}}
    created = registry.reserve("api", metadata=metadata)
    metadata["ports"].append(9090)
    created["metadata"]["nested"]["enabled"] = False
    created["events"].append({"seq": -1})

    fresh = registry.snapshot(created["id"])
    assert fresh["metadata"] == {"ports": [8080], "nested": {"enabled": True}}
    assert len(fresh["events"]) == 1


def test_cancel_request_is_idempotent_and_worker_can_finish_cancelled(registry):
    job = registry.reserve("api")
    requested = registry.request_cancel(job["id"], message="stop safely")
    repeated = registry.request_cancel(job["id"])

    assert requested["cancel_requested"] is True
    assert requested["status"] == "cancelling"
    assert requested["message"] == "stop safely"
    assert repeated["events"] == requested["events"]

    finished = registry.finish(job["id"], "cancelled", stage="cancelled", message="stopped")
    assert finished["status"] == "cancelled"
    assert registry.finish(job["id"], "cancelled") == finished
    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(job["id"], message="too late")
    with pytest.raises(InvalidMigrationJobTransition):
        registry.finish(job["id"], "completed")
    assert registry.begin_finalization(job["id"]) is None


def test_events_are_bounded_while_sequence_stays_monotonic():
    registry = MigrationJobRegistry(id_factory=IdFactory(), clock=FakeClock(), max_events=2)
    job = registry.reserve("api")
    registry.update(job["id"], stage="copying", progress=25)
    registry.update(job["id"], stage="starting", progress=75)

    events = registry.snapshot(job["id"])["events"]
    assert [event["seq"] for event in events] == [2, 3]
    assert [event["stage"] for event in events] == ["copying", "starting"]
    assert registry.snapshot(job["id"])["dropped_event_count"] == 1


def test_finalization_resolves_cancel_race_atomically(registry):
    cancelled = registry.reserve("cancelled")
    registry.request_cancel(cancelled["id"])
    assert registry.begin_finalization(cancelled["id"]) is None

    committed = registry.reserve("committed")
    finalizing = registry.begin_finalization(committed["id"])
    assert finalizing["finalizing"] is True
    assert finalizing["status"] == "finalizing"
    with pytest.raises(MigrationCancellationTooLate):
        registry.request_cancel(committed["id"])
    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(committed["id"], status="running")
    assert registry.update(committed["id"], stage="stopping_source")["status"] == "finalizing"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_job_never_reopens_finalization_gate(registry, terminal_status):
    job = registry.reserve(terminal_status)
    registry.finish(job["id"], terminal_status)

    assert registry.begin_finalization(job["id"]) is None


def test_cancel_and_finalization_race_has_one_consistent_winner():
    registry = MigrationJobRegistry(id_factory=IdFactory(), clock=FakeClock())
    for index in range(20):
        job = registry.reserve(f"api-{index}")
        start = Barrier(2)
        outcomes = []

        def cancel():
            start.wait()
            try:
                outcomes.append(("cancel", registry.request_cancel(job["id"])["status"]))
            except MigrationCancellationTooLate:
                outcomes.append(("cancel", "too_late"))

        def finalize():
            start.wait()
            snapshot = registry.begin_finalization(job["id"])
            outcomes.append(("finalize", None if snapshot is None else snapshot["status"]))

        threads = [Thread(target=cancel), Thread(target=finalize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(
            (value for _action, value in outcomes), key=lambda value: str(value)
        ) in (
            [None, "cancelling"],
            ["finalizing", "too_late"],
        )


def test_cancelling_job_cannot_return_to_running(registry):
    job = registry.reserve("api")
    registry.request_cancel(job["id"])

    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(job["id"], status="running")


def test_lifecycle_boundaries_cannot_be_set_through_generic_update(registry):
    queued = registry.reserve("api", status="queued")
    running = registry.update(queued["id"], status="running")
    assert running["status"] == "running"

    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(queued["id"], status="queued")
    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(queued["id"], status="cancelling")
    with pytest.raises(InvalidMigrationJobTransition):
        registry.update(queued["id"], status="finalizing")


def test_terminal_ttl_is_lazy_and_never_expires_running_jobs():
    clock = FakeClock()
    registry = MigrationJobRegistry(
        id_factory=IdFactory(), clock=clock, terminal_ttl=timedelta(seconds=10)
    )
    done = registry.reserve("done")
    running = registry.reserve("running")
    registry.finish(done["id"], "completed")
    clock.advance(seconds=10)

    assert registry.snapshot_for_container("done") is None
    assert registry.snapshot(running["id"])["status"] == "running"
    with pytest.raises(MigrationJobNotFound):
        registry.snapshot(done["id"])


def test_finished_job_allows_new_reservation_and_stale_cleanup_keeps_new_alias():
    clock = FakeClock()
    registry = MigrationJobRegistry(
        id_factory=SequenceIdFactory("old", "new"),
        clock=clock,
        terminal_ttl=timedelta(seconds=10),
    )
    old = registry.reserve("api")
    registry.finish(old["id"], "completed")
    new = registry.reserve("api")

    assert registry.release(old["id"])
    assert registry.snapshot_for_container("api")["id"] == new["id"]

    # The old terminal record remains readable until TTL, but must not own the
    # active/latest alias once a new migration has reserved the container.
    clock.advance(seconds=10)
    assert registry.prune_expired() == 0
    assert registry.snapshot_for_container("api")["id"] == new["id"]


def test_id_factory_collision_is_rejected_while_job_is_retained():
    registry = MigrationJobRegistry(
        id_factory=SequenceIdFactory("same-id", "same-id"), clock=FakeClock()
    )
    registry.reserve("api")
    with pytest.raises(ValueError, match="already in use"):
        registry.reserve("worker")


def test_capacity_counts_retained_terminal_jobs_but_ttl_frees_capacity():
    clock = FakeClock()
    registry = MigrationJobRegistry(
        id_factory=IdFactory(), clock=clock, max_jobs=1, terminal_ttl=timedelta(seconds=1)
    )
    first = registry.reserve("one")
    registry.finish(first["id"], "completed")
    with pytest.raises(MigrationJobCapacityExceeded):
        registry.reserve("two")
    clock.advance(seconds=1)
    assert registry.reserve("two")["container_name"] == "two"
