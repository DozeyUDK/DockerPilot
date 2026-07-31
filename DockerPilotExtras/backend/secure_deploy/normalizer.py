"""Deterministic Spec v1 → Compose JSON normalizer (no docker compose)."""

from __future__ import annotations

from typing import Any, Dict, List

from .errors import ValidationFailedError

_ALLOWED_EXPOSURE = {
    "none",
    "localhost",
    "lan_allowlist",
    "zerotier_allowlist",
    "public_via_existing_proxy",
}


def normalize_spec_to_compose(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a validated Spec into secret-free Compose JSON."""
    if not isinstance(spec, dict):
        raise ValidationFailedError("spec must be an object")

    metadata = spec.get("metadata") or {}
    image = spec.get("image") or {}
    runtime = spec.get("runtime") or {}
    network = spec.get("network") or {}
    storage = spec.get("storage") or {}
    health = spec.get("health") or {}
    secrets = spec.get("secrets") or {}

    service_name = metadata.get("service")
    if not service_name:
        raise ValidationFailedError("metadata.service is required")

    exposure = network.get("exposure")
    if exposure not in _ALLOWED_EXPOSURE:
        raise ValidationFailedError("unknown or unsupported exposure", code="unknown_exposure")

    for forbidden in ("privileged", "network_mode", "devices", "pid", "ipc", "group_add"):
        if forbidden in runtime:
            raise ValidationFailedError(f"forbidden runtime field: {forbidden}")

    digest = image.get("digest")
    reference = image.get("reference")
    if not digest or not reference:
        raise ValidationFailedError("image.reference and image.digest are required")
    if image.get("allow_mutable_tag") and not image.get("exception_ref"):
        raise ValidationFailedError("mutable tag requires exception_ref")

    image_ref = f"{reference}@{digest}"

    command = runtime.get("command")
    entrypoint = runtime.get("entrypoint")
    if isinstance(command, str) or isinstance(entrypoint, str):
        raise ValidationFailedError("command/entrypoint must be argv arrays")

    ports = _normalize_ports(network)
    volumes = _normalize_volumes(storage)
    networks = list(network.get("networks") or [])

    service: Dict[str, Any] = {
        "image": image_ref,
        "user": runtime.get("user"),
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": list(runtime.get("cap_drop") or ["ALL"]),
        "cap_add": list(runtime.get("cap_add") or []),
        "restart": runtime.get("restart_policy") or "unless-stopped",
        "healthcheck": {
            "test": list(health.get("test") or []),
            "interval": f"{int(health.get('interval_seconds') or 30)}s",
            "timeout": f"{int(health.get('timeout_seconds') or 5)}s",
            "retries": int(health.get("retries") or 3),
            "start_period": f"{int(health.get('start_period_seconds') or 0)}s",
        },
    }
    if runtime.get("init") is not None:
        service["init"] = bool(runtime.get("init"))
    if runtime.get("pids_limit") is not None:
        service["pids_limit"] = runtime["pids_limit"]
    if runtime.get("memory_limit"):
        service["mem_limit"] = runtime["memory_limit"]
    if runtime.get("cpu_limit"):
        service["cpus"] = runtime["cpu_limit"]
    if runtime.get("tmpfs"):
        service["tmpfs"] = [
            item["target"] if isinstance(item, dict) else item for item in runtime["tmpfs"]
        ]
    if command:
        service["command"] = list(command)
    if entrypoint:
        service["entrypoint"] = list(entrypoint)
    if ports:
        service["ports"] = ports
    if volumes:
        service["volumes"] = volumes
    if networks:
        service["networks"] = networks

    compose: Dict[str, Any] = {
        "name": metadata.get("project"),
        "services": {service_name: service},
        "x-dockerpilot-secret-refs": list((secrets.get("refs") or [])),
    }
    if networks:
        compose["networks"] = {name: {"internal": True} for name in networks}
    return compose


def _normalize_ports(network: Dict[str, Any]) -> List[Dict[str, Any]]:
    exposure = network.get("exposure")
    ports = network.get("published_ports") or []
    sources = network.get("allowed_sources") or []

    if exposure == "none" and ports:
        raise ValidationFailedError("exposure none forbids published ports")
    if exposure in {"lan_allowlist", "zerotier_allowlist"} and ports and not sources:
        raise ValidationFailedError("allowlist exposure requires allowed_sources")

    out: List[Dict[str, Any]] = []
    for port in ports:
        bind = str(port.get("bind_address") or "")
        if bind in {"0.0.0.0", "::", "*"}:
            if exposure == "localhost":
                raise ValidationFailedError("localhost exposure forbids wildcard bind")
            if exposure == "public_via_existing_proxy":
                raise ValidationFailedError(
                    "public_via_existing_proxy must not auto-publish on 0.0.0.0"
                )
            if exposure in {"lan_allowlist", "zerotier_allowlist"} and not sources:
                raise ValidationFailedError("wildcard bind without policy rejected")
            if bind == "::" and not any(":" in str(src) for src in sources):
                raise ValidationFailedError("IPv6 wildcard without IPv6 sources rejected")

        if exposure == "localhost":
            bind = "127.0.0.1"
        elif exposure == "public_via_existing_proxy":
            bind = "127.0.0.1"

        out.append(
            {
                "target": int(port["container_port"]),
                "published": int(port["host_port"]),
                "protocol": port.get("protocol") or "tcp",
                "host_ip": bind,
            }
        )
    return out


def _normalize_volumes(storage: Dict[str, Any]) -> List[Any]:
    out: List[Any] = []
    for volume in storage.get("volumes") or []:
        vtype = volume.get("type")
        target = volume.get("target")
        if vtype == "named_volume":
            source = volume.get("source")
            if not source:
                raise ValidationFailedError("named_volume requires source")
            mode = "ro" if volume.get("read_only") else "rw"
            out.append(f"{source}:{target}:{mode}")
        elif vtype == "bind":
            if not volume.get("approved_root_ref"):
                raise ValidationFailedError("bind requires approved_root_ref")
            source = volume.get("source")
            if source in {"/", "/etc"} or str(source).endswith("docker.sock"):
                raise ValidationFailedError("dangerous bind rejected")
            mode = "ro" if volume.get("read_only", True) else "rw"
            out.append(
                {
                    "type": "bind",
                    "source": source,
                    "target": target,
                    "read_only": mode == "ro",
                }
            )
        elif vtype == "tmpfs":
            out.append({"type": "tmpfs", "target": target})
        else:
            raise ValidationFailedError(f"unsupported volume type: {vtype}")
    return out
