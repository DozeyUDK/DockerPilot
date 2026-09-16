"""Characterization tests for deployment history recording."""

from datetime import datetime, timedelta
import json

import pytest

from dockerpilot.deployment_history import record_deployment
from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.models import DeploymentConfig


FIXED_NOW = datetime(2026, 9, 16, 5, 10, 30, 123456)


def make_config() -> DeploymentConfig:
    return DeploymentConfig(
        image_tag="example:v2",
        container_name="example",
        port_mapping={},
        environment={},
        volumes={},
    )


def call_record(history_file, records, errors, *, target_env=None) -> None:
    record_deployment(
        "deploy-123",
        make_config(),
        "rolling",
        True,
        timedelta(seconds=12.5),
        target_env,
        append_record=records.append,
        log_error=errors.append,
        now=lambda: FIXED_NOW,
        history_file=str(history_file),
    )


def test_record_deployment_appends_exact_schema_to_memory_and_new_file(tmp_path):
    records = []
    errors = []
    history_file = tmp_path / "deployment_history.json"

    call_record(history_file, records, errors)

    expected = {
        "id": "deploy-123",
        "timestamp": "2026-09-16T05:10:30.123456",
        "type": "rolling",
        "image_tag": "example:v2",
        "container_name": "example",
        "success": True,
        "duration_seconds": 12.5,
    }
    assert records == [expected]
    assert json.loads(history_file.read_text()) == [expected]
    assert errors == []


@pytest.mark.parametrize("target_env", [None, ""])
def test_record_deployment_omits_falsy_environment(tmp_path, target_env):
    records = []

    call_record(tmp_path / "history.json", records, [], target_env=target_env)

    assert "environment" not in records[0]


def test_record_deployment_includes_truthy_environment(tmp_path):
    records = []

    call_record(tmp_path / "history.json", records, [], target_env="staging")

    assert records[0]["environment"] == "staging"


def test_record_deployment_keeps_last_100_file_records_in_existing_order(tmp_path):
    history_file = tmp_path / "history.json"
    existing = [{"id": f"old-{index}"} for index in range(105)]
    history_file.write_text(json.dumps(existing))
    records = []

    call_record(history_file, records, [])

    persisted = json.loads(history_file.read_text())
    assert len(persisted) == 100
    assert persisted[0] == {"id": "old-6"}
    assert persisted[-1] == records[0]


def test_record_deployment_keeps_memory_record_when_json_is_malformed(tmp_path):
    history_file = tmp_path / "history.json"
    history_file.write_text("{not-json")
    records = []
    errors = []

    call_record(history_file, records, errors)

    assert len(records) == 1
    assert history_file.read_text() == "{not-json"
    assert len(errors) == 1
    assert errors[0].startswith("Failed to save deployment history: ")


def test_record_deployment_logs_non_list_history_without_overwriting(tmp_path):
    history_file = tmp_path / "history.json"
    history_file.write_text("{}")
    records = []
    errors = []

    call_record(history_file, records, errors)

    assert len(records) == 1
    assert history_file.read_text() == "{}"
    assert errors == [
        "Failed to save deployment history: 'dict' object has no attribute 'append'"
    ]


def test_record_deployment_does_not_swallow_memory_append_error(tmp_path):
    errors = []

    def fail_append(record):
        raise RuntimeError("memory unavailable")

    with pytest.raises(RuntimeError, match="memory unavailable"):
        record_deployment(
            "deploy-123",
            make_config(),
            "rolling",
            True,
            timedelta(seconds=1),
            append_record=fail_append,
            log_error=errors.append,
            now=lambda: FIXED_NOW,
            history_file=str(tmp_path / "history.json"),
        )

    assert errors == []


def test_mixin_wrapper_uses_legacy_filename_and_lazy_callbacks(monkeypatch):
    calls = []

    def fake_impl(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["append_record"]({"id": "sentinel"})
        kwargs["log_error"]("failure")
        return "result"

    monkeypatch.setattr(
        "dockerpilot.deployment_service._record_deployment_impl",
        fake_impl,
    )

    class Logger:
        def __init__(self):
            self.errors = []

        def error(self, message):
            self.errors.append(message)

    service = DeploymentServiceMixin()
    service.deployment_history = []
    service.logger = Logger()
    duration = timedelta(seconds=2)

    assert service._record_deployment(
        "deploy-1",
        make_config(),
        "canary",
        False,
        duration,
        target_env="prod",
    ) == "result"
    args, kwargs = calls[0]
    assert args[:5] == ("deploy-1", make_config(), "canary", False, duration)
    assert args[5] == "prod"
    assert kwargs["history_file"] == "deployment_history.json"
    assert kwargs["now"] == datetime.now
    assert service.deployment_history == [{"id": "sentinel"}]
    assert service.logger.errors == ["failure"]
