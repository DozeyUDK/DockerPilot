"""Configuration archive import/export helpers."""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_FILES = (
    "deployment.yml",
    "alerts.yml",
    "integration-tests.yml",
    "docker_pilot.log",
    "docker_metrics.json",
    "deployment_history.json",
)


def export_configuration(
    console: Any,
    logger: Any,
    config_name: str = "docker-pilot-config.tar.gz",
) -> bool:
    """Export known DockerPilot configuration files as a gzip tar archive."""
    try:
        with tarfile.open(config_name, "w:gz") as archive:
            for config_file in DEFAULT_CONFIG_FILES:
                if Path(config_file).exists():
                    archive.add(config_file)
                    console.print(f"[green]Added {config_file}[/green]")
        console.print(f"[bold green]Configuration exported to {config_name}[/bold green]")
        return True
    except Exception as exc:
        logger.error(f"Configuration export failed: {exc}")
        return False


def import_configuration(console: Any, logger: Any, config_archive: str) -> bool:
    """Import configuration from a DockerPilot backup archive."""
    try:
        if not Path(config_archive).exists():
            console.print(f"[red]Archive not found: {config_archive}[/red]")
            return False

        with tarfile.open(config_archive, "r:gz") as archive:
            archive.extractall(".")
            console.print("[green]Configuration files imported[/green]")
            for member in archive.getmembers():
                if member.isfile():
                    console.print(f"[cyan]Imported: {member.name}[/cyan]")
        return True
    except Exception as exc:
        logger.error(f"Configuration import failed: {exc}")
        return False
