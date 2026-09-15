"""HTTP health-check primitives extracted from deployment services."""

from collections.abc import Callable
from typing import Any
import time

import requests


RequestGet = Callable[..., Any]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


def detect_health_check_endpoint(
    image_tag: str,
    *,
    defaults: dict[str, Any],
    config: dict[str, Any],
    logger: Any,
) -> str | None:
    """Select an HTTP endpoint using the legacy image-name rules."""
    image_lower = image_tag.lower()
    default_health_checks = defaults.get("health_checks", {})
    user_health_checks = config.get("health_checks", {})

    non_http_services = user_health_checks.get(
        "non_http_services",
        default_health_checks.get("non_http_services", []),
    )
    endpoint_mappings = {
        **default_health_checks.get("endpoint_mappings", {}),
        **user_health_checks.get("endpoint_mappings", {}),
    }
    default_endpoint = user_health_checks.get(
        "default_endpoint",
        default_health_checks.get("default_endpoint", "/health"),
    )

    for service in non_http_services:
        if service in image_lower:
            logger.info(
                f"Detected non-HTTP service ({service}) - skipping HTTP health check"
            )
            return None

    infrastructure_services = [
        "minikube",
        "kicbase",
        "kubernetes",
        "k8s",
        "kind",
        "k3s",
        "k3d",
    ]
    for infra_service in infrastructure_services:
        if infra_service in image_lower:
            logger.info(
                f"Detected infrastructure service ({infra_service}) - skipping HTTP health check"
            )
            return None

    for image_pattern, endpoint in endpoint_mappings.items():
        if image_pattern.lower() in image_lower:
            logger.info(
                f"Detected image pattern '{image_pattern}' -> endpoint '{endpoint}'"
            )
            return endpoint

    logger.info(f"Using default health check endpoint: {default_endpoint}")
    return default_endpoint


def advanced_health_check(
    port: str,
    endpoint: str | None,
    timeout: int,
    max_retries: int,
    *,
    logger: Any,
    request_get: RequestGet | None = None,
    sleep: Sleep | None = None,
    clock: Clock | None = None,
) -> bool:
    """Run the legacy retrying HTTP health check.

    ``timeout`` remains part of the compatibility contract. The legacy behavior
    uses per-attempt timeouts of 10 seconds for attempts 1-3 and 5 seconds for
    later attempts, irrespective of that value.
    """
    del timeout

    if endpoint is None:
        logger.info("Skipping HTTP health check (non-HTTP service)")
        return True

    request_get = request_get or requests.get
    sleep = sleep or time.sleep
    clock = clock or time.time
    url = f"http://localhost:{port}{endpoint}"

    for attempt in range(max_retries):
        try:
            start_time = clock()
            request_timeout = 10 if attempt < 3 else 5
            response = request_get(url, timeout=request_timeout)
            response_time = clock() - start_time

            if 200 <= response.status_code < 300:
                logger.info(
                    f"Health check passed (attempt {attempt + 1}): "
                    f"{response_time:.2f}s (status {response.status_code})"
                )
                return True

            logger.warning(
                f"Health check returned {response.status_code} "
                f"(attempt {attempt + 1})"
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Health check failed (attempt {attempt + 1}): {exc}")

        if attempt < max_retries - 1:
            wait_time = 5 if attempt < 3 else 3
            sleep(wait_time)

    return False
