"""Ephemeral credentials scoped to one logical DockerPilot execution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


_UNSET = object()
_sudo_password: ContextVar[object] = ContextVar(
    "dockerpilot_sudo_password",
    default=_UNSET,
)
_privileged_backup_authorized: ContextVar[object] = ContextVar(
    "dockerpilot_privileged_backup_authorized",
    default=_UNSET,
)


@contextmanager
def sudo_credential(password: Optional[str]) -> Iterator[None]:
    """Make a credential visible only inside the current execution context."""

    token = _sudo_password.set(password)
    try:
        yield
    finally:
        _sudo_password.reset(token)


def resolve_sudo_password(fallback: Optional[str] = None) -> Optional[str]:
    """Return the scoped credential, preserving legacy instance fallback."""

    password = _sudo_password.get()
    if password is _UNSET:
        return fallback
    return password  # type: ignore[return-value]


@contextmanager
def privileged_backup_authorization(authorized: bool) -> Iterator[None]:
    """Scope authorization to use the privileged backup helper path."""

    token = _privileged_backup_authorized.set(bool(authorized))
    try:
        yield
    finally:
        _privileged_backup_authorized.reset(token)


def resolve_privileged_backup_authorization(fallback: bool = True) -> bool:
    """Resolve privileged-backup authorization for the current execution.

    The fallback keeps trusted local CLI behavior unchanged. DockerPilotExtras
    always installs an explicit False/True scope for each promotion run.
    """

    authorized = _privileged_backup_authorized.get()
    if authorized is _UNSET:
        return bool(fallback)
    return bool(authorized)
