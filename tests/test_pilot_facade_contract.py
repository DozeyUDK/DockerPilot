import inspect
import types

import dockerpilot.pilot as pilot_module
from dockerpilot.pilot import DockerPilotEnhanced, LogLevel


def test_pilot_keeps_compatibility_surface_after_modularization():
    expected_methods = {
        "list_containers",
        "list_images",
        "container_operation",
        "run_new_container",
        "exec_container",
        "monitor_container_live",
        "get_container_stats_once",
        "stop_and_remove_container",
        "exec_command_non_interactive",
        "health_check_standalone",
        "show_deployment_history",
        "create_pipeline_config",
        "_create_github_actions_config",
        "_create_gitlab_ci_config",
        "_create_jenkins_config",
        "run_integration_tests",
        "setup_monitoring_alerts",
        "create_production_checklist",
        "generate_documentation",
        "validate_system_requirements",
        "export_configuration",
        "import_configuration",
        "_check_cancel_flag",
        "_update_progress",
        "_with_loading",
        "_get_database_config",
        "_get_database_name",
    }
    missing = expected_methods.difference(dir(DockerPilotEnhanced))
    assert not missing, f"Compatibility methods missing: {sorted(missing)}"


def test_runtime_compatibility_helpers_remain_context_managers():
    assert hasattr(DockerPilotEnhanced._with_loading, "__wrapped__")
    assert hasattr(DockerPilotEnhanced._error_handler, "__wrapped__")


def test_facade_methods_are_small_delegates_not_embedded_subsystems():
    # This is a regression guard against re-growing pilot.py with feature logic.
    for name in (
        "generate_documentation",
        "create_production_checklist",
        "validate_system_requirements",
        "export_configuration",
        "import_configuration",
        "exec_container",
        "monitor_container_live",
    ):
        source_lines = inspect.getsource(getattr(DockerPilotEnhanced, name)).splitlines()
        assert len(source_lines) <= 20, f"{name} grew back to {len(source_lines)} lines"


def test_setup_logging_preserves_legacy_none_return(monkeypatch):
    sentinel_logger = object()
    monkeypatch.setattr(pilot_module, "setup_logging_service", lambda *_args, **_kwargs: sentinel_logger)
    pilot = object.__new__(DockerPilotEnhanced)
    pilot.log_file = "docker_pilot.log"

    assert pilot._setup_logging(LogLevel.INFO) is None
    assert pilot.logger is sentinel_logger


def test_load_config_preserves_legacy_none_return(monkeypatch):
    expected = {"deployment": "demo"}
    monkeypatch.setattr(pilot_module, "load_config_service", lambda *_args, **_kwargs: expected)
    pilot = object.__new__(DockerPilotEnhanced)
    pilot.logger = object()

    assert pilot._load_config("deployment.yml") is None
    assert pilot.config is expected


def test_with_loading_still_dispatches_through_overridable_show_loading():
    pilot = object.__new__(DockerPilotEnhanced)
    calls = []

    def fake_loader(self, message, stop_event):
        calls.append(message)
        stop_event.wait(timeout=0.2)

    pilot._show_loading = types.MethodType(fake_loader, pilot)
    with pilot._with_loading("Migrating"):
        pass

    assert calls == ["Migrating"]
