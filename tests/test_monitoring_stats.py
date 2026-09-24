"""Regression tests for extracted container statistics sampling."""

from types import SimpleNamespace

from dockerpilot import monitoring_stats
from dockerpilot.monitoring import MonitoringManager


class _Logger:
    def __init__(self):
        self.messages = []

    def error(self, message):
        self.messages.append(("error", str(message)))


class _Container:
    def __init__(self, samples):
        self.samples = list(samples)
        self.calls = []

    def stats(self, stream=False):
        self.calls.append(stream)
        return self.samples.pop(0)


def _sample(total, system, usage, limit, rx, tx, pids):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": total, "percpu_usage": [1, 1]},
            "system_cpu_usage": system,
        },
        "memory_stats": {"usage": usage, "limit": limit},
        "networks": {
            "eth0": {"rx_bytes": rx, "tx_bytes": tx},
            "eth1": {"rx_bytes": rx, "tx_bytes": tx},
        },
        "pids_stats": {"current": pids},
    }


def test_get_container_stats_preserves_sampling_and_calculations(monkeypatch):
    mib = 1024 * 1024
    container = _Container([
        _sample(100, 1000, 128 * mib, 1024 * mib, 1 * mib, 2 * mib, 3),
        _sample(300, 2000, 256 * mib, 1024 * mib, 3 * mib, 4 * mib, 7),
    ])
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        logger=_Logger(),
    )
    sleeps = []
    monkeypatch.setattr(monitoring_stats.time, "sleep", lambda seconds: sleeps.append(seconds))

    stats = monitoring_stats.get_container_stats(host, "demo")

    assert stats is not None
    assert stats.cpu_percent == 10.0
    assert stats.memory_usage_mb == 256.0
    assert stats.memory_limit_mb == 1024.0
    assert stats.memory_percent == 25.0
    assert stats.network_rx_mb == 6.0
    assert stats.network_tx_mb == 8.0
    assert stats.pids == 7
    assert container.calls == [False, False]
    assert sleeps == [1]


def test_get_container_stats_returns_none_and_logs_on_failure():
    logger = _Logger()
    host = SimpleNamespace(
        client=SimpleNamespace(
            containers=SimpleNamespace(get=lambda _name: (_ for _ in ()).throw(RuntimeError("boom")))
        ),
        logger=logger,
    )

    assert monitoring_stats.get_container_stats(host, "missing") is None
    assert any("Failed to get stats for missing" in message for _level, message in logger.messages)


def test_monitoring_manager_facade_delegates_stats(monkeypatch):
    manager = MonitoringManager.__new__(MonitoringManager)
    calls = []
    marker = object()
    monkeypatch.setattr(
        "dockerpilot.monitoring._get_container_stats_impl",
        lambda host, name: calls.append((host, name)) or marker,
    )

    assert manager.get_container_stats("demo") is marker
    assert calls == [(manager, "demo")]
