"""Process/bootstrap helpers for the DockerPilot composition root."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

import docker
import yaml
from rich.panel import Panel


def configure_console_streams() -> None:
    """Improve Windows console compatibility for Unicode-rich output."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def show_banner(console: Any) -> None:
    """Display the DockerPilot banner."""
    banner = r"""
  _____             _             _____ _ _       _
 |  __ \           | |           |  __ (_) |     | |
 | |  | | ___   ___| | _____ _ __| |__) || | ___ | |_
 | |  | |/ _ \ / __| |/ / _ \ '__|  ___/ | |/ _ \| __|
 | |__| | (_) | (__|   <  __/ |  | |   | | | (_) | |_
 |_____/ \___/ \___|_|\_\___|_|  |_|   |_|_|\___/ \__|

         by Dozey
    """
    console.print(Panel(banner, title="[bold blue]Docker Managing Tool[/bold blue]", title_align="center", border_style="blue"))
    console.print("[dim]Author: dozey | Version: Enhanced[/dim]\n")


def setup_logging(log_file: str, level: Any):
    """Configure and return the legacy DockerPilot logger."""
    log_format = "%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
    file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter(log_format))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger = logging.getLogger("DockerPilot")
    logger.setLevel(getattr(logging, level.value))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def load_config(logger: Any, config_file: str) -> dict:
    """Load YAML configuration with legacy error handling."""
    try:
        with open(config_file, "r", encoding="utf-8") as source:
            config = yaml.safe_load(source)
        logger.info(f"Configuration loaded from {config_file}")
        return config
    except Exception as exc:
        logger.error(f"Failed to load config: {exc}")
        return {}


def initialize_docker_client(console: Any, logger: Any, max_retries: int = 3) -> Optional[Any]:
    """Initialize docker-py using the active Docker CLI context when available."""
    for attempt in range(max_retries):
        try:
            base_url = None
            try:
                context = subprocess.check_output(
                    ["docker", "context", "show"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                ).strip()
                if context:
                    inspected = subprocess.check_output(
                        ["docker", "context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=3,
                    ).strip()
                    if inspected:
                        try:
                            base_url = json.loads(inspected)
                        except Exception:
                            base_url = inspected.strip('"')
            except Exception:
                base_url = None

            client = docker.DockerClient(base_url=base_url) if base_url else docker.from_env()
            client.ping()
            logger.info(
                "Docker client connected successfully "
                f"(base_url={getattr(client, 'api', None) and getattr(client.api, 'base_url', None)})"
            )
            return client
        except Exception as exc:
            error_msg = str(exc)
            error_type = type(exc).__name__
            logger.warning(f"Docker connection attempt {attempt + 1} failed ({error_type}): {error_msg}")
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to Docker daemon after {max_retries} attempts ({error_type}): {error_msg}")
                if console is not None:
                    console.print("[bold red]❌ Cannot connect to Docker daemon![/bold red]")
                return None
            time.sleep(2)
    return None
