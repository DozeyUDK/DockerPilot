"""Shared runtime support used by deployment and backup compatibility methods."""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

import docker
import requests


_MINIMAL_HEALTH_DEFAULTS = {
    "health_checks": {
        "non_http_services": ["ssh", "redis", "mysql", "postgresql", "mongodb"],
        "endpoint_mappings": {},
        "default_endpoint": "/health",
    }
}


def check_cancel_flag(logger: Any, container_name: Optional[str]) -> bool:
    """Check legacy cancellation-flag locations and consume the first match."""
    if not container_name:
        return False
    locations = [
        Path.cwd() / f"cancel_{container_name}.flag",
        Path.home() / "DockerPilot" / f"cancel_{container_name}.flag",
        Path.home() / "DockerPilot" / ".dockerpilot_extras" / f"cancel_{container_name}.flag",
    ]
    for flag_path in locations:
        if flag_path.exists():
            logger.warning(f"Cancel flag detected for {container_name} at {flag_path}")
            try:
                flag_path.unlink()
            except Exception:
                pass
            return True
    return False


def load_health_check_defaults(logger: Any, configs_dir: Optional[Path] = None) -> dict:
    """Load health-check defaults with the legacy fallback payload."""
    defaults_path = (configs_dir or (Path(__file__).resolve().parents[1] / "configs")) / "health-checks-defaults.json"
    try:
        if defaults_path.exists():
            with open(defaults_path, "r", encoding="utf-8") as source:
                defaults = json.load(source)
            logger.debug(f"Loaded health check defaults from {defaults_path}")
            return defaults
        logger.warning(f"Health check defaults file not found: {defaults_path}")
    except Exception as exc:
        logger.error(f"Failed to load health check defaults: {exc}")
    return {
        "health_checks": {
            "non_http_services": list(_MINIMAL_HEALTH_DEFAULTS["health_checks"]["non_http_services"]),
            "endpoint_mappings": {},
            "default_endpoint": "/health",
        }
    }


def get_database_config(defaults: dict, image_tag: str, logger: Any = None) -> dict:
    """Return database-specific configuration for an image reference."""
    database_services = defaults.get("database_services", {})
    image_lower = image_tag.lower()
    for db_name in sorted(database_services.keys(), key=len, reverse=True):
        if db_name in image_lower:
            if logger is not None:
                logger.debug(f"Matched database service: {db_name} for image {image_tag}")
            return database_services[db_name]
    return {}


def get_database_name(defaults: dict, image_tag: str) -> str:
    """Return the matched database service name for an image reference."""
    database_services = defaults.get("database_services", {})
    image_lower = image_tag.lower()
    for db_name in sorted(database_services.keys(), key=len, reverse=True):
        if db_name in image_lower:
            return db_name
    return ""


def update_progress(callback: Optional[Callable[..., Any]], logger: Any, stage: str, progress: int, message: str) -> None:
    """Call the optional progress callback without allowing callback failures to escape."""
    if callback:
        try:
            callback(stage, progress, message)
        except Exception as exc:
            if logger is not None:
                logger.debug(f"Progress callback error: {exc}")


def show_loading(message: str = "Processing", stop_event: Optional[threading.Event] = None) -> None:
    """Render the legacy animated loading dots to stdout."""
    dots = [".", "..", "...", "...."]
    idx = 0
    while stop_event is None or not stop_event.is_set():
        sys.stdout.write(f"\r{message}{dots[idx % len(dots)]}")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.5)
    sys.stdout.write("\r" + " " * (len(message) + 4) + "\r")
    sys.stdout.flush()


@contextmanager
def loading_context(message: str = "Processing"):
    """Context manager that owns the legacy loading animation thread."""
    stop_event = threading.Event()
    loading_thread = threading.Thread(target=show_loading, args=(message, stop_event), daemon=True)
    loading_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        loading_thread.join(timeout=1.0)
        sys.stdout.write("\r" + " " * (len(message) + 4) + "\r")
        sys.stdout.flush()


@contextmanager
def error_context(console: Any, logger: Any, operation: str, container_name: Optional[str] = None):
    """Preserve DockerPilot's shared error-reporting context manager."""
    try:
        yield
    except docker.errors.NotFound:
        error_msg = f"Container/Image not found: {container_name or 'unknown'}"
        logger.error(f"{operation} failed: {error_msg}")
        console.print(f"[bold red]❌ {error_msg}[/bold red]")
    except docker.errors.APIError as exc:
        error_msg = f"Docker API error during {operation}: {exc}"
        logger.error(error_msg)
        console.print(f"[bold red]❌ {error_msg}[/bold red]")
    except requests.exceptions.RequestException as exc:
        error_msg = f"Network error during {operation}: {exc}"
        logger.error(error_msg)
        console.print(f"[bold red]❌ {error_msg}[/bold red]")
    except Exception as exc:
        error_msg = f"Unexpected error during {operation}: {exc}"
        logger.error(error_msg)
        console.print(f"[bold red]❌ {error_msg}[/bold red]")


def parse_multi_target(target_string: str) -> list[str]:
    """Parse a comma-separated container/image target list."""
    if not target_string:
        return []
    return [target.strip() for target in target_string.split(",") if target.strip()]
