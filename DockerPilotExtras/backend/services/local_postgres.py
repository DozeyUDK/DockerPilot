"""Local PostgreSQL discovery/bootstrap helpers for DockerPilot Extras."""

from __future__ import annotations


def parse_env_list(env_list) -> dict[str, str]:
    env_map = {}
    for item in env_list or []:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            env_map[key] = value
    return env_map


def discover_local_postgres(
    container_name: str,
    *,
    default_schema: str,
    default_table_prefix: str,
    default_auto_create_schema: bool,
    sanitize_postgres_config,
) -> dict:
    try:
        import docker
    except ImportError as exc:
        return {"success": False, "error": f"Docker SDK not available: {exc}"}

    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        return {"success": False, "error": f"Container {container_name} not found"}
    except Exception as exc:
        return {"success": False, "error": f"Failed to inspect container: {exc}"}

    container.reload()
    attrs = container.attrs or {}
    env_map = parse_env_list((attrs.get("Config") or {}).get("Env") or [])
    ports = ((attrs.get("NetworkSettings") or {}).get("Ports") or {}).get("5432/tcp") or []
    host_port = ports[0].get("HostPort") if ports and isinstance(ports, list) and ports[0] else None
    postgres_cfg = {
        "host": "127.0.0.1",
        "port": int(host_port) if host_port else 5432,
        "database": env_map.get("POSTGRES_DB", "postgres"),
        "user": env_map.get("POSTGRES_USER", "postgres"),
        "password": env_map.get("POSTGRES_PASSWORD", ""),
        "sslmode": "prefer",
        "schema": default_schema,
        "table_prefix": default_table_prefix,
        "auto_create_schema": default_auto_create_schema,
        "container_name": container_name,
    }
    return {
        "success": True,
        "container": {
            "name": container.name,
            "status": container.status,
            "image": str(container.image.tags[0] if container.image.tags else container.image.id),
            "id": container.id,
        },
        "postgres": postgres_cfg,
        "postgres_sanitized": sanitize_postgres_config(postgres_cfg),
    }


def ensure_local_postgres_container(
    *,
    container_name: str,
    image: str,
    host_port: int,
    database: str,
    user: str,
    password: str,
    volume_name: str | None = None,
):
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError(f"Docker SDK not available: {exc}") from exc

    client = docker.from_env()
    created = False
    try:
        container = client.containers.get(container_name)
        if container.status != "running":
            container.start()
            container.reload()
    except docker.errors.NotFound:
        env = {
            "POSTGRES_DB": database,
            "POSTGRES_USER": user,
            "POSTGRES_PASSWORD": password,
        }
        volumes = {volume_name: {"bind": "/var/lib/postgresql/data", "mode": "rw"}} if volume_name else None
        container = client.containers.run(
            image=image,
            name=container_name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            environment=env,
            ports={"5432/tcp": int(host_port)},
            volumes=volumes,
        )
        created = True
    return container, created
