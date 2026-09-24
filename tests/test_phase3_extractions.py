from contextlib import contextmanager
from types import SimpleNamespace
import json

from dockerpilot.container_manager import ContainerManager
from dockerpilot import deployment_history
from dockerpilot.deployment_history import show_deployment_history
from dockerpilot import monitoring
from dockerpilot.monitoring import MonitoringManager
from dockerpilot.services import health_checks


class FakeConsole:
    def __init__(self):
        self.messages = []
        self.objects = []

    def print(self, message):
        self.objects.append(message)
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


@contextmanager
def passthrough_error_handler(*_args, **_kwargs):
    yield


class FakeContainers:
    def __init__(self, container):
        self.container = container

    def get(self, _name):
        return self.container


class FakeClient:
    def __init__(self, container):
        self.containers = FakeContainers(container)


def test_managers_own_legacy_runtime_operations():
    assert hasattr(ContainerManager, "exec_container")
    assert hasattr(ContainerManager, "stop_and_remove_container")
    assert hasattr(ContainerManager, "exec_command_non_interactive")
    assert hasattr(MonitoringManager, "get_container_stats_once")
    assert hasattr(MonitoringManager, "monitor_container_live")


def test_stop_and_remove_preserves_running_container_flow():
    calls = []

    class Container:
        status = "running"

        def stop(self, timeout):
            calls.append(("stop", timeout))

        def remove(self):
            calls.append(("remove", None))

    console = FakeConsole()
    manager = ContainerManager(FakeClient(Container()), console, FakeLogger(), passthrough_error_handler)

    assert manager.stop_and_remove_container("web", timeout=17) is True
    assert calls == [("stop", 17), ("remove", None)]
    assert any("removed" in message for message in console.messages)


def test_exec_non_interactive_preserves_output_and_exit_status():
    class Container:
        status = "running"

        def exec_run(self, command):
            assert command == "echo hello"
            return SimpleNamespace(output=b"hello\n", exit_code=0)

    console = FakeConsole()
    manager = ContainerManager(FakeClient(Container()), console, FakeLogger(), passthrough_error_handler)

    assert manager.exec_command_non_interactive("web", "echo hello") is True
    assert any("hello" in message for message in console.messages)
    assert any("executed successfully" in message for message in console.messages)


def test_get_container_stats_once_preserves_cpu_memory_network_math(monkeypatch):
    stats1 = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 100, "percpu_usage": [1, 1]},
            "system_cpu_usage": 1000,
        },
        "memory_stats": {"usage": 100, "limit": 1000},
    }
    stats2 = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]},
            "system_cpu_usage": 1200,
        },
        "memory_stats": {"usage": 50 * 1024 * 1024, "limit": 100 * 1024 * 1024},
        "networks": {"eth0": {"rx_bytes": 2 * 1024 * 1024, "tx_bytes": 3 * 1024 * 1024}},
        "pids_stats": {"current": 7},
    }

    class Container:
        def __init__(self):
            self.samples = [stats1, stats2]

        def stats(self, stream=False):
            assert stream is False
            return self.samples.pop(0)

    monkeypatch.setattr(monitoring.time, "sleep", lambda _seconds: None)
    console = FakeConsole()
    manager = MonitoringManager(
        FakeClient(Container()), console, FakeLogger(), error_handler=passthrough_error_handler
    )

    assert manager.get_container_stats_once("web") is True
    assert any("CPU Usage: 100.00%" in message for message in console.messages)
    assert any("50.00 MB / 100.00 MB (50.00%)" in message for message in console.messages)
    assert any("Network RX: 2.00 MB, TX: 3.00 MB" in message for message in console.messages)
    assert any("Processes: 7" in message for message in console.messages)


def test_standalone_health_check_success(monkeypatch):
    response = SimpleNamespace(status_code=200, elapsed=SimpleNamespace(total_seconds=lambda: 0.01))
    monkeypatch.setattr(health_checks.requests, "get", lambda *_args, **_kwargs: response)
    console = FakeConsole()
    assert health_checks.health_check_standalone(console, 8080, max_retries=1)
    assert any("Health check OK" in message for message in console.messages)


def test_standalone_health_check_retries_network_failure(monkeypatch):
    response = SimpleNamespace(status_code=200, elapsed=SimpleNamespace(total_seconds=lambda: 0.02))
    calls = {"count": 0, "sleeps": 0}

    def fake_get(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise health_checks.requests.exceptions.RequestException("offline")
        return response

    monkeypatch.setattr(health_checks.requests, "get", fake_get)
    monkeypatch.setattr(health_checks.time, "sleep", lambda _seconds: calls.__setitem__("sleeps", calls["sleeps"] + 1))
    console = FakeConsole()

    assert health_checks.health_check_standalone(console, 8080, max_retries=2) is True
    assert calls == {"count": 2, "sleeps": 1}


def test_deployment_history_missing_file_is_nonfatal(tmp_path):
    console = FakeConsole()
    show_deployment_history(console, FakeLogger(), history_file=str(tmp_path / "missing.json"))
    assert any("No deployment history" in message for message in console.messages)


def test_deployment_history_sorts_newest_first_and_honors_limit(tmp_path, monkeypatch):
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            [
                {"timestamp": "2026-01-01T10:00:00", "id": "old-id-123456", "type": "rolling", "image_tag": "app:v1", "container_name": "app", "success": True, "duration_seconds": 4.0},
                {"timestamp": "2026-01-02T10:00:00", "id": "new-id-123456", "type": "rolling", "image_tag": "app:v2", "container_name": "app", "success": False, "duration_seconds": 5.0},
            ]
        ),
        encoding="utf-8",
    )

    class FakeTable:
        def __init__(self, *args, **kwargs):
            self.rows = []

        def add_column(self, *_args, **_kwargs):
            pass

        def add_row(self, *row):
            self.rows.append(row)

    monkeypatch.setattr(deployment_history, "Table", FakeTable)
    console = FakeConsole()
    show_deployment_history(console, FakeLogger(), limit=1, history_file=str(history))

    table = console.objects[-1]
    assert len(table.rows) == 1
    assert table.rows[0][1] == "new-id-12345"
    assert "Failed" in table.rows[0][5]
