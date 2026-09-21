from types import SimpleNamespace

from dockerpilot.container_manager import ContainerManager
from dockerpilot.deployment_history import show_deployment_history
from dockerpilot.monitoring import MonitoringManager
from dockerpilot.services import health_checks


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message):
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


def test_managers_own_legacy_runtime_operations():
    assert hasattr(ContainerManager, "exec_container")
    assert hasattr(ContainerManager, "stop_and_remove_container")
    assert hasattr(ContainerManager, "exec_command_non_interactive")
    assert hasattr(MonitoringManager, "get_container_stats_once")
    assert hasattr(MonitoringManager, "monitor_container_live")


def test_standalone_health_check_success(monkeypatch):
    response = SimpleNamespace(status_code=200, elapsed=SimpleNamespace(total_seconds=lambda: 0.01))
    monkeypatch.setattr(health_checks.requests, "get", lambda *_args, **_kwargs: response)
    console = FakeConsole()
    assert health_checks.health_check_standalone(console, 8080, max_retries=1)
    assert any("Health check OK" in message for message in console.messages)


def test_deployment_history_missing_file_is_nonfatal(tmp_path):
    console = FakeConsole()
    show_deployment_history(console, FakeLogger(), history_file=str(tmp_path / "missing.json"))
    assert any("No deployment history" in message for message in console.messages)
