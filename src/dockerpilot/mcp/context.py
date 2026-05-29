from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable, List, Optional


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


def _parse_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class MCPConfig:
    readonly: bool
    allow_destructive: bool
    allowed_containers: List[str]
    denied_containers: List[str]
    max_log_lines: int
    exec_timeout: int
    redact_secrets: bool
    migration_max_bundle_bytes: int = 2_147_483_648  # 2 GiB
    migration_allow_arbitrary_output_dir: bool = False

    # Defensive caps (not configurable in v1)
    max_log_bytes: int = 256_000
    max_exec_bytes: int = 256_000

    @classmethod
    def from_env(cls, environ: Optional[dict[str, str]] = None) -> "MCPConfig":
        env = os.environ if environ is None else environ
        return cls(
            readonly=_parse_bool(env.get("DOCKERPILOT_MCP_READONLY"), True),
            allow_destructive=_parse_bool(env.get("DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE"), False),
            allowed_containers=_parse_csv(env.get("DOCKERPILOT_MCP_ALLOWED_CONTAINERS")),
            denied_containers=_parse_csv(env.get("DOCKERPILOT_MCP_DENIED_CONTAINERS")),
            max_log_lines=max(1, _parse_int(env.get("DOCKERPILOT_MCP_MAX_LOG_LINES"), 200)),
            exec_timeout=max(1, _parse_int(env.get("DOCKERPILOT_MCP_EXEC_TIMEOUT"), 10)),
            redact_secrets=_parse_bool(env.get("DOCKERPILOT_MCP_REDACT_SECRETS"), True),
            migration_max_bundle_bytes=max(
                10_000_000, _parse_int(env.get("DOCKERPILOT_MCP_MIGRATION_MAX_BUNDLE_BYTES"), 2_147_483_648)
            ),
            migration_allow_arbitrary_output_dir=_parse_bool(
                env.get("DOCKERPILOT_MCP_MIGRATION_ALLOW_ARBITRARY_OUTPUT_DIR"), False
            ),
        )

    def is_container_denied(self, name: str) -> bool:
        return _matches_any(name, self.denied_containers)

    def is_container_allowed(self, name: str) -> bool:
        if self.is_container_denied(name):
            return False
        if not self.allowed_containers:
            return True
        return _matches_any(name, self.allowed_containers)


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    target = (name or "").strip()
    for raw in patterns:
        pat = raw.strip()
        if not pat:
            continue
        if target == pat:
            return True
        if target.startswith(pat):
            return True
    return False
