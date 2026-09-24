"""System requirement validation for DockerPilot."""

from __future__ import annotations

import shutil
import sys
from typing import Any


def validate_system_requirements(console: Any, client: Any) -> bool:
    """Validate Python, Docker, required modules, disk space and daemon access."""
    console.print("[cyan]Validating system requirements...[/cyan]")
    requirements_met = True

    python_version = sys.version_info
    if python_version < (3, 10):
        console.print("[red]❌ Python 3.10+ required[/red]")
        requirements_met = False
    else:
        console.print(f"[green]✓ Python {python_version.major}.{python_version.minor}[/green]")

    try:
        docker_version = client.version()
        console.print(f"[green]✓ Docker {docker_version['Version']}[/green]")
    except Exception as exc:
        console.print(f"[red]❌ Docker connection failed: {exc}[/red]")
        requirements_met = False

    for module in ("docker", "yaml", "requests", "rich", "pathlib"):
        try:
            __import__(module)
            console.print(f"[green]✓ Module {module}[/green]")
        except ImportError:
            console.print(f"[red]❌ Module {module} not found[/red]")
            requirements_met = False

    try:
        disk_usage = shutil.disk_usage(".")
        free_gb = disk_usage.free / (1024**3)
        if free_gb < 1:
            console.print(f"[red]❌ Insufficient disk space: {free_gb:.1f}GB[/red]")
            requirements_met = False
        else:
            console.print(f"[green]✓ Disk space: {free_gb:.1f}GB available[/green]")
    except Exception:
        console.print("[yellow]⚠️ Could not check disk space[/yellow]")

    try:
        client.ping()
        console.print("[green]✓ Docker daemon accessible[/green]")
    except Exception:
        console.print("[red]❌ Docker daemon permission denied[/red]")
        console.print("[yellow]Try: sudo usermod -aG docker $USER[/yellow]")
        requirements_met = False

    if requirements_met:
        console.print("\n[bold green]✅ All system requirements met![/bold green]")
    else:
        console.print("\n[bold red]❌ Some requirements not met. Please fix and retry.[/bold red]")
    return requirements_met
