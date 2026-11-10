"""Regression coverage for DockerPilot Extras structured logging."""

import json
import io
import logging
import os
from pathlib import Path
import stat
import sys

import pytest

pytest.importorskip("flask")
EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from flask import Flask

from backend.logging_config import SafeConsoleFormatter, _PermissionedRotatingFileHandler, configure_logging
from backend.services.remote_failure_aggregator import RemoteFailureAggregator
from backend.services.remote_probe import run_remote_probe


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_jsonl_logging_redacts_secrets_and_uses_restrictive_permissions(tmp_path):
    app = Flask("logging-test")
    secret = "logging-secret-sentinel"
    log_path = configure_logging(
        app,
        log_dir=tmp_path,
        file_level=logging.DEBUG,
        console_level=logging.CRITICAL,
    )

    app.logger.info(
        "Remote operation failed password=%s",
        secret,
        extra={"event": "test_event", "server_id": "node-a", "operation": "probe"},
    )

    records = _read_json_lines(log_path)
    assert len(records) == 1
    assert records[0]["event"] == "test_event"
    assert records[0]["server_id"] == "node-a"
    assert secret not in log_path.read_text(encoding="utf-8")

    if os.name == "posix":
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_logging_configuration_is_idempotent(tmp_path):
    app = Flask("logging-idempotency-test")
    configure_logging(app, log_dir=tmp_path, console_level=logging.CRITICAL)
    configure_logging(app, log_dir=tmp_path, console_level=logging.CRITICAL)

    owned = [
        handler for handler in app.logger.handlers
        if getattr(handler, "_dockerpilot_extras_owned", False)
    ]
    assert len(owned) == 2


def test_request_logging_skips_successful_polling_and_adds_request_id(tmp_path):
    app = Flask("request-logging-test")
    log_path = configure_logging(
        app,
        log_dir=tmp_path,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
        slow_request_ms=10_000,
    )

    @app.get("/api/poll")
    def poll():
        return {"ok": True}

    @app.post("/api/change")
    def change():
        app.logger.info("State-changing operation completed", extra={"event": "state_change"})
        return {"ok": True}, 202

    @app.get("/api/remote-down")
    def remote_down():
        from flask import request

        request.environ["dockerpilot_extras.suppress_http_log"] = True
        return {"ok": False}, 503

    poll_response = app.test_client().get("/api/poll")
    assert poll_response.status_code == 200
    assert poll_response.headers.get("X-Request-ID")
    remote_response = app.test_client().get("/api/remote-down")
    assert remote_response.status_code == 503

    request_id = "request-123456"
    change_response = app.test_client().post(
        "/api/change",
        headers={"X-Request-ID": request_id},
    )
    assert change_response.status_code == 202
    assert change_response.headers["X-Request-ID"] == request_id

    records = _read_json_lines(log_path)
    http_records = [record for record in records if record.get("event") == "http_request"]
    assert len(http_records) == 1
    assert http_records[0]["request_id"] == request_id
    assert http_records[0]["http_method"] == "POST"
    assert http_records[0]["http_status"] == 202
    state_change_records = [record for record in records if record.get("event") == "state_change"]
    assert len(state_change_records) == 1
    assert state_change_records[0]["request_id"] == request_id


def test_remote_failure_aggregation_summarizes_repeats_and_logs_recovery(caplog):
    now = [100.0]
    logger = logging.getLogger("dockerpilot.tests.remote_failure_aggregation")
    aggregator = RemoteFailureAggregator(
        logger,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    server = {"id": "node-a"}

    with caplog.at_level(logging.INFO, logger=logger.name):
        aggregator.record_failure(server, "status_probe", OSError("No route to host"))
        now[0] += 1
        aggregator.record_failure(server, "status_probe", OSError("No route to host"))
        now[0] += 60
        aggregator.record_failure(server, "status_probe", OSError("No route to host"))
        now[0] += 4
        aggregator.record_success(server, "status_probe")

    events = [
        record for record in caplog.records
        if getattr(record, "event", None) in {
            "remote_operation_failed", "remote_operation_restored"
        }
    ]
    assert [record.event for record in events] == [
        "remote_operation_failed",
        "remote_operation_failed",
        "remote_operation_restored",
    ]
    assert events[1].occurrences == 2
    assert events[2].occurrences == 3
    assert events[2].downtime_seconds == 65.0


def test_remote_probe_logs_one_failure_without_command_text(caplog):
    logger = logging.getLogger("dockerpilot.tests.remote_probe")
    command = "docker inspect demo --env TOKEN=must-not-appear"

    def fail(*_args, **_kwargs):
        raise OSError("No route to host")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        output, error = run_remote_probe(
            {"id": "node-a"},
            command,
            execute_command_via_ssh=fail,
            logger=logger,
            attempts=3,
            retry_delay=0,
        )

    assert output == ""
    assert "No route to host" in error
    records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "remote_operation_failed"
    ]
    assert len(records) == 1
    assert command not in caplog.text
    assert "must-not-appear" not in caplog.text


def test_console_formatter_redacts_secret_assignments():
    secret = "console-secret-sentinel"
    record = logging.LogRecord(
        "dockerpilot.tests.console",
        logging.WARNING,
        __file__,
        1,
        "Remote operation failed password=%s",
        (secret,),
        None,
    )

    formatted = SafeConsoleFormatter("%(levelname)s %(message)s").format(record)
    assert secret not in formatted
    assert "redacted" in formatted


