"""Health endpoint detection helpers for DockerPilot Extras."""

from __future__ import annotations


_NON_HTTP_KEYWORDS = (
    "ssh", "redis", "mariadb", "mysql", "postgres", "postgresql",
    "mongo", "mongodb", "db2", "memcached", "rabbitmq", "kafka",
    "zookeeper", "minikube", "kicbase", "kubernetes", "k8s", "kind",
    "k3s", "k3d",
)

_ENDPOINT_MAPPINGS = {
    "homeassistant": "/",
    "home-assistant": "/",
    "glances": "/",
    "grafana": "/api/health",
    "qdrant": "/healthz",
    "ollama": "/api/version",
    "prometheus": "/-/healthy",
    "influxdb": "/ready",
    "nextcloud": "/status.php",
    "elasticsearch": "/_cluster/health",
    "nginx": "/",
    "apache": "/",
    "traefik": "/ping",
    "portainer": "/api/status",
}


def detect_from_running_containers(image_tag: str, *, get_dockerpilot, logger):
    image_lower = image_tag.lower()
    try:
        pilot = get_dockerpilot()
        if pilot and pilot.client:
            running_containers = pilot.client.containers.list(filters={"status": "running"})
            running_services = set()
            for container in running_containers:
                container_image = container.image.tags[0] if container.image.tags else container.image.id
                service_name = container_image.split("/")[-1].split(":")[0].lower()
                running_services.add(service_name)

            logger.debug("Detected running services from containers: %s", sorted(running_services))
            for keyword in _NON_HTTP_KEYWORDS:
                if keyword in image_lower and any(keyword in service for service in running_services):
                    logger.info("Detected non-HTTP service '%s' from running containers", keyword)
                    return None

            for service_name in running_services:
                if service_name not in image_lower:
                    continue
                for pattern, endpoint in _ENDPOINT_MAPPINGS.items():
                    if pattern in service_name or service_name in pattern:
                        logger.info("Detected service '%s' -> endpoint '%s'", service_name, endpoint)
                        return endpoint

            for pattern, endpoint in _ENDPOINT_MAPPINGS.items():
                if pattern in image_lower:
                    logger.info("Detected image pattern '%s' -> endpoint '%s'", pattern, endpoint)
                    return endpoint
    except Exception as exc:  # best-effort dynamic fallback
        logger.debug("Could not inspect running containers for health detection: %s", exc)

    if any(keyword in image_lower for keyword in ("ssh", "redis", "mariadb", "mysql", "postgres", "mongo", "db2")):
        return None
    return "/health"


def detect_health_check_endpoint(image_tag: str, *, get_dockerpilot, logger):
    try:
        pilot = get_dockerpilot()
        return pilot._detect_health_check_endpoint(image_tag)
    except Exception as exc:
        logger.warning("Could not use pilot health check detection: %s; using dynamic fallback", exc)
        return detect_from_running_containers(
            image_tag,
            get_dockerpilot=get_dockerpilot,
            logger=logger,
        )
