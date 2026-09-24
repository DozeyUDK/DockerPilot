"""Per-run capabilities for synchronous migration and promotion work."""

from __future__ import annotations

from typing import Any, Callable, Optional

from dockerpilot.execution_context import sudo_credential


class DockerPilotExecutionContext:
    """Activate a credential without storing it on the shared pilot instance."""

    __slots__ = (
        "_pilot_provider",
        "_sudo_password",
        "_credential_scope",
        "_pilot",
        "_active",
        "_used",
    )

    def __init__(
        self,
        pilot_provider: Callable[[], Any],
        *,
        sudo_password: Optional[str] = None,
    ) -> None:
        self._pilot_provider = pilot_provider
        self._sudo_password = sudo_password
        self._credential_scope = None
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

        # Consume the capability before any dependency call.  A failing pilot
        # provider must not leave a reusable object retaining the credential.
        password = self._sudo_password
        self._sudo_password = None
        self._used = True
        pilot = self._pilot_provider()
        scope = sudo_credential(password)
        scope.__enter__()
        password = None
        self._pilot = pilot
        self._credential_scope = scope
        self._active = True
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        scope = self._credential_scope
        self._credential_scope = None
        self._pilot = None
        self._sudo_password = None
        self._active = False
        if scope is not None:
            scope.__exit__(exc_type, exc_value, traceback)
        return False
