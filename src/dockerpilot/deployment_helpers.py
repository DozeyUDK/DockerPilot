"""Small deployment helpers extracted from :mod:`dockerpilot.deployment_service`."""

from typing import Any

from .models import DeploymentConfig


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
