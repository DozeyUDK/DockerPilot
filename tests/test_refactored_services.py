from types import SimpleNamespace

from dockerpilot.services.alerts import AlertService
from dockerpilot.services.integration_testing import run_single_integration_test
from dockerpilot.services.system_validation import validate_system_requirements


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


class FakeDockerClient:
    def version(self):
        return {"Version": "test"}

    def ping(self):
        return True


def test_database_integration_test_keeps_placeholder_semantics():
    result = run_single_integration_test({"name": "db", "type": "database"})
    assert result["passed"] is True
    assert "specific database drivers" in result["note"]


def test_alert_service_loads_rules_and_triggers_console_output(tmp_path):
    config = tmp_path / "alerts.yml"
    config.write_text(
        "alerts:\n"
        "  - name: CPU high\n"
        "    condition: 'cpu_percent > 50'\n"
        "    message: hot\n"
        "notification_channels: []\n",
        encoding="utf-8",
    )
    console = FakeConsole()
    service = AlertService(console, FakeLogger())
    assert service.initialize_alert_monitoring(str(config))
    service.check_alerts(SimpleNamespace(cpu_percent=75.0, memory_percent=10.0), "web")
    assert any("ALERT" in message for message in console.messages)


def test_system_validation_happy_path(monkeypatch):
    from dockerpilot.services import system_validation

    monkeypatch.setattr(
        system_validation.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=5 * 1024**3),
    )
    console = FakeConsole()
    assert validate_system_requirements(console, FakeDockerClient()) is True
    assert any("All system requirements met" in message for message in console.messages)
