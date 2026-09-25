"""Safety helpers for the shareable DockerPilot live demo."""

from __future__ import annotations

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_MUTATION_ALLOWLIST = frozenset({
    "/api/auth/login",
    "/api/auth/logout",
    # This POST is computation-only: it validates input and returns generated
    # pipeline text without writing files or invoking Docker/subprocesses.
    "/api/pipeline/generate",
})


def demo_mutation_is_blocked(
    *,
    enabled: bool,
    allow_mutations: bool,
    method: str | None,
    path: str | None,
    allowed_paths=DEFAULT_MUTATION_ALLOWLIST,
) -> bool:
    """Return whether a request must be denied by shareable demo mode.

    Demo mode is fail-closed for API mutations. Normal DockerPilot operation is
    unchanged unless ``DOCKERPILOT_DEMO=true``; even then mutations require the
    explicit ``DOCKERPILOT_DEMO_ALLOW_MUTATIONS=true`` opt-in. A tiny allowlist
    is reserved for authentication lifecycle and computation-only demo actions.
    """
    if not enabled or allow_mutations:
        return False

    request_method = str(method or "").upper()
    request_path = str(path or "")
    if request_method not in MUTATING_METHODS:
        return False
    if not request_path.startswith("/api/"):
        return False
    return request_path not in allowed_paths
