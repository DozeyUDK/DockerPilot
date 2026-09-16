"""Characterize the legacy canary monitor and its extracted timing loop.

Some assertions capture surprising existing behavior, not recommended policy.
All requests and time operations are replaced; no Docker client is constructed.
"""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import requests

from dockerpilot import deployment_service
from dockerpilot.health_checks import monitor_canary_performance


@pytest.fixture(params=["service", "helper"])
def monitor(monkeypatch, request):
    state = SimpleNamespace(now=100.0)

    def sleep(seconds):
        state.now += seconds

    state.clock = Mock(side_effect=lambda: state.now)
    state.sleep = Mock(side_effect=sleep)
    state.get = Mock(return_value=SimpleNamespace(status_code=200))
    state.logger = Mock()
    service = SimpleNamespace(logger=state.logger)
    state.service = service
    monkeypatch.setattr(
        deployment_service, "time",
        SimpleNamespace(time=state.clock, sleep=state.sleep),
    )
    monkeypatch.setattr(
        deployment_service, "requests", SimpleNamespace(get=state.get),
    )
    state.run = lambda duration: (
        deployment_service.DeploymentServiceMixin._monitor_canary_performance(
            service, "8123", duration,
        )
    )
    if request.param == "helper":
        state.run = lambda duration: monitor_canary_performance(
            "8123", duration,
            request_get=lambda url, **kwargs: deployment_service.requests.get(url, **kwargs),
            clock=lambda: deployment_service.time.time(),
            sleep=lambda seconds: deployment_service.time.sleep(seconds),
            log_error=lambda message: service.logger.error(message),
            log_info=lambda message: service.logger.info(message),
        )
    return state


@pytest.mark.parametrize("duration", [0, -1])
def test_nonpositive_duration_accepts_without_requests(monitor, duration):
    assert monitor.run(duration) is True
    monitor.get.assert_not_called()
    monitor.sleep.assert_not_called()
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 0/0 errors (0.00%)"
    )


def test_success_uses_health_url_timeout_and_one_second_cadence(monitor):
    assert monitor.run(3) is True
    assert monitor.get.call_args_list == [
        call("http://localhost:8123/health", timeout=2)
    ] * 3
    assert monitor.sleep.call_args_list == [call(1)] * 3
    monitor.logger.error.assert_not_called()
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 0/3 errors (0.00%)"
    )


def test_request_elapsed_time_counts_and_last_sleep_can_pass_deadline(monitor):
    def slow_request(*args, **kwargs):
        monitor.now += 2.5
        return SimpleNamespace(status_code=200)

    monitor.get.side_effect = slow_request
    assert monitor.run(2) is True
    monitor.get.assert_called_once_with("http://localhost:8123/health", timeout=2)
    monitor.sleep.assert_called_once_with(1)
    assert monitor.now == 103.5


@pytest.mark.parametrize("status", [201, 204, 301, 404, 500])
def test_only_exact_200_is_success(monitor, status):
    monitor.get.return_value = SimpleNamespace(status_code=status)
    assert monitor.run(1) is False
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 1/1 errors (100.00%)"
    )


def test_ten_failures_do_not_trigger_early_abort(monitor):
    monitor.get.return_value = SimpleNamespace(status_code=500)
    assert monitor.run(10) is False
    assert monitor.get.call_count == monitor.sleep.call_count == 10
    monitor.logger.error.assert_not_called()
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 10/10 errors (100.00%)"
    )


def test_eleventh_failure_aborts_without_sleep_or_summary(monitor):
    monitor.get.return_value = SimpleNamespace(status_code=500)
    assert monitor.run(30) is False
    assert monitor.get.call_count == 11
    assert monitor.sleep.call_count == 10
    monitor.logger.error.assert_called_once_with("Canary error rate too high: 11/11")
    monitor.logger.info.assert_not_called()


@pytest.mark.parametrize(
    "statuses, expected, summary",
    [
        ([200] * 18 + [500] * 2, False, "2/20 errors (10.00%)"),
        ([200] * 19 + [500], False, "1/20 errors (5.00%)"),
        ([200] * 20 + [500], True, "1/21 errors (4.76%)"),
    ],
)
def test_exact_thresholds_and_final_acceptance(monitor, statuses, expected, summary):
    monitor.get.side_effect = [SimpleNamespace(status_code=s) for s in statuses]
    assert monitor.run(len(statuses)) is expected
    assert monitor.get.call_count == monitor.sleep.call_count == len(statuses)
    monitor.logger.error.assert_not_called()
    monitor.logger.info.assert_called_once_with(f"Canary monitoring complete: {summary}")


