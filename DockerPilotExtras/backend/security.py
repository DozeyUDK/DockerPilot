"""Small, dependency-free helpers for safe operator diagnostics."""

from __future__ import annotations

import re


_SENSITIVE_NAME = r"[A-Za-z0-9_]*(?:password|passwd|pass|token|secret|api[_-]?key|credential|auth)[A-Za-z0-9_]*"
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?:[\"']?{_SENSITIVE_NAME}[\"']?\s*[:=])",
    re.IGNORECASE,
)
_ENV_OPTION_RE = re.compile(r"(?:^|\s)(?:-e|--env)(?:=|\s+)", re.IGNORECASE)
_URL_CREDENTIAL_RE = re.compile(r"://[^\s/@:]+:")
_MAX_SAFE_ERROR_LENGTH = 500


def redact_sensitive_text(value: object) -> str:
    """Conservatively cut secret-bearing tails from diagnostic text.

    Shell quoting is deliberately not parsed with a regular expression. Once
    an environment option or secret assignment begins, the remainder is less
    valuable than guaranteeing that quoted or malformed values cannot leak.
    """

    text = str(value)
    candidates = []
    for kind, pattern in (
        ("env", _ENV_OPTION_RE),
        ("secret", _SECRET_ASSIGNMENT_RE),
        ("url", _URL_CREDENTIAL_RE),
    ):
        match = pattern.search(text)
        if match:
            candidates.append((match.start(), kind, match))

    if not candidates:
        return text

    _start, kind, match = min(candidates, key=lambda item: item[0])
    if kind == "env":
        return f"{text[:match.start()].rstrip()} <redacted-env>"
    if kind == "url":
        return f"{text[:match.start()]}://<redacted>"
    return f"{text[:match.end()]}<redacted>"


def safe_error_message(exc: BaseException) -> str:
    """Build a bounded, redacted error summary suitable for logs and APIs."""

    redacted = redact_sensitive_text(exc)
    if len(redacted) > _MAX_SAFE_ERROR_LENGTH:
        redacted = f"{redacted[:_MAX_SAFE_ERROR_LENGTH]}..."
    return f"{type(exc).__name__}: {redacted}"

