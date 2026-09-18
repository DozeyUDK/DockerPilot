"""Flask-independent seam for synchronous container migrations.

The executor is intentionally injected.  During the extraction phase it is
the existing, synchronous migration implementation; later it can be replaced
without making environment promotion depend on an HTTP request context.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping, Optional

from backend.security import redact_sensitive_text


MigrationExecutor = Callable[[dict[str, Any]], Any]
_MIGRATION_EXECUTION_COORDINATOR = RLock()


@dataclass(frozen=True)
class MigrationSpec:
    """Inputs shared by the migration endpoint and promotion workflow."""

    container_name: Optional[str]
    source_server_id: Optional[str] = "local"
    target_server_id: Optional[str] = None
    include_data: Any = False
    stop_source: Any = False

    @classmethod
    def from_payload(cls, payload: Optional[Mapping[str, Any]]) -> "MigrationSpec":
        # Treat malformed JSON shapes like an empty request.  The legacy
        # executor will then return its stable missing-fields 400 response.
        data = payload if isinstance(payload, Mapping) else {}
        return cls(
            container_name=data.get("container_name"),
            source_server_id=data.get("source_server_id", "local"),
            target_server_id=data.get("target_server_id"),
            include_data=data.get("include_data", False),
            stop_source=data.get("stop_source", False),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "container_name": self.container_name,
            "source_server_id": self.source_server_id,
            "target_server_id": self.target_server_id,
            "include_data": self.include_data,
            "stop_source": self.stop_source,
        }


@dataclass(frozen=True)
class MigrationResult:
    """Normalized result of the legacy synchronous executor."""

    body: Any
    status: int = 200
    explicit_status: bool = False
    response_tail: tuple[Any, ...] = ()

    @property
    def is_terminal(self) -> bool:
        """A 202 response represents accepted work, not finished work."""

        return self.status != 202

    @property
    def completed_successfully(self) -> bool:
        if not self.is_terminal or self.status != 200:
            return False
        return isinstance(self.body, Mapping) and self.body.get("success") is True

    def to_response(self) -> Any:
        """Preserve Flask-RESTful's legacy body-vs-tuple response shape."""

        if self.explicit_status:
            return (self.body, self.status, *self.response_tail)
        return self.body


class MigrationRunner:
    """Run one migration synchronously and normalize its legacy response."""

    def __init__(self, executor: MigrationExecutor, *, coordinator=None) -> None:
        self._executor = executor
        # Direct API migrations, async jobs, and coordinated promotion
        # operations share this lock.
        # The legacy executor uses a cached DockerPilot instance and is not
        # safe to overlap within one backend process.
        self._coordinator = coordinator or _MIGRATION_EXECUTION_COORDINATOR

    def run_inline(
        self,
        spec: MigrationSpec,
        *,
        execution_context: Any = None,
        operation_context: Any = None,
    ) -> MigrationResult:
        """Block until the injected executor returns a terminal result."""

        return self.run_operation(
            lambda: self._execute(spec, operation_context),
            execution_context=execution_context,
        )

    def run_operation(
        self,
        operation: Callable[[], Any],
        *,
        execution_context: Any = None,
    ) -> MigrationResult:
        """Run one arbitrary migration-adjacent operation under the coordinator."""

        try:
            with self._coordinator:
                if execution_context is None:
                    raw_result = operation()
                else:
                    with execution_context:
                        raw_result = operation()
        except Exception as exc:
            return MigrationResult(
                {"error": redact_sensitive_text(exc)},
                status=500,
                explicit_status=True,
            )

        if isinstance(raw_result, tuple) and len(raw_result) >= 2:
            body, status = raw_result[0], raw_result[1]
            try:
                normalized_status = int(status)
            except (TypeError, ValueError):
                return MigrationResult(
                    {"error": "Migration returned an invalid status"},
                    status=500,
                    explicit_status=True,
                )
            return MigrationResult(
                body,
                normalized_status,
                explicit_status=True,
                response_tail=tuple(raw_result[2:]),
            )

        return MigrationResult(raw_result)

    def run(
        self,
        spec: MigrationSpec,
        *,
        execution_context: Any = None,
        operation_context: Any = None,
    ) -> MigrationResult:
        """Backward-compatible spelling for the synchronous operation."""

        return self.run_inline(
            spec,
            execution_context=execution_context,
            operation_context=operation_context,
        )

    def _execute(self, spec: MigrationSpec, operation_context: Any) -> Any:
        if operation_context is None:
            return self._executor(spec.to_payload())
        return self._executor(
            spec.to_payload(),
            operation_context=operation_context,
        )


# Explicit alias for callers that want to emphasize current execution mode.
SynchronousMigrationRunner = MigrationRunner
