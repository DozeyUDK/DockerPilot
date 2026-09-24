"""Regression tests for extracted monitoring dashboard/history services."""

import json
from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from dockerpilot import monitoring_dashboard
from dockerpilot.models import ContainerStats
from dockerpilot.monitoring import MonitoringManager


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


class _FakeLive:
    def __init__(self, *args, **kwargs):
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, value):
        self.updates.append(value)


def _stats(cpu=10.0, memory=25.0):
    return ContainerStats(
        cpu_percent=cpu,
        memory_usage_mb=256.0,
        memory_limit_mb=1024.0,
        memory_percent=memory,
        network_rx_mb=3.0,
        network_tx_mb=4.0,
        pids=7,
        timestamp=datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc),
    )


def test_save_metrics_history_preserves_json_shape_and_timestamp(tmp_path):
    metrics_file = tmp_path / "metrics.json"
    logger = _Logger()
    host = SimpleNamespace(metrics_file=str(metrics_file), logger=logger)

    monitoring_dashboard.save_metrics_history(host, {"demo": [_stats()]})

    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["demo"][0]["cpu_percent"] == 10.0
    assert data["demo"][0]["timestamp"] == "2026-09-24T18:00:00+00:00"
    assert any("Metrics history saved" in message for _level, message in logger.messages)


def test_show_monitoring_summary_keeps_average_and_peak_values():
    console = Console(file=StringIO(), record=True, force_terminal=False, width=120)
    host = SimpleNamespace(console=console)

    monitoring_dashboard.show_monitoring_summary(
        host,
        {"demo": [_stats(cpu=10.0, memory=20.0), _stats(cpu=20.0, memory=40.0)]},
    )

    output = console.export_text()
    assert "demo" in output
    assert "15.0%" in output
    assert "30.0%" in output
    assert "20.0%" in output
    assert "40.0%" in output


def test_dashboard_collects_one_iteration_and_forwards_history(monkeypatch):
    console = Console(file=StringIO(), force_terminal=False, width=120)
    container = SimpleNamespace(
        status="running",
        attrs={"Created": "2026-09-24T17:00:00+00:00"},
    )
    saved = []
    summarized = []
    host = SimpleNamespace(
        client=SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container)),
        console=console,
        get_container_stats=lambda _name: _stats(),
        _save_metrics_history=lambda history: saved.append(history),
        _show_monitoring_summary=lambda history: summarized.append(history),
    )
    live_instances = []

    def make_live(*args, **kwargs):
        live = _FakeLive(*args, **kwargs)
        live_instances.append(live)
        return live

    times = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(monitoring_dashboard, "Live", make_live)
    monkeypatch.setattr(monitoring_dashboard.time, "time", lambda: next(times))
    monkeypatch.setattr(monitoring_dashboard.time, "sleep", lambda _seconds: None)

    monitoring_dashboard.monitor_containers_dashboard(host, ["demo"], duration=1)

    assert len(live_instances) == 1
    assert len(live_instances[0].updates) == 1
    assert len(saved) == 1 and len(saved[0]["demo"]) == 1
    assert summarized == saved


def test_monitoring_manager_dashboard_facade_delegates(monkeypatch):
    manager = MonitoringManager.__new__(MonitoringManager)
    calls = []
    monkeypatch.setattr(
        "dockerpilot.monitoring._monitor_containers_dashboard_impl",
        lambda host, containers, duration: calls.append((host, containers, duration)) or "ok",
    )

    assert manager.monitor_containers_dashboard(["a", "b"], duration=9) == "ok"
    assert calls == [(manager, ["a", "b"], 9)]
