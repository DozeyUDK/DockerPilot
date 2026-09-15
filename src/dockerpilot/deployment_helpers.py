"""Small deployment helpers extracted from :mod:`dockerpilot.deployment_service`."""

from dataclasses import fields
from collections.abc import Callable
from typing import Any, Optional

import docker

from .models import DeploymentConfig


NetworkGet = Callable[[str], Any]
Warn = Callable[[str], None]


def resolve_runtime_network(
    requested_network: Optional[str],
    *,
    get_network: NetworkGet,
    warn: Warn,
) -> Optional[str]:
    """Resolve a Docker network name using the legacy fallback rules."""
    if requested_network is None:
        return "bridge"

    network = str(requested_network).strip()
    if not network:
        return "bridge"
    if network in {"bridge", "host", "none"}:
        return network

    try:
        get_network(network)
        return network
    except docker.errors.NotFound:
        warn(
            f"Docker network '{network}' not found on current host. "
            "Falling back to 'bridge'."
        )
        return "bridge"
    except Exception as exc:
        warn(
            f"Could not validate docker network '{network}' ({exc}). "
            "Falling back to 'bridge'."
        )
        return "bridge"


def deployment_config_from_dict(deployment: dict, logger: Any) -> DeploymentConfig:
    """Build ``DeploymentConfig`` while preserving legacy normalization rules."""
    deployment = deployment or {}
    if not isinstance(deployment, dict):
        raise ValueError("deployment config must be a dictionary")

    normalized = dict(deployment)
    for key in ("volumes", "port_mapping", "environment", "build_args"):
        if normalized.get(key) is None or not isinstance(normalized.get(key), dict):
            normalized[key] = {}

    model_fields = {field.name for field in fields(DeploymentConfig)}
    extra_keys = sorted(key for key in normalized if key not in model_fields)
    if extra_keys:
        logger.warning(
            f"Ignoring unsupported deployment config field(s): {', '.join(extra_keys)}"
        )

    filtered = {key: value for key, value in normalized.items() if key in model_fields}
    return DeploymentConfig(**filtered)


def get_resource_limits(config: DeploymentConfig) -> dict:
    """Convert resource limits to Docker API format."""
    limits = {}

    if config.cpu_limit:
        # Preserve the legacy mixin behavior, including silently ignored parse errors.
        try:
            cpu_limit = float(config.cpu_limit) * 1000000000
            limits["nano_cpus"] = int(cpu_limit)
        except:
            pass

    if config.memory_limit:
        # Preserve the legacy binary-unit conversion semantics.
        try:
            memory_str = config.memory_limit.lower()
            if memory_str.endswith("g"):
                memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024 * 1024)
            elif memory_str.endswith("m"):
                memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024)
            else:
                memory_bytes = int(memory_str)

            limits["mem_limit"] = memory_bytes
        except:
            pass

    return limits


def normalize_volumes(volumes: Any, logger: Any) -> list:
    """Convert volumes from config format to Docker API format."""
    if not volumes:
        return []

    if isinstance(volumes, list):
        return volumes

    if not isinstance(volumes, dict):
        logger.warning(f"Volumes is not a dict or list, got {type(volumes)}: {volumes}")
        return []

    normalized = []
    for key, value in volumes.items():
        if isinstance(value, dict):
            if "bind" in value:
                bind_path = value["bind"]
                mode = value.get("mode", "rw")
                normalized.append(f"{key}:{bind_path}:{mode}")
            else:
                logger.warning(
                    f"Volume dict for '{key}' missing 'bind', skipping: {value}"
                )
        elif isinstance(value, str):
            if not key.startswith("/") and not key.startswith("./") and not key.startswith("../"):
                normalized.append(f"{key}:{value}")
            else:
                normalized.append(f"{key}:{value}")
        else:
            logger.warning(
                f"Unknown volume format for key '{key}': {type(value)} - {value}"
            )

    return normalized
