"""Characterization tests for deployment HTTP health checks."""

from types import SimpleNamespace

import requests

from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.health_checks import advanced_health_check, detect_health_check_endpoint


class RecordingLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, message) -> None:
        self.infos.append(str(message))

    def warning(self, message) -> None:
        self.warnings.append(str(message))


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_detect_endpoint_uses_user_mapping_over_default_mapping():
    logger = RecordingLogger()

    endpoint = detect_health_check_endpoint(
        "registry/team/NGINX:v1",
        defaults={
            "health_checks": {
                "endpoint_mappings": {"nginx": "/default"},
                "default_endpoint": "/health",
            }
        },
        config={
            "health_checks": {
                "endpoint_mappings": {"nginx": "/custom"},
            }
        },
        logger=logger,
    )

    assert endpoint == "/custom"
    assert logger.infos == [
        "Detected image pattern 'nginx' -> endpoint '/custom'"
    ]


def test_detect_endpoint_user_non_http_list_replaces_default_list():
    logger = RecordingLogger()
    defaults = {
        "health_checks": {
            "non_http_services": ["redis"],
            "default_endpoint": "/ready",
        }
    }
    config = {"health_checks": {"non_http_services": ["postgres"]}}

    assert detect_health_check_endpoint(
        "redis:7",
        defaults=defaults,
        config=config,
        logger=logger,
    ) == "/ready"
    assert detect_health_check_endpoint(
        "postgres:17",
        defaults=defaults,
        config=config,
        logger=logger,
    ) is None


def test_detect_endpoint_skips_known_infrastructure_images():
    logger = RecordingLogger()

    assert detect_health_check_endpoint(
        "registry.k8s.io/kicbase:v0.0.47",
        defaults={},
        config={},
        logger=logger,
    ) is None
    assert logger.infos == [
        "Detected infrastructure service (kicbase) - skipping HTTP health check"
    ]


def test_detect_endpoint_uses_configured_and_builtin_defaults():
    configured_logger = RecordingLogger()
    builtin_logger = RecordingLogger()

    assert detect_health_check_endpoint(
        "example/app:v1",
        defaults={"health_checks": {"default_endpoint": "/live"}},
        config={},
        logger=configured_logger,
    ) == "/live"
    assert detect_health_check_endpoint(
        "example/app:v1",
        defaults={},
        config={},
        logger=builtin_logger,
    ) == "/health"
    assert configured_logger.infos == ["Using default health check endpoint: /live"]
    assert builtin_logger.infos == ["Using default health check endpoint: /health"]


def test_detect_endpoint_wrapper_preserves_dynamic_defaults_loader(monkeypatch):
    calls = []

    def fake_impl(image_tag, *, defaults, config, logger):
        calls.append((image_tag, defaults, config, logger))
        return "/sentinel"

    monkeypatch.setattr(
        "dockerpilot.deployment_service._detect_health_check_endpoint_impl",
        fake_impl,
    )

    class Service(DeploymentServiceMixin):
        def _load_health_check_defaults(self):
            return {"health_checks": {"default_endpoint": "/default"}}

    service = Service()
    service.config = {"health_checks": {"default_endpoint": "/custom"}}
    service.logger = RecordingLogger()

    assert service._detect_health_check_endpoint("example:v1") == "/sentinel"
    assert calls == [
        (
            "example:v1",
            {"health_checks": {"default_endpoint": "/default"}},
            service.config,
            service.logger,
        )
    ]


def test_none_endpoint_skips_request_and_succeeds():
    logger = RecordingLogger()

    def unexpected_request(*args, **kwargs):
        raise AssertionError("request must not run")

    assert advanced_health_check(
        "8080",
        None,
        30,
        3,
        logger=logger,
        request_get=unexpected_request,
    ) is True
    assert logger.infos == ["Skipping HTTP health check (non-HTTP service)"]


def test_first_2xx_response_succeeds_and_records_response_time():
    logger = RecordingLogger()
    calls: list[tuple[str, int]] = []

    def request_get(url, *, timeout):
        calls.append((url, timeout))
        return SimpleNamespace(status_code=204)

    assert advanced_health_check(
        "8080",
        "/ready",
        99,
        4,
        logger=logger,
        request_get=request_get,
        clock=FakeClock([10.0, 10.25]),
        sleep=lambda seconds: None,
    ) is True
    assert calls == [("http://localhost:8080/ready", 10)]
    assert logger.infos == [
        "Health check passed (attempt 1): 0.25s (status 204)"
    ]


def test_non_2xx_responses_retry_with_legacy_timeouts_and_sleep_schedule():
    logger = RecordingLogger()
    request_timeouts: list[int] = []
    sleeps: list[float] = []

    def request_get(url, *, timeout):
        request_timeouts.append(timeout)
        return SimpleNamespace(status_code=503)

    assert advanced_health_check(
        "9000",
        "/health",
        1,
        5,
        logger=logger,
        request_get=request_get,
        clock=FakeClock([0.0, 0.1] * 5),
        sleep=sleeps.append,
    ) is False
    assert request_timeouts == [10, 10, 10, 5, 5]
    assert sleeps == [5, 5, 5, 3]
    assert logger.warnings == [
        f"Health check returned 503 (attempt {attempt})"
        for attempt in range(1, 6)
    ]


def test_request_errors_are_logged_and_retried():
    logger = RecordingLogger()
    sleeps: list[float] = []

    def request_get(url, *, timeout):
        raise requests.exceptions.ConnectionError("connection refused")

    assert advanced_health_check(
        "8080",
        "/",
        10,
        2,
        logger=logger,
        request_get=request_get,
        clock=FakeClock([1.0, 2.0]),
        sleep=sleeps.append,
    ) is False
    assert sleeps == [5]
    assert logger.warnings == [
        "Health check failed (attempt 1): connection refused",
        "Health check failed (attempt 2): connection refused",
    ]


def test_success_after_failure_stops_retries_and_additional_sleep():
    logger = RecordingLogger()
    sleeps: list[float] = []
    statuses = iter([503, 200, 500])

    def request_get(url, *, timeout):
        return SimpleNamespace(status_code=next(statuses))

    assert advanced_health_check(
        "8080",
        "/health",
        30,
        3,
        logger=logger,
        request_get=request_get,
        clock=FakeClock([0.0, 0.1, 1.0, 1.2]),
        sleep=sleeps.append,
    ) is True
    assert sleeps == [5]
    assert logger.warnings == ["Health check returned 503 (attempt 1)"]
    assert logger.infos == ["Health check passed (attempt 2): 0.20s (status 200)"]


def test_zero_retries_fails_without_request_or_sleep():
    logger = RecordingLogger()

    def unexpected(*args, **kwargs):
        raise AssertionError("callback must not run")

    assert advanced_health_check(
        "8080",
        "/health",
        10,
        0,
        logger=logger,
        request_get=unexpected,
        sleep=unexpected,
    ) is False
    assert logger.infos == []
    assert logger.warnings == []


def test_mixin_wrapper_delegates_with_legacy_signature(monkeypatch):
    calls = []

    def fake_impl(port, endpoint, timeout, max_retries, *, logger):
        calls.append((port, endpoint, timeout, max_retries, logger))
        return "sentinel"

    monkeypatch.setattr(
        "dockerpilot.deployment_service._advanced_health_check_impl",
        fake_impl,
    )
    service = DeploymentServiceMixin()
    service.logger = RecordingLogger()

    assert service._advanced_health_check("8080", "/health", 30, 4) == "sentinel"
    assert calls == [("8080", "/health", 30, 4, service.logger)]
