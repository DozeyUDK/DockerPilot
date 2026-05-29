import pytest

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.docker_ops import DockerClientProvider, DockerOps
import docker


class FakeContainer:
    def __init__(self, name: str, stats_payload=None):
        self.name = name
        self._stats_payload = stats_payload or {}

    def stats(self, stream=False):
        assert stream is False
        return self._stats_payload


class FakeContainersAPI:
    def __init__(self, containers_by_name):
        self._containers_by_name = containers_by_name

    def get(self, name):
        if name not in self._containers_by_name:
            raise docker.errors.NotFound("not found")
        return self._containers_by_name[name]


class FakeClient:
    def __init__(self, containers_by_name):
        self.containers = FakeContainersAPI(containers_by_name)


def _ops_with_fake_client(fake_client):
    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    provider = DockerClientProvider(cfg)
    provider._client = fake_client
    return DockerOps(provider)


def test_container_stats_handles_missing_fields():
    payload = {
        # missing precpu_stats/system deltas etc
        "cpu_stats": {},
        "memory_stats": {"usage": 100, "limit": 200},
        "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
        "pids_stats": {},
    }
    ops = _ops_with_fake_client(FakeClient({"app": FakeContainer("app", stats_payload=payload)}))
    stats = ops.container_stats("app")
    assert stats["cpu_percent"] is None
    assert stats["memory_usage"] == 100
    assert stats["memory_limit"] == 200
    assert stats["memory_percent"] == 50.0
    assert stats["network_rx"] == 10
    assert stats["network_tx"] == 20
    assert stats["pids"] is None


def test_logs_tail_is_capped_by_config(monkeypatch):
    class LogsContainer(FakeContainer):
        def logs(self, tail, since, timestamps):
            assert tail == 5
            assert since is None
            assert timestamps is True
            return b"line1\nline2\nline3\nline4\nline5\n"

    cfg = MCPConfig(
        readonly=True,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=5,
        exec_timeout=10,
        redact_secrets=True,
    )
    provider = DockerClientProvider(cfg)
    provider._client = FakeClient({"app": LogsContainer("app")})
    ops = DockerOps(provider)
    out = ops.container_logs(name="app", tail=200, since=None, timestamps=True, redact=True)
    assert out["tail"] == 5
    assert out["truncated"] is True
