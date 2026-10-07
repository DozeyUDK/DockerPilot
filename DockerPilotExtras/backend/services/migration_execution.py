"""Per-run capabilities for synchronous migration and promotion work."""

from __future__ import annotations

from typing import Any, Callable

from dockerpilot.execution_context import privileged_backup_authorization


class DockerPilotExecutionContext:
    """Activate one-shot operation capabilities without storing OS credentials."""

    __slots__ = (
        "_pilot_provider",
        "_privileged_backup_authorized",
        "_authorization_scope",
        "_pilot",
        "_active",
        "_used",
    )

    def __init__(
        self,
        pilot_provider: Callable[[], Any],
        *,
        privileged_backup_authorized: bool = False,
    ) -> None:
        self._pilot_provider = pilot_provider
        self._privileged_backup_authorized = bool(privileged_backup_authorized)
        self._authorization_scope = None
        self._pilot = None
        self._active = False
        self._used = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}(active={self._active})"

    @property
    def pilot(self) -> Any:
        if not self._active:
            raise RuntimeError("DockerPilot execution context is not active")
        return self._pilot

    def __enter__(self) -> "DockerPilotExecutionContext":
        if self._active:
            raise RuntimeError("DockerPilot execution context is already active")
        if self._used:
            raise RuntimeError("DockerPilot execution context has already been used")

        authorized = self._privileged_backup_authorized
        self._privileged_backup_authorized = False
        self._used = True
        pilot = self._pilot_provider()
        scope = privileged_backup_authorization(authorized)
        scope.__enter__()
        authorized = False
        self._pilot = pilot
        self._authorization_scope = scope
        self._active = True
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        scope = self._authorization_scope
        self._authorization_scope = None
        self._pilot = None
        self._privileged_backup_authorized = False
        self._active = False
        if scope is not None:
            scope.__exit__(exc_type, exc_value, traceback)
        return False
