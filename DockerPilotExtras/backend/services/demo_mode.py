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


DEMO_GENERATOR_MAX_REQUEST_BYTES = 64 * 1024
DEMO_GENERATOR_MAX_TEST_COMMANDS = 32
DEMO_GENERATOR_MAX_STRING_LENGTH = 4096


def validate_demo_generator_payload(data) -> str | None:
    """Bound the computation-only generator exposed by the public demo."""
    if not isinstance(data, dict):
        return "Provide a JSON object."
    for key, value in data.items():
        if isinstance(value, str) and len(value) > DEMO_GENERATOR_MAX_STRING_LENGTH:
            return f"{key} exceeds the demo string-length limit."
    commands = data.get("test_commands")
    if isinstance(commands, list):
        if len(commands) > DEMO_GENERATOR_MAX_TEST_COMMANDS:
            return "test_commands exceeds the demo item limit."
        if any(len(str(command)) > DEMO_GENERATOR_MAX_STRING_LENGTH for command in commands):
            return "A test command exceeds the demo string-length limit."
    return None
