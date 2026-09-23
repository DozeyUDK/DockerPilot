from types import SimpleNamespace
import json
import subprocess

from dockerpilot.services import alerts, integration_testing, system_validation, templates


class FakeConsole:
    def __init__(self):
        self.messages = []
        self.objects = []

    def print(self, message, **_kwargs):
        self.objects.append(message)
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", str(message)))

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


class FakeResponse:
    def __init__(self, status_code=200, seconds=0.01):
        self.status_code = status_code
        self.elapsed = SimpleNamespace(total_seconds=lambda: seconds)


def test_http_integration_test_preserves_get_request_arguments(monkeypatch):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return FakeResponse(204, 0.02)

    monkeypatch.setattr(integration_testing.requests, "get", fake_get)
    result = integration_testing.run_http_test(
        {
            "name": "health",
            "url": "http://service/health",
            "expected_status": 204,
            "headers": {"X-Test": "1"},
            "timeout": 7,
        },
        start_time=0.0,
    )

    assert result["passed"] is True
    assert result["status_code"] == 204
    assert calls == [("http://service/health", {"X-Test": "1"}, 7)]


def test_http_integration_test_preserves_post_json(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return FakeResponse(201)

    monkeypatch.setattr(integration_testing.requests, "post", fake_post)
    result = integration_testing.run_http_test(
        {
            "name": "create",
            "type": "http",
            "method": "POST",
            "url": "http://service/items",
            "data": {"name": "demo"},
            "expected_status": 201,
            "timeout": 3,
        },
        start_time=0.0,
    )

    assert result["passed"] is True
    assert calls == [("http://service/items", {}, {"name": "demo"}, 3)]


def test_http_integration_test_turns_request_errors_into_failed_result(monkeypatch):
    def fail(*_args, **_kwargs):
        raise integration_testing.requests.exceptions.RequestException("offline")

    monkeypatch.setattr(integration_testing.requests, "get", fail)
    result = integration_testing.run_http_test(
        {"name": "health", "url": "http://service/health"},
        start_time=0.0,
    )
    assert result["passed"] is False
    assert "offline" in result["error"]


def test_run_integration_tests_keeps_callback_and_report_order(tmp_path):
    config = tmp_path / "tests.yml"
    config.write_text(
        "tests:\n"
        "  - name: one\n"
        "    type: database\n"
        "  - name: two\n"
        "    type: database\n",
        encoding="utf-8",
    )
    events = []

    def run_single(test):
        events.append(("run", test["name"]))
        return {"name": test["name"], "passed": True, "duration": 0.0}

    def report(results):
        events.append(("report", [item["name"] for item in results]))

    assert integration_testing.run_integration_tests(
        FakeConsole(), FakeLogger(), str(config), run_single=run_single, generate_report=report
    ) is True
    assert events == [
        ("run", "one"),
        ("run", "two"),
        ("report", ["one", "two"]),
    ]


def test_custom_integration_test_preserves_timeout_result(tmp_path, monkeypatch):
    script = tmp_path / "slow.py"
    script.write_text("pass\n", encoding="utf-8")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(integration_testing.subprocess, "run", timeout)
    result = integration_testing.run_custom_test(
        {"name": "slow", "script": str(script), "timeout": 1},
        start_time=0.0,
    )
    assert result["passed"] is False
    assert result["error"] == "Test script timed out"


def test_save_test_report_keeps_legacy_json_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logger = FakeLogger()
    results = [{"name": "one", "passed": True, "duration": 0.1}]

    assert integration_testing.save_test_report(logger, results, passed=1, failed=0) is True
    payload = json.loads((tmp_path / "integration-test-report.json").read_text(encoding="utf-8"))
    assert payload["summary"] == {"total": 1, "passed": 1, "failed": 0, "success_rate": 100.0}
    assert payload["tests"] == results


def test_alert_setup_creates_packaged_template_and_initializes(tmp_path):
    output = tmp_path / "alerts.yml"
    service = alerts.AlertService(FakeConsole(), FakeLogger())

    assert service.setup_monitoring_alerts(str(output)) is True
    assert output.is_file()
    assert isinstance(service.alert_rules, list)
    assert isinstance(service.notification_channels, list)


def test_alert_trigger_preserves_notification_callback_contract():
    service = alerts.AlertService(FakeConsole(), FakeLogger())
    service.notification_channels = [
        {"type": "slack", "webhook_url": "https://example.invalid/hook"},
        {"type": "email"},
    ]
    sent = []
    rule = {"name": "CPU", "message": "hot", "condition": "cpu_percent > 80"}

    service.trigger_alert(
        rule,
        "web",
        "CPU: 90.0%",
        send_notification=lambda channel, message: sent.append((channel["type"], message)),
    )

    assert [kind for kind, _ in sent] == ["slack", "email"]
    assert all("Container: web" in message for _, message in sent)


def test_alert_slack_notification_payload_is_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(
        alerts.requests,
        "post",
        lambda url, json, timeout: calls.append((url, json, timeout)),
    )
    service = alerts.AlertService(FakeConsole(), FakeLogger())
    service.send_notification(
        {"type": "slack", "webhook_url": "https://hooks.invalid/demo", "channel": "#ops"},
        "alert text",
    )

    assert calls == [
        (
            "https://hooks.invalid/demo",
            {
                "text": "alert text",
                "channel": "#ops",
                "username": "Docker Pilot",
                "icon_emoji": ":warning:",
            },
            5,
        )
    ]


def test_system_validation_fails_when_docker_client_is_unavailable(monkeypatch):
    class BrokenClient:
        def version(self):
            raise RuntimeError("offline")

        def ping(self):
            raise RuntimeError("denied")

    monkeypatch.setattr(
        system_validation.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=5 * 1024**3),
    )
    console = FakeConsole()
    assert system_validation.validate_system_requirements(console, BrokenClient()) is False
    assert any("Docker connection failed" in message for message in console.messages)
    assert any("Docker daemon permission denied" in message for message in console.messages)


def test_checklist_missing_template_preserves_false_result(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    console = FakeConsole()
    logger = FakeLogger()

    assert templates.create_production_checklist(
        console,
        logger,
        str(tmp_path / "checklist.md"),
        templates_dir=templates_dir,
    ) is False
    assert any(level == "error" and "Template not found" in message for level, message in logger.messages)
