"""Characterization tests for DeploymentServiceMixin helper behavior."""

import pytest

from dockerpilot.deployment_service import DeploymentServiceMixin
from dockerpilot.models import DeploymentConfig


class RecordingLogger:
    """Minimal logger that records warning messages."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message) -> None:
        self.warnings.append(str(message))


def make_service() -> DeploymentServiceMixin:
    service = DeploymentServiceMixin()
    service.logger = RecordingLogger()
    return service


def make_config(*, cpu_limit=None, memory_limit=None) -> DeploymentConfig:
    return DeploymentConfig(
        image_tag="example:latest",
        container_name="example",
        port_mapping={},
        environment={},
        volumes={},
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
    )


def test_deployment_config_normalizes_mapping_fields_without_mutating_input():
    service = make_service()
    raw = {
        "image_tag": "example:v1",
        "container_name": "example",
        "port_mapping": None,
        "environment": ["INVALID=list"],
        "volumes": "invalid-volume",
        "build_args": None,
    }

    config = service._deployment_config_from_dict(raw)

    assert config.port_mapping == {}
    assert config.environment == {}
    assert config.volumes == {}
    assert config.build_args == {}
    assert raw["port_mapping"] is None
    assert raw["environment"] == ["INVALID=list"]
    assert raw["volumes"] == "invalid-volume"


def test_deployment_config_preserves_supported_optional_values():
    service = make_service()

    config = service._deployment_config_from_dict(
        {
            "image_tag": "example:v2",
            "container_name": "example",
            "port_mapping": {"80/tcp": "8080"},
            "environment": {"APP_ENV": "test"},
            "volumes": {"data": "/data"},
            "build_args": {"VERSION": "2"},
            "network": "app-network",
            "command": ["python", "app.py"],
        }
    )

    assert config.image_tag == "example:v2"
    assert config.port_mapping == {"80/tcp": "8080"}
    assert config.environment == {"APP_ENV": "test"}
    assert config.volumes == {"data": "/data"}
    assert config.build_args == {"VERSION": "2"}
    assert config.network == "app-network"
    assert config.command == ["python", "app.py"]


def test_deployment_config_warns_for_sorted_unknown_fields_and_ignores_them():
    service = make_service()

    config = service._deployment_config_from_dict(
        {
            "image_tag": "example:latest",
            "container_name": "example",
            "port_mapping": {},
            "environment": {},
            "volumes": {},
            "zeta": 1,
            "alpha": 2,
        }
    )

    assert not hasattr(config, "alpha")
    assert not hasattr(config, "zeta")
    assert service.logger.warnings == [
        "Ignoring unsupported deployment config field(s): alpha, zeta"
    ]


def test_deployment_config_rejects_truthy_non_dictionary_input():
    service = make_service()

    with pytest.raises(ValueError, match="deployment config must be a dictionary"):
        service._deployment_config_from_dict(["invalid"])


def test_deployment_config_preserves_falsy_non_dictionary_legacy_error():
    service = make_service()

    with pytest.raises(TypeError, match="required positional arguments"):
        service._deployment_config_from_dict([])


def test_get_resource_limits_converts_cpu_and_gigabyte_memory():
    service = make_service()

    limits = service._get_resource_limits(
        make_config(cpu_limit="1.5", memory_limit="1g")
    )

    assert limits == {
        "nano_cpus": 1_500_000_000,
        "mem_limit": 1_073_741_824,
    }


def test_get_resource_limits_accepts_megabytes_and_raw_bytes_case_insensitively():
    service = make_service()

    assert service._get_resource_limits(make_config(memory_limit="512M")) == {
        "mem_limit": 512 * 1024 * 1024
    }
    assert service._get_resource_limits(make_config(memory_limit="4096")) == {
        "mem_limit": 4096
    }


def test_get_resource_limits_silently_ignores_invalid_values():
    service = make_service()

    limits = service._get_resource_limits(
        make_config(cpu_limit="not-a-cpu", memory_limit="not-memory")
    )

    assert limits == {}


def test_normalize_volumes_returns_empty_list_for_falsy_input():
    service = make_service()

    assert service._normalize_volumes({}) == []
    assert service._normalize_volumes(None) == []
    empty_list: list = []
    result = service._normalize_volumes(empty_list)
    assert result == []
    # Legacy checks `if not volumes` before `isinstance(..., list)`.
    assert result is not empty_list


def test_normalize_volumes_returns_existing_list_unchanged():
    service = make_service()
    volumes = ["named:/data", "/host:/container:ro"]

    result = service._normalize_volumes(volumes)

    assert result is volumes


def test_normalize_volumes_preserves_mapping_order_and_formats_string_values():
    service = make_service()

    result = service._normalize_volumes(
        {
            "named_volume": "/data",
            "/host/path": "/container/path",
            "./relative": "/relative-target",
        }
    )

    assert result == [
        "named_volume:/data",
        "/host/path:/container/path",
        "./relative:/relative-target",
    ]


def test_normalize_volumes_formats_bind_dict_and_defaults_mode_to_rw():
    service = make_service()

    result = service._normalize_volumes(
        {
            "/host/a": {"bind": "/container/a", "mode": "ro"},
            "/host/b": {"bind": "/container/b"},
        }
    )

    assert result == [
        "/host/a:/container/a:ro",
        "/host/b:/container/b:rw",
    ]


def test_normalize_volumes_warns_and_skips_unsupported_entries():
    service = make_service()

    result = service._normalize_volumes(
        {
            "/missing-bind": {"mode": "ro"},
            "/unknown": 123,
        }
    )

    assert result == []
    assert len(service.logger.warnings) == 2
    assert "missing 'bind'" in service.logger.warnings[0]
    assert "Unknown volume format" in service.logger.warnings[1]


def test_normalize_volumes_warns_for_non_dict_non_list_input():
    service = make_service()

    result = service._normalize_volumes("named:/data")

    assert result == []
    assert len(service.logger.warnings) == 1
    assert "Volumes is not a dict or list" in service.logger.warnings[0]
