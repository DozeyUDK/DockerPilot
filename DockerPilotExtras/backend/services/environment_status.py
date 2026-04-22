"""Environment status aggregation service."""

from __future__ import annotations

from typing import Callable, Dict


def build_environment_status(
    env_servers: Dict[str, str],
    get_server_config_by_id: Callable,
    get_containers_and_images_for_server: Callable,
):
    """Build server-first environment status payload."""
    environments = ["dev", "staging", "prod"]
    env_status = {}

    for env in environments:
        server_id = env_servers.get(env, "local")
        server_config = get_server_config_by_id(server_id)
        containers, images, host_error = get_containers_and_images_for_server(server_config)

        running = [c for c in containers if c.get("state") == "running"]
        stopped = [c for c in containers if c.get("state") != "running"]

        env_images = []
        for container in containers:
            tag = container.get("image")
            if tag and tag not in env_images:
                env_images.append(tag)
        if not env_images:
            env_images = list(images)[:5]
        env_images = env_images[:5]

        primary_image = env_images[0] if env_images else None
        server_label = "Local"
        if server_id != "local" and server_config:
            server_label = server_config.get("name") or server_config.get("hostname") or server_id

        if host_error and not containers and not images:
            lifecycle_status = "unavailable"
        else:
            lifecycle_status = "active" if running else ("inactive" if containers else "empty")

        env_status[env] = {
            "containers": {
                "total": len(containers),
                "running": len(running),
                "stopped": len(stopped),
                "list": containers[:5],
                "all": containers,
                "host_total": len(containers),
            },
            "images": env_images,
            "status": lifecycle_status,
            "primary_image": primary_image,
            "server_id": server_id,
            "server_label": server_label,
            "host_error": host_error,
            "scope_mode": "server-full",
            "bindings_count": 0,
            "bindings_explicit": False,
        }

    return {
        "success": True,
        "environments": env_status,
        "debug": {
            "env_servers": env_servers,
            "view_mode": "server-first",
        },
    }
