"""Validation helpers used by deployment strategies."""

from __future__ import annotations

import time
from typing import Any, Callable

import requests


def comprehensive_container_validation(
    container: Any,
    config: Any,
    port: str,
    target_name: str,
    *,
    get_database_config: Callable[[str], dict],
    get_database_name: Callable[[str], Any],
    logger: Any,
    request_get: Callable[..., Any] = requests.get,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, str]:
    """Validate a candidate deployment before traffic is switched to it.

    This is a dependency-injected extraction of the legacy
    ``DeploymentServiceMixin._comprehensive_container_validation`` behavior.
    """
    validation_errors: list[str] = []

    # 1. Container status / restart stability.
    try:
        container.reload()
        status = container.status
        if status != "running":
            validation_errors.append(
                f"Container status is '{status}', expected 'running'"
            )
            return False, "; ".join(validation_errors)

        restart_count = (
            container.attrs.get("RestartCount", 0)
            if hasattr(container, "attrs")
            else 0
        )
        db_config = get_database_config(config.image_tag)
        is_database = len(db_config) > 0
        max_restarts = db_config.get("max_restart_count", 15) if is_database else 3

        if restart_count > max_restarts:
            validation_errors.append(
                "Container has restarted "
                f"{restart_count} times (possible crash loop, max allowed: {max_restarts})"
            )
            return False, "; ".join(validation_errors)

        if is_database and restart_count > 5:
            logger.info(
                f"Database container has {restart_count} restarts, checking stability..."
            )
            sleep(5)
            try:
                container.reload()
                if container.status != "running":
                    validation_errors.append(
                        "Container not stable after "
                        f"{restart_count} restarts (current status: {container.status})"
                    )
                    return False, "; ".join(validation_errors)
                new_restart_count = (
                    container.attrs.get("RestartCount", 0)
                    if hasattr(container, "attrs")
                    else 0
                )
                if new_restart_count > restart_count:
                    validation_errors.append(
                        "Container still restarting (restart count increased from "
                        f"{restart_count} to {new_restart_count})"
                    )
                    return False, "; ".join(validation_errors)
                logger.info(
                    f"Container appears stable after {restart_count} restarts"
                )
            except Exception as exc:
                logger.warning(f"Could not verify container stability: {exc}")
    except Exception as exc:
        validation_errors.append(f"Failed to check container status: {exc}")
        return False, "; ".join(validation_errors)

    # 2. HTTP health check, skipped for explicitly non-HTTP services.
    health_check_passed = False
    response_time = None
    if config.health_check_endpoint is None:
        logger.info("Skipping HTTP health check for non-HTTP service")
        health_check_passed = True
    else:
        try:
            url = f"http://localhost:{port}{config.health_check_endpoint}"
            start_time = clock()
            response = request_get(url, timeout=10)
            response_time = clock() - start_time
            if 200 <= response.status_code < 300:
                health_check_passed = True
                logger.info(
                    f"Health check passed: {response_time:.2f}s response time"
                )
            else:
                validation_errors.append(
                    "Health check returned status "
                    f"{response.status_code}, expected 200-299"
                )
        except requests.exceptions.Timeout:
            validation_errors.append("Health check timeout after 10s")
        except requests.exceptions.ConnectionError:
            validation_errors.append(
                "Health check connection error - service may not be ready"
            )
        except Exception as exc:
            validation_errors.append(f"Health check failed: {exc}")

        if not health_check_passed:
            return False, "; ".join(validation_errors)

    # 3. Logs / known critical patterns.
    try:
        logs = container.logs(tail=100).decode("utf-8", errors="ignore")
        db_config = get_database_config(config.image_tag)
        if db_config:
            log_patterns = db_config.get("log_patterns", {})
            loading_patterns = log_patterns.get("loading_shards", []) + log_patterns.get(
                "loading", []
            )
            for pattern in loading_patterns:
                if pattern.lower() in logs.lower():
                    db_name = get_database_name(config.image_tag) or "database"
                    logger.info(
                        f"{db_name} is loading data - this is normal and may take time"
                    )
                    break

        error_patterns = [
            "FATAL",
            "CRITICAL",
            "panic",
            "segmentation fault",
            "out of memory",
            "cannot bind",
            "address already in use",
            "permission denied",
            "access denied",
            "failed to start",
        ]
        oom_safe_patterns = [
            "oom detection",
            "configure.*oom",
            "disabling oom",
            "no oom",
            "could not configure.*oom",
            "unable to configure.*oom",
        ]

        found_errors: list[str] = []
        logs_lower = logs.lower()
        for pattern in error_patterns:
            if pattern.lower() in logs_lower:
                found_errors.append(pattern)

        if "oom" in logs_lower:
            is_safe_oom = any(
                safe_pattern in logs_lower for safe_pattern in oom_safe_patterns
            )
            if not is_safe_oom:
                oom_error_patterns = [
                    "killed.*oom",
                    "out of memory",
                    "oom killer",
                    "memory limit exceeded",
                ]
                if any(oom_err in logs_lower for oom_err in oom_error_patterns):
                    found_errors.append("OOM")
            else:
                logger.info(
                    "Found safe OOM detection warning (not a critical error)"
                )

        if found_errors:
            validation_errors.append(
                f"Found critical errors in logs: {', '.join(found_errors)}"
            )
            logger.warning(
                f"Warning: Found error patterns in logs: {', '.join(found_errors)}"
            )
    except Exception as exc:
        logger.warning(f"Could not check container logs: {exc}")

    # 4. Resource usage.
    try:
        stats = container.stats(stream=False)
        if "memory_stats" in stats:
            mem_usage = stats["memory_stats"].get("usage", 0)
            mem_limit = stats["memory_stats"].get("limit", 1)
            if mem_limit > 0:
                mem_percent = (mem_usage / mem_limit) * 100.0
                if mem_percent > 95:
                    validation_errors.append(
                        f"Memory usage critical: {mem_percent:.1f}%"
                    )
                elif mem_percent > 80:
                    logger.warning(f"Memory usage high: {mem_percent:.1f}%")
    except Exception as exc:
        logger.warning(f"Could not check resource usage: {exc}")

    # 5. Volume mounts.
    try:
        mounts = container.attrs.get("Mounts", [])
        if config.volumes:
            expected_volumes = (
                len(config.volumes)
                if isinstance(config.volumes, (dict, list))
                else 0
            )
            if len(mounts) < expected_volumes:
                validation_errors.append(
                    f"Expected {expected_volumes} volume(s), found {len(mounts)}"
                )
    except Exception as exc:
        logger.warning(f"Could not verify volumes: {exc}")

    # 6. Health response latency.
    if response_time:
        if response_time > 5.0:
            validation_errors.append(
                f"Health check response time too slow: {response_time:.2f}s"
            )
        elif response_time > 2.0:
            logger.warning(
                f"Health check response time slow: {response_time:.2f}s"
            )

    # 7. Final stability check.
    sleep(2)
    try:
        container.reload()
        if container.status != "running":
            validation_errors.append(
                f"Container status changed to '{container.status}' after validation"
            )
            return False, "; ".join(validation_errors)
    except Exception as exc:
        logger.warning(f"Could not re-check container status: {exc}")

    if validation_errors:
        return False, "; ".join(validation_errors)
    return True, "All validations passed"
