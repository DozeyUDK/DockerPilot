from types import SimpleNamespace

from dockerpilot.deployment_validation import comprehensive_container_validation


class FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)


class FakeContainer:
    def __init__(self, *, status="running", restart_count=0, logs=b"", stats=None, mounts=None):
        self.status = status
        self.attrs = {
            "RestartCount": restart_count,
            "Mounts": list(mounts or []),
        }
        self._logs = logs
        self._stats = stats or {}
        self.reload_calls = 0

    def reload(self):
        self.reload_calls += 1

    def logs(self, **_kwargs):
        return self._logs

    def stats(self, **_kwargs):
        return self._stats


def config(**overrides):
    values = {
        "image_tag": "demo:latest",
        "health_check_endpoint": None,
        "volumes": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def validate(container, cfg=None, **overrides):
    logger = overrides.pop("logger", FakeLogger())
    result = comprehensive_container_validation(
        container,
        cfg or config(),
        "8080",
        "green",
        get_database_config=overrides.pop("get_database_config", lambda _image: {}),
        get_database_name=overrides.pop("get_database_name", lambda _image: None),
        logger=logger,
        sleep=overrides.pop("sleep", lambda _seconds: None),
        **overrides,
    )
    return result, logger


def test_non_http_running_container_passes_validation():
    (ok, message), _logger = validate(FakeContainer())
    assert ok
    assert message == "All validations passed"


def test_non_running_container_fails_immediately():
    (ok, message), _logger = validate(FakeContainer(status="exited"))
    assert not ok
    assert "expected 'running'" in message


def test_http_non_2xx_response_fails_validation():
    response = SimpleNamespace(status_code=503)
    (ok, message), _logger = validate(
        FakeContainer(),
        config(health_check_endpoint="/health"),
        request_get=lambda *_args, **_kwargs: response,
        clock=iter([10.0, 10.2]).__next__,
    )
    assert not ok
    assert "503" in message


def test_critical_log_pattern_is_reported_as_failure():
    (ok, message), logger = validate(FakeContainer(logs=b"FATAL failed to start"))
    assert not ok
    assert "critical errors" in message
    assert logger.warning_messages


def test_critical_memory_usage_is_reported_as_failure():
    stats = {"memory_stats": {"usage": 98, "limit": 100}}
    (ok, message), _logger = validate(FakeContainer(stats=stats))
    assert not ok
    assert "Memory usage critical: 98.0%" in message


def test_database_restart_stability_uses_injected_sleep_and_threshold():
    sleeps = []
    container = FakeContainer(restart_count=6)
    (ok, message), logger = validate(
        container,
        get_database_config=lambda _image: {"max_restart_count": 15},
        get_database_name=lambda _image: "db",
        sleep=sleeps.append,
    )
    assert ok, message
    assert sleeps == [5, 2]
    assert container.reload_calls >= 3
    assert any("appears stable" in message for message in logger.info_messages)
