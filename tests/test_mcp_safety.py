import pytest

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.safety import ToolBlocked, require_confirm, require_container_allowed, require_destructive_allowed, require_not_readonly


def test_denylist_wins_over_allowlist():
    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=["dev-"],
        denied_containers=["dev-secret"],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    with pytest.raises(ToolBlocked):
        require_container_allowed(cfg, "dev-secret")


def test_allowlist_prefix_permits_expected_names():
    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=["dev-", "test-"],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    require_container_allowed(cfg, "dev-app")
    require_container_allowed(cfg, "test-redis")
    with pytest.raises(ToolBlocked):
        require_container_allowed(cfg, "prod-app")


def test_readonly_blocks_state_changing_ops():
    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    with pytest.raises(ToolBlocked):
        require_not_readonly(cfg)


def test_destructive_requires_separate_flag():
    cfg = MCPConfig(
        readonly=False,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    with pytest.raises(ToolBlocked):
        require_destructive_allowed(cfg)


def test_confirm_false_blocks():
    with pytest.raises(ToolBlocked):
        require_confirm(False)
    with pytest.raises(ToolBlocked):
        require_confirm(None)  # type: ignore[arg-type]
    require_confirm(True)

