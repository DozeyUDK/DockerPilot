from pathlib import Path

from dockerpilot.services.runtime_support import (
    get_database_config,
    get_database_name,
    load_health_check_defaults,
    parse_multi_target,
    update_progress,
)


class FakeLogger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


def test_parse_multi_target_trims_and_ignores_empty_values():
    assert parse_multi_target("web, api,, worker ") == ["web", "api", "worker"]
    assert parse_multi_target("") == []


def test_database_matching_prefers_longest_specific_name():
    defaults = {
        "database_services": {
            "sql": {"kind": "generic"},
            "postgresql": {"kind": "postgres"},
        }
    }
    logger = FakeLogger()
    assert get_database_name(defaults, "postgresql:17") == "postgresql"
    assert get_database_config(defaults, "postgresql:17", logger) == {"kind": "postgres"}


def test_health_defaults_fall_back_when_file_is_missing(tmp_path):
    logger = FakeLogger()
    defaults = load_health_check_defaults(logger, configs_dir=tmp_path)
    assert defaults["health_checks"]["default_endpoint"] == "/health"
    assert any(level == "warning" for level, _ in logger.messages)


def test_progress_callback_failures_are_swallowed_and_logged():
    logger = FakeLogger()

    def broken_callback(*_args):
        raise RuntimeError("callback failed")

    update_progress(broken_callback, logger, "deploy", 50, "halfway")
    assert any("callback failed" in message for level, message in logger.messages if level == "debug")
