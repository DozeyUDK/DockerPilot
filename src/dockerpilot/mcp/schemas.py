from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


class SystemSummary(TypedDict):
    docker_available: bool
    server_version: Optional[str]
    containers: dict[str, int]
    images: dict[str, int]
    warnings: list[str]


class DetectedIssue(TypedDict):
    severity: Literal["info", "warning", "critical"]
    container: str
    kind: str
    message: str
    suggested_next_tools: list[str]


class ToolRecommendation(TypedDict):
    tool: str
    reason: str
    args: dict[str, Any]

