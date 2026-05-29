import pytest

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.safety import ToolBlocked
from dockerpilot.mcp.tools import DockerPilotTools


def test_readonly_blocks_actions_before_docker_calls(monkeypatch):
    import dockerpilot.mcp.docker_ops as docker_ops

    calls = {"from_env": 0}

    def boom():
        calls["from_env"] += 1
        raise AssertionError("docker.from_env must not be called when policy blocks the operation")

    monkeypatch.setattr(docker_ops.docker, "from_env", boom)

    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    tools = DockerPilotTools(cfg)
    with pytest.raises(ToolBlocked):
        tools.dockerpilot_container_start(name="app", confirm=True, dry_run=True)
    with pytest.raises(ToolBlocked):
        tools.dockerpilot_container_exec(name="app", command=["echo", "hi"], confirm=True)
    assert calls["from_env"] == 0


def test_confirm_false_blocks_actions(monkeypatch):
    import dockerpilot.mcp.docker_ops as docker_ops

    calls = {"from_env": 0}

    def boom():
        calls["from_env"] += 1
        raise AssertionError("docker.from_env must not be called when confirm=false blocks the operation")

    monkeypatch.setattr(docker_ops.docker, "from_env", boom)

    cfg = MCPConfig(
        readonly=False,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    tools = DockerPilotTools(cfg)
    with pytest.raises(ToolBlocked):
        tools.dockerpilot_container_stop(name="app", timeout=1, confirm=False, dry_run=True)
    assert calls["from_env"] == 0


def test_destructive_requires_allow_destructive_flag():
    cfg = MCPConfig(
        readonly=False,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    tools = DockerPilotTools(cfg)
    with pytest.raises(ToolBlocked):
        tools.dockerpilot_container_remove(name="app", force=False, confirm=True, dry_run=True)
    with pytest.raises(ToolBlocked):
        tools.dockerpilot_image_prune(dangling_only=True, confirm=True, dry_run=True)
