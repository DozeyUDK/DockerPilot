"""AT-04B regression: agent-safe migration export must stop before Docker helper mutation."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.safety import ToolBlocked
from dockerpilot.mcp.tools import DockerPilotTools


def test_agent_safe_named_volume_export_is_blocked_before_migration_ops(monkeypatch, tmp_path):
    cfg = MCPConfig.from_env(
        {
            "DOCKERPILOT_AGENT_SAFE_MODE": "true",
            "DOCKERPILOT_MCP_READONLY": "false",
            "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "true",
            "DOCKERPILOT_MCP_ALLOWED_CONTAINERS": "demo",
        }
    )
    tools = DockerPilotTools(cfg)
    migration_calls = []

    def forbidden_migration_ops():
        migration_calls.append(True)
        raise AssertionError(
            "agent-safe export crossed the safety guard and reached migration/Docker helper code"
        )

    monkeypatch.setattr(tools, "_migration_ops", forbidden_migration_ops)

    with pytest.raises(ToolBlocked, match="Write operations are disabled"):
        tools.dockerpilot_migration_export_bundle(
            "demo",
            include_data=True,
            output_dir=str(tmp_path),
            confirm=True,
        )

    assert migration_calls == []


def test_agent_safe_export_guard_is_frozen_at_startup(monkeypatch, tmp_path):
    env = {
        "DOCKERPILOT_AGENT_SAFE_MODE": "true",
        "DOCKERPILOT_MCP_READONLY": "false",
        "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "true",
    }
    cfg = MCPConfig.from_env(env)
    tools = DockerPilotTools(cfg)

    # A compromised caller mutating environment input after startup must not
    # turn the already-created MCP config into a write-capable migration path.
    env["DOCKERPILOT_AGENT_SAFE_MODE"] = "false"
    env["DOCKERPILOT_MCP_READONLY"] = "false"

    monkeypatch.setattr(
        tools,
        "_migration_ops",
        lambda: (_ for _ in ()).throw(
            AssertionError("migration/Docker helper code must remain unreachable")
        ),
    )

    with pytest.raises(ToolBlocked, match="Write operations are disabled"):
        tools.dockerpilot_migration_export_bundle(
            "demo",
            include_data=True,
            output_dir=str(tmp_path),
            confirm=True,
        )
