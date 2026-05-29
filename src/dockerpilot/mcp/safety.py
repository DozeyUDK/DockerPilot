from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .context import MCPConfig


class ToolBlocked(RuntimeError):
    """Raised when a tool is blocked by policy."""


_SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|token|secret|key|api[_-]?key|access[_-]?key|private[_-]?key|credential|auth)"
)

_LIKELY_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+[a-z0-9\-\._]+|ghp_[a-z0-9]{20,}|xox[baprs]-[a-z0-9-]{10,}|AIza[0-9A-Za-z\-_]{20,})\b"
)


def require_container_allowed(config: MCPConfig, name: str) -> None:
    if not name or not name.strip():
        raise ToolBlocked("Container name is required.")
    if config.is_container_denied(name):
        raise ToolBlocked(f"Container '{name}' is denied by DOCKERPILOT_MCP_DENIED_CONTAINERS.")
    if not config.is_container_allowed(name):
        raise ToolBlocked(f"Container '{name}' is not allowed by DOCKERPILOT_MCP_ALLOWED_CONTAINERS.")


def require_not_readonly(config: MCPConfig) -> None:
    if config.readonly:
        raise ToolBlocked("Write operations are disabled (DOCKERPILOT_MCP_READONLY=true).")


def require_destructive_allowed(config: MCPConfig) -> None:
    if not config.allow_destructive:
        raise ToolBlocked(
            "Destructive operations are disabled (set DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true to enable)."
        )


def require_confirm(confirm: bool) -> None:
    if confirm is not True:
        raise ToolBlocked("This operation requires explicit confirm=true.")


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key or ""))


def redact_for_key(key: str, value: Any) -> Any:
    if value is None:
        return None
    if is_secret_key(key):
        return "***"
    return value


def redact_text(text: str) -> str:
    if not text:
        return text
    # Mask obvious "k=v" patterns
    text = re.sub(
        r"(?im)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|credential|auth)\b\s*[:=]\s*([^\s'\"`]+)",
        lambda m: f"{m.group(1)}=***",
        text,
    )
    # Mask a few common token formats even without keys nearby
    return _LIKELY_SECRET_VALUE_RE.sub("***", text)


def redact_obj(obj: Any) -> Any:
    """Recursively redact secret-looking keys in mappings/lists."""
    if isinstance(obj, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            if is_secret_key(key):
                redacted[key] = "***"
            else:
                redacted[key] = redact_obj(v)
        return redacted
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def env_list_to_dict(env_list: Sequence[str] | None) -> dict[str, str]:
    envs: dict[str, str] = {}
    for item in env_list or []:
        if not isinstance(item, str):
            continue
        if "=" not in item:
            envs[item] = ""
            continue
        k, v = item.split("=", 1)
        envs[k] = v
    return envs


def redact_env_dict(env: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in env.items():
        out[k] = "***" if is_secret_key(k) else v
    return out


def redact_labels(labels: Mapping[str, str] | None) -> dict[str, str]:
    labels = labels or {}
    out: dict[str, str] = {}
    for k, v in labels.items():
        out[str(k)] = "***" if is_secret_key(str(k)) else str(v)
    return out


def cap_text(text: str, max_bytes: int) -> tuple[str, bool]:
    if not text:
        return text, False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[: max(0, max_bytes)]
    return truncated.decode("utf-8", errors="replace"), True
