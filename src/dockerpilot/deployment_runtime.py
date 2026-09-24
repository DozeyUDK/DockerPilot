"""Small runtime-planning helpers shared by deployment strategies."""

from __future__ import annotations

from typing import Any, Callable, Mapping, MutableMapping, Optional


_INFRASTRUCTURE_IMAGE_MARKERS = (
    "minikube",
    "kicbase",
    "kubernetes",
    "k8s",
    "kind",
    "k3s",
    "k3d",
)


def offset_port_mapping(
    port_mapping: Optional[Mapping[Any, Any]],
    offset: int,
) -> dict[Any, str]:
    """Return host ports shifted by ``offset`` while preserving container-port keys."""
    if not port_mapping:
        return {}
    return {
        container_port: str(int(host_port) + offset)
        for container_port, host_port in port_mapping.items()
    }


def requires_privileged_mode(
    config: Any,
    active_container: Any = None,
    *,
    log_info: Callable[[str], None] = lambda _message: None,
    log_debug: Callable[[str], None] = lambda _message: None,
    active_copy_description: str = "new container",
) -> bool:
    """Determine whether a deployment container should run privileged.

    The decision intentionally mirrors the legacy deployment-service behavior:
    explicit configuration wins, known infrastructure images are auto-detected,
    and an active container's privileged setting is inherited when available.
    """
    if getattr(config, "privileged", False):
        return True

    image_lower = str(getattr(config, "image_tag", "")).lower()
    for marker in _INFRASTRUCTURE_IMAGE_MARKERS:
        if marker in image_lower:
            log_info(
                "Auto-detected infrastructure container requiring privileged mode: "
                f"{marker}"
            )
            return True

    if active_container is not None:
        try:
            active_privileged = active_container.attrs.get("HostConfig", {}).get(
                "Privileged", False
            )
            if active_privileged:
                log_info(
                    "Active container has privileged mode enabled, copying to "
                    f"{active_copy_description}"
                )
                return True
        except Exception as exc:
            log_debug(f"Could not check active container privileged mode: {exc}")

    return False


def apply_container_command(
    container_kwargs: MutableMapping[str, Any],
    config: Any,
) -> MutableMapping[str, Any]:
    """Apply the configured command or the legacy Alpine keepalive fallback."""
    command = getattr(config, "command", None)
    if command:
        container_kwargs["command"] = command
    elif "alpine" in str(getattr(config, "image_tag", "")).lower():
        container_kwargs["command"] = ["sh", "-c", "sleep 3600"]
    return container_kwargs
