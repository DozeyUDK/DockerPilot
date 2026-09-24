"""Regression tests for one-shot monitoring snapshot extraction."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import monitoring_snapshot
from dockerpilot.monitoring import MonitoringManager


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(("warning", str(message)))


class _Container:
    def __init__(self, samples):
        self.samples = list(samples)
        self.calls = []

    def stats(self, stream=False):
        self.calls.append(stream)
        return self.samples.pop(0)


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _sample(total, system, usage, limit, rx, tx, pids):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": total, "percpu_usage": [1, 1]},
            "system_cpu_usage": system,
        },
        "memory_stats": {"usage": usage, "limit": limit},
        "networks": {"eth0": {"rx_bytes": rx, "tx_bytes": tx}},
        "pids_stats": {"current": pids},
    }


def test_one_shot_snapshot_preserves_legacy_cpu_formula(monkeypatch):
    mib = 1024 * 1024
    container = _Container([
        _sample(100, 1000, 128 * mib, 1024 * mib, 1 * mib, 2 * mib, 3),
        _sample(300, 2000, 256 * mib, 1024 * mib, 3 * mib, 4 * mib, 7),
    ])
    console = Console(file=StringIO(), record=True, force_terminal=False, width=120)
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=console,
        logger=_Logger(),
        _error_handler=_error_handler,
    )
    sleeps = []
    monkeypatch.setattr(monitoring_snapshot.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert monitoring_snapshot.get_container_stats_once(host, "demo") is True

    output = console.export_text()
    # This path intentionally differs from get_container_stats():
    # (200 / 1000) * 2 CPUs * 100 = 40%.
    assert "CPU Usage: 40.00%" in output
    assert "256.00 MB / 1024.00 MB (25.00%)" in output
    assert "Network RX: 3.00 MB, TX: 4.00 MB" in output
    assert "Processes: 7" in output
    assert container.calls == [False, False]
    assert sleeps == [1]


def test_one_shot_snapshot_logs_cpu_parse_error_and_continues(monkeypatch):
    container = _Container([
        {"cpu_stats": {}, "memory_stats": {}},
        {"cpu_stats": {}, "memory_stats": {"usage": 0, "limit": 1}},
    ])
    console = Console(file=StringIO(), record=True, force_terminal=False, width=120)
    logger = _Logger()
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=console,
        logger=logger,
        _error_handler=_error_handler,
    )
    monkeypatch.setattr(monitoring_snapshot.time, "sleep", lambda _seconds: None)

    assert monitoring_snapshot.get_container_stats_once(host, "demo") is True
    assert "CPU Usage: 0.00%" in console.export_text()
    assert any("CPU calculation error" in message for _level, message in logger.messages)


def test_monitoring_manager_snapshot_facade_delegates(monkeypatch):
    manager = MonitoringManager.__new__(MonitoringManager)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.monitoring._get_container_stats_once_impl",
        lambda host, name: calls.append((host, name)) or True,
    )

    assert manager.get_container_stats_once("demo") is True
    assert calls == [(manager, "demo")]
