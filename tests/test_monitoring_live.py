"""Regression tests for live streaming monitoring extraction."""

import json
from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import monitoring_live
from dockerpilot.monitoring import MonitoringManager


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(("warning", str(message)))


class _Container:
    def __init__(self, stream):
        self.stream = stream
        self.calls = []

    def stats(self, stream=False):
        self.calls.append(stream)
        return iter(self.stream)


@contextmanager
def _error_handler(*_args, **_kwargs):
    yield


def _sample(total, system, usage_mb, limit_mb):
    mib = 1024 * 1024
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": total, "percpu_usage": [1, 1]},
            "system_cpu_usage": system,
        },
        "memory_stats": {"usage": usage_mb * mib, "limit": limit_mb * mib},
    }


def test_live_monitor_parses_bytes_and_preserves_cpu_delta_formula(monkeypatch):
    first = _sample(100, 1000, 128, 1024)
    second = _sample(300, 2000, 256, 1024)
    container = _Container([json.dumps(first).encode(), second])
    console = Console(file=StringIO(), record=True, force_terminal=False, width=120)
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=console,
        logger=_Logger(),
        _error_handler=_error_handler,
    )
    times = iter([0.0, 1.0, 2.0])
    cleared = []
    sleeps = []
    monkeypatch.setattr(monitoring_live.time, "time", lambda: next(times))
    monkeypatch.setattr(monitoring_live.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(monitoring_live.os, "system", lambda command: cleared.append(command) or 0)

    assert monitoring_live.monitor_container_live(host, "demo", duration=30) is True

    output = console.export_text()
    assert "CPU: 0.00%" in output
    assert "CPU: 40.00%" in output
    assert "RAM: 256.0MB / 1024.0MB (25.0%)" in output
    assert "Live monitoring completed" in output
    assert container.calls == [True]
    assert len(cleared) == 2
    assert sleeps == [1, 1]


def test_live_monitor_logs_invalid_json_and_completes(monkeypatch):
    logger = _Logger()
    container = _Container([b"not-json"])
    console = Console(file=StringIO(), record=True, force_terminal=False, width=120)
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=console,
        logger=logger,
        _error_handler=_error_handler,
    )
    times = iter([0.0, 1.0])
    monkeypatch.setattr(monitoring_live.time, "time", lambda: next(times))

    assert monitoring_live.monitor_container_live(host, "demo", duration=30) is True
    assert any("Stats parsing error" in message for _level, message in logger.messages)
    assert "Live monitoring completed" in console.export_text()


def test_monitoring_manager_live_facade_delegates(monkeypatch):
    manager = MonitoringManager.__new__(MonitoringManager)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.monitoring._monitor_container_live_impl",
        lambda host, name, duration: calls.append((host, name, duration)) or True,
    )

    assert manager.monitor_container_live("demo", duration=12) is True
    assert calls == [(manager, "demo", 12)]
