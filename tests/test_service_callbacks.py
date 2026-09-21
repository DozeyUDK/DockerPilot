from types import SimpleNamespace

from dockerpilot.services.alerts import AlertService
from dockerpilot.services.integration_testing import run_single_integration_test


class FakeConsole:
    def print(self, _message):
        pass


class FakeLogger:
    def info(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


def test_integration_service_uses_injected_compatibility_callback():
    calls = []

    def custom_http(config, start_time):
        calls.append((config["name"], start_time))
        return {"name": config["name"], "passed": True, "duration": 0}

    result = run_single_integration_test(
        {"name": "custom-http", "type": "http", "url": "http://unused"},
        run_http=custom_http,
    )
    assert result["passed"] is True
    assert calls and calls[0][0] == "custom-http"


def test_alert_service_uses_injected_trigger_callback():
    service = AlertService(FakeConsole(), FakeLogger())
    service.alert_rules = [
        {"name": "CPU", "condition": "cpu_percent > 50", "message": "hot"}
    ]
    triggered = []
    service.check_alerts(
        SimpleNamespace(cpu_percent=75.0, memory_percent=10.0),
        "web",
        trigger=lambda rule, container, details: triggered.append((rule["name"], container, details)),
    )
    assert triggered == [("CPU", "web", "CPU: 75.0%")]
