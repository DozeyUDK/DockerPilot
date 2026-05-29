import time

import pytest

import docker

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.docker_ops import DockerClientProvider, DockerOps
from dockerpilot.mcp.safety import ToolBlocked


class FakeExecResult:
    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        self.output = output


class ExecContainer:
    def __init__(self, exec_impl):
        self._exec_impl = exec_impl

    def exec_run(self, cmd, demux, tty):
        return self._exec_impl(cmd=cmd, demux=demux, tty=tty)


class FakeContainersAPI:
    def __init__(self, container):
        self._container = container

    def get(self, name):
        if name != "app":
            raise docker.errors.NotFound("not found")
        return self._container


class FakeClient:
    def __init__(self, container):
        self.containers = FakeContainersAPI(container)


def _ops(container, *, exec_timeout=1, redact_secrets=True, max_exec_bytes=64):
    cfg = MCPConfig(
        readonly=False,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=exec_timeout,
        redact_secrets=redact_secrets,
        max_exec_bytes=max_exec_bytes,
    )
    provider = DockerClientProvider(cfg)
    provider._client = FakeClient(container)
    return DockerOps(provider)


def test_container_exec_rejects_empty_or_wrong_type():
    ops = _ops(ExecContainer(lambda **_: FakeExecResult(0, (b"", b""))))
    with pytest.raises(ToolBlocked):
        ops.container_exec("app", [], timeout=1)
    with pytest.raises(ToolBlocked):
        ops.container_exec("app", "echo hi", timeout=1)  # type: ignore[arg-type]
    with pytest.raises(ToolBlocked):
        ops.container_exec("app", [""], timeout=1)


def test_container_exec_no_tty_no_shell_wrapping():
    seen = {}

    def impl(cmd, demux, tty):
        seen["cmd"] = cmd
        seen["demux"] = demux
        seen["tty"] = tty
        return FakeExecResult(0, (b"ok", b""))

    ops = _ops(ExecContainer(impl))
    result = ops.container_exec("app", ["echo", "hi"], timeout=1)
    assert seen["cmd"] == ["echo", "hi"]
    assert seen["demux"] is True
    assert seen["tty"] is False
    assert result["exit_code"] == 0


def test_container_exec_output_is_capped_and_truncated_flag_set():
    big = b"a" * 200

    def impl(cmd, demux, tty):
        return FakeExecResult(0, (big, b""))

    ops = _ops(ExecContainer(impl), max_exec_bytes=20)
    result = ops.container_exec("app", ["cmd"], timeout=1)
    assert result["truncated"] is True
    assert len(result["stdout"].encode("utf-8", errors="replace")) <= 20


def test_container_exec_redacts_secret_looking_output_when_enabled():
    def impl(cmd, demux, tty):
        return FakeExecResult(0, (b"token=supersecret\nok\n", b"PASSWORD: nope\n"))

    ops = _ops(ExecContainer(impl), redact_secrets=True, max_exec_bytes=200)
    result = ops.container_exec("app", ["cmd"], timeout=1)
    assert "supersecret" not in result["stdout"]
    assert "nope" not in result["stderr"]
    assert "***" in result["stdout"] or "***" in result["stderr"]


def test_container_exec_timeout_sets_timed_out_true():
    def impl(cmd, demux, tty):
        time.sleep(2)
        return FakeExecResult(0, (b"late", b""))

    ops = _ops(ExecContainer(impl), exec_timeout=1)
    start = time.time()
    result = ops.container_exec("app", ["cmd"], timeout=None)
    elapsed = time.time() - start
    assert elapsed < 1.5
    assert result["timed_out"] is True
    assert result.get("error", {}).get("kind") == "Timeout"


def test_container_exec_returns_structured_error_on_failure():
    def impl(cmd, demux, tty):
        raise docker.errors.APIError("boom")

    ops = _ops(ExecContainer(impl))
    result = ops.container_exec("app", ["cmd"], timeout=1)
    assert result["exit_code"] is None
    assert result["timed_out"] is False
    assert result.get("error", {}).get("message")