def test_jsonl_file_rotates_at_configured_size(tmp_path):
    app = Flask("logging-rotation-test")
    log_path = configure_logging(
        app,
        log_dir=tmp_path,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
        max_bytes=1024,
        backup_count=2,
    )

    for index in range(30):
        app.logger.info("rotation record %s %s", index, "x" * 120)

    assert log_path.exists()
    assert (tmp_path / "extras.jsonl.1").exists()
    assert len(list(tmp_path.glob("extras.jsonl*"))) <= 3


def test_auth_and_server_selection_logs_do_not_include_session_identifiers_or_usernames():
    server_source = (
        EXTRAS_DIR / "backend" / "resources" / "servers.py"
    ).read_text(encoding="utf-8")
    auth_source = (
        EXTRAS_DIR / "backend" / "resources" / "auth.py"
    ).read_text(encoding="utf-8")

    assert "session_id:" not in server_source
    assert "session now has:" not in server_source
    assert "Authenticated web session for user" not in auth_source
    assert 'extra={"event": "auth_session_created"}' in auth_source


def test_remote_failure_aggregation_keeps_distinct_operations_independent(caplog):
    now = [10.0]
    logger = logging.getLogger("dockerpilot.tests.remote_failure_independent")
    aggregator = RemoteFailureAggregator(
        logger,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    server = {"id": "node-a"}

    with caplog.at_level(logging.INFO, logger=logger.name):
        aggregator.record_failure(server, "docker_version", OSError("docker probe timeout"))
        aggregator.record_failure(server, "dockerpilot_version", OSError("DockerPilot probe timeout"))
        now[0] += 5
        aggregator.record_success(server, "docker_version")
        now[0] += 5
        aggregator.record_failure(server, "dockerpilot_version", OSError("DockerPilot probe timeout"))
        now[0] += 5
        aggregator.record_success(server, "dockerpilot_version")

    events = [
        record for record in caplog.records
        if getattr(record, "event", None) in {
            "remote_operation_failed", "remote_operation_restored"
        }
    ]
    assert [(record.event, record.operation) for record in events] == [
        ("remote_operation_failed", "docker_version"),
        ("remote_operation_failed", "dockerpilot_version"),
        ("remote_operation_restored", "docker_version"),
        ("remote_operation_restored", "dockerpilot_version"),
    ]
    assert events[-1].occurrences == 2
    assert events[-1].downtime_seconds == 15.0


def test_redaction_happens_before_records_propagate_to_root_handlers(tmp_path):
    app = Flask("logging-root-propagation-test")
    secret = "root-handler-secret-sentinel"
    log_path = configure_logging(
        app,
        log_dir=tmp_path,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
    )

    root = logging.getLogger()
    previous_level = root.level
    stream = io.StringIO()
    root_handler = logging.StreamHandler(stream)
    root_handler.setFormatter(logging.Formatter("%(message)s %(exc_text)s"))
    root.addHandler(root_handler)
    root.setLevel(logging.DEBUG)

    try:
        try:
            raise RuntimeError(f"password={secret}")
        except RuntimeError:
            app.logger.exception("Remote operation failed password=%s", secret)
    finally:
        root.removeHandler(root_handler)
        root_handler.close()
        root.setLevel(previous_level)

    root_output = stream.getvalue()
    file_output = log_path.read_text(encoding="utf-8")
    assert secret not in root_output
    assert secret not in file_output
    assert "redacted" in root_output
    assert "redacted" in file_output


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "O_NOFOLLOW"), reason="POSIX O_NOFOLLOW required")
def test_logging_refuses_symlink_log_file_without_touching_target(tmp_path):
    app = Flask("logging-symlink-file-test")
    log_dir = tmp_path / "logs"
    log_dir.mkdir(mode=0o700)
    target = tmp_path / "do-not-touch.txt"
    target.write_text("preserve-this-content", encoding="utf-8")
    (log_dir / "extras.jsonl").symlink_to(target)

    log_path = configure_logging(
        app,
        log_dir=log_dir,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
    )

    assert log_path is None
    assert target.read_text(encoding="utf-8") == "preserve-this-content"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permissions required")
def test_logging_refuses_symlink_log_directory_without_chmodding_target(tmp_path):
    app = Flask("logging-symlink-directory-test")
    target_dir = tmp_path / "existing-shared-directory"
    target_dir.mkdir()
    os.chmod(target_dir, 0o755)
    symlink_dir = tmp_path / "logs-link"
    try:
        symlink_dir.symlink_to(target_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    log_path = configure_logging(
        app,
        log_dir=symlink_dir,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
    )

    assert log_path is None
    assert stat.S_IMODE(target_dir.stat().st_mode) == 0o755
    assert not (target_dir / "extras.jsonl").exists()


def test_file_handler_write_failure_never_prints_the_original_record(capfd, tmp_path):
    app = Flask("logging-handler-error-test")
    secret = "disk-failure-secret-sentinel"
    log_path = configure_logging(
        app,
        log_dir=tmp_path,
        file_level=logging.INFO,
        console_level=logging.CRITICAL,
        max_bytes=0,
        backup_count=0,
    )
    assert log_path is not None

    handler = next(
        item for item in app.logger.handlers
        if isinstance(item, _PermissionedRotatingFileHandler)
    )
    original_stream = handler.stream

    class _FailingStream:
        def write(self, _value):
            raise OSError("simulated disk full")

        def flush(self):
            return None

    handler.stream = _FailingStream()
    record = logging.LogRecord(
        "dockerpilot.tests.file-handler-error",
        logging.ERROR,
        __file__,
        1,
        "Remote operation failed password=%s",
        (secret,),
        None,
    )
    try:
        handler.emit(record)
        stderr = capfd.readouterr().err
    finally:
        handler.stream = original_stream
        handler.close()

    assert "file logging failed" in stderr
    assert secret not in stderr
    assert "simulated disk full" not in stderr