def test_error_rate_above_ten_percent_aborts_after_successful_request(monitor):
    monitor.get.side_effect = [
        SimpleNamespace(status_code=s) for s in [500, 500] + [200] * 9
    ]
    assert monitor.run(30) is False
    assert monitor.get.call_count == 11
    assert monitor.sleep.call_count == 10
    monitor.logger.error.assert_called_once_with("Canary error rate too high: 2/11")
    monitor.logger.info.assert_not_called()


@pytest.mark.parametrize("exception", [requests.Timeout, KeyboardInterrupt, SystemExit])
def test_request_exceptions_are_counted_but_bypass_early_abort(monitor, exception):
    monitor.get.side_effect = exception("request failed")
    assert monitor.run(12) is False
    assert monitor.get.call_count == monitor.sleep.call_count == 12
    monitor.logger.error.assert_not_called()
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 12/12 errors (100.00%)"
    )


def test_next_response_checks_errors_from_previous_exceptions(monitor):
    monitor.get.side_effect = [requests.Timeout()] * 11 + [
        SimpleNamespace(status_code=200)
    ]
    assert monitor.run(30) is False
    assert monitor.get.call_count == 12
    assert monitor.sleep.call_count == 11
    monitor.logger.error.assert_called_once_with("Canary error rate too high: 11/12")
    monitor.logger.info.assert_not_called()


def test_failure_reading_response_status_counts_request_twice(monitor):
    # The first increment occurs before reading status_code; the handler adds another.
    monitor.get.return_value = object()
    assert monitor.run(1) is False
    monitor.get.assert_called_once()
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 1/2 errors (50.00%)"
    )


def test_error_logger_exception_is_counted_and_monitoring_continues(monitor):
    monitor.get.return_value = SimpleNamespace(status_code=500)
    monitor.logger.error.side_effect = RuntimeError("log failed")
    assert monitor.run(12) is False
    assert monitor.get.call_count == monitor.sleep.call_count == 12
    assert monitor.logger.error.call_args_list == [
        call("Canary error rate too high: 11/11"),
        call("Canary error rate too high: 13/13"),
    ]
    monitor.logger.info.assert_called_once_with(
        "Canary monitoring complete: 14/14 errors (100.00%)"
    )


def test_sleep_failure_propagates_without_final_summary(monitor):
    monitor.sleep.side_effect = RuntimeError("sleep failed")
    with pytest.raises(RuntimeError, match="sleep failed"):
        monitor.run(3)
    monitor.get.assert_called_once()
    monitor.logger.info.assert_not_called()


@pytest.mark.parametrize(
    "clock_values",
    [[RuntimeError("clock failed")], [100, RuntimeError("clock failed")]],
)
def test_clock_failure_propagates(monitor, clock_values):
    monitor.clock.side_effect = clock_values
    with pytest.raises(RuntimeError, match="clock failed"):
        monitor.run(3)
    monitor.get.assert_not_called()
    monitor.sleep.assert_not_called()
    monitor.logger.info.assert_not_called()


def test_final_summary_logger_failure_propagates(monitor):
    monitor.logger.info.side_effect = RuntimeError("summary failed")
    with pytest.raises(RuntimeError, match="summary failed"):
        monitor.run(0)
    monitor.get.assert_not_called()


def test_clock_failure_precedes_logger_lookup(monitor):
    del monitor.service.logger
    monitor.clock.side_effect = RuntimeError("clock first")
    with pytest.raises(RuntimeError, match="clock first"):
        monitor.run(3)


def test_collaborators_are_resolved_at_each_use(monitor, monkeypatch):
    replacement_get = Mock(return_value=SimpleNamespace(status_code=200))
    replacement_sleep = Mock()
    replacement_logger = Mock()

    def replace_collaborators(*args, **kwargs):
        monkeypatch.setattr(
            deployment_service, "requests", SimpleNamespace(get=replacement_get),
        )
        monkeypatch.setattr(
            deployment_service, "time",
            SimpleNamespace(time=Mock(side_effect=[101, 102]), sleep=replacement_sleep),
        )
        monitor.service.logger = replacement_logger
        return SimpleNamespace(status_code=200)

    monitor.get.side_effect = replace_collaborators
    assert monitor.run(2) is True
    monitor.get.assert_called_once_with("http://localhost:8123/health", timeout=2)
    replacement_get.assert_called_once_with("http://localhost:8123/health", timeout=2)
    monitor.sleep.assert_not_called()
    assert replacement_sleep.call_args_list == [call(1), call(1)]
    monitor.logger.info.assert_not_called()
    replacement_logger.info.assert_called_once_with(
        "Canary monitoring complete: 0/2 errors (0.00%)"
    )
