"""System requirement validation for DockerPilot."""

from __future__ import annotations
from dockerpilot.ui_icons import icon

import shutil
import sys
from typing import Any


def validate_system_requirements(console: Any, client: Any) -> bool:
    """Validate Python, Docker, required modules, disk space and daemon access."""
    console.print("[cyan]Validating system requirements...[/cyan]")
    requirements_met = True

    python_version = sys.version_info
    if python_version < (3, 10):
        console.print(f"[red]{icon('error')} Python 3.10+ required[/red]")
        requirements_met = False
    else:
        console.print(f"[green]{icon('success')} Python {python_version.major}.{python_version.minor}[/green]")

    try:
        docker_version = client.version()
        console.print(f"[green]{icon('success')} Docker {docker_version['Version']}[/green]")
    except Exception as exc:
        console.print(f"[red]{icon('error')} Docker connection failed: {exc}[/red]")
        requirements_met = False

    for module in ("docker", "yaml", "requests", "rich", "pathlib"):
        try:
            __import__(module)
            console.print(f"[green]{icon('success')} Module {module}[/green]")
        except ImportError:
            console.print(f"[red]{icon('error')} Module {module} not found[/red]")
            requirements_met = False

    try:
        disk_usage = shutil.disk_usage(".")
        free_gb = disk_usage.free / (1024**3)
        if free_gb < 1:
            console.print(f"[red]{icon('error')} Insufficient disk space: {free_gb:.1f}GB[/red]")
            requirements_met = False
        else:
            console.print(f"[green]{icon('success')} Disk space: {free_gb:.1f}GB available[/green]")
    except Exception:
        console.print(f"[yellow]{icon('warning')} Could not check disk space[/yellow]")

    try:
        client.ping()
        console.print(f"[green]{icon('success')} Docker daemon accessible[/green]")
    except Exception:
        console.print(f"[red]{icon('error')} Docker daemon permission denied[/red]")
        console.print(
            "[yellow]Configure Docker access explicitly; do not modify docker.sock permissions manually. "
            "Docker-group membership grants root-equivalent host control.[/yellow]"
        )
        requirements_met = False

    if requirements_met:
        console.print(f"\n[bold green]{icon('success')} All system requirements met![/bold green]")
    else:
        console.print(f"\n[bold red]{icon('error')} Some requirements not met. Please fix and retry.[/bold red]")
    return requirements_met
