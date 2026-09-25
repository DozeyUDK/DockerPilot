"""DockerPilot instance and remote-server inventory runtime helpers."""

from __future__ import annotations


def get_or_create_pilot(
    server_id: str,
    *,
    instances: dict,
    lock,
    config_dir,
    load_servers_config,
    pilot_cls,
    log_level,
    logger,
):
    """Return cached DockerPilot instance while preserving legacy remote fallback semantics."""
    with lock:
        if server_id in instances:
            return instances[server_id], server_id

        config_path = config_dir / "deployment.yml"
        config_path_str = str(config_path) if config_path.exists() else None

        if server_id != "local":
            config = load_servers_config()
            server_config = next(
                (server for server in config.get("servers", []) if server.get("id") == server_id),
                None,
            )
            if server_config:
                logger.warning(
                    "Remote server %s selected, but Docker SDK remote mode is not implemented; "
                    "using local Docker compatibility instance.",
                    server_id,
                )

        instance = pilot_cls(
            config_file=config_path_str,
            log_level=log_level,
            register_signal_handlers=False,
        )
        instances[server_id] = instance
        return instance, server_id


def get_selected_server_config(selected_server_id: str, *, load_servers_config, logger=None):
    if selected_server_id == "local":
        return None
    config = load_servers_config()
    for server in config.get("servers", []):
        if server.get("id") == selected_server_id:
            if logger:
                logger.info(
                    "Found server config for %s: %s",
                    selected_server_id,
                    server.get("hostname"),
                )
            return server
    if logger:
        logger.warning("Server %s not found in config, falling back to local", selected_server_id)
    return None


def get_server_config_by_id(server_id: str, *, load_servers_config):
    if server_id == "local":
        return {"id": "local"}
    config = load_servers_config()
    return next(
        (server for server in config.get("servers", []) if server.get("id") == server_id),
        None,
    )


def get_containers_and_images_for_server(server_config, *, execute_docker_command, logger=None) -> tuple:
    if server_config is None:
        server_config = {"id": "local"}
    containers = []
    images = []
    container_error = None
    image_error = None

    try:
        out = execute_docker_command(
            server_config,
            r"ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}'",
            check_exit_status=False,
        )
        for line in (out or "").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append(
                    {
                        "name": parts[0].lstrip("/"),
                        "image": parts[1],
                        "state": parts[2].lower(),
                        "status": parts[3],
                    }
                )
    except Exception as exc:
        container_error = str(exc)
        if logger:
            logger.warning(
                "Failed to get containers for server %s: %s",
                server_config.get("id", "?"),
                exc,
            )

    try:
        out = execute_docker_command(
            server_config,
            r"images --format '{{.Repository}}\t{{.Tag}}'",
            check_exit_status=False,
        )
        for line in (out or "").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] and parts[0] != "<none>":
                tag = f"{parts[0]}:{parts[1]}"
                if tag not in images:
                    images.append(tag)
    except Exception as exc:
        image_error = str(exc)
        if logger:
            logger.warning(
                "Failed to get images for server %s: %s",
                server_config.get("id", "?"),
                exc,
            )

    host_error = None
    if container_error or image_error:
        if not containers and not images:
            if container_error and image_error and container_error == image_error:
                host_error = container_error
            else:
                host_error = "; ".join(
                    message for message in (container_error, image_error) if message
                )
    return containers, images, host_error
