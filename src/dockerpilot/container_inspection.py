"""Container log and JSON inspection helpers."""

from typing import Any
import json

import docker
from rich.panel import Panel


def view_container_logs(host: Any, container_names: str = None, tail: int = 50):
    """View container logs, including comma-separated container names."""
    if container_names:
        if ',' in container_names:
            names_list = [name.strip() for name in container_names.split(',') if name.strip()]
        else:
            names_list = [container_names.strip()]

        for container_name in names_list:
            try:
                container = host.client.containers.get(container_name)
                logs = container.logs(tail=tail).decode()
                host.console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
                host.console.print(f"[cyan]Container: {container_name} - Last {tail} lines[/cyan]")
                host.console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
                host.console.print(logs)
            except docker.errors.NotFound:
                host.console.print(f"[red]Container '{container_name}' not found[/red]")
            except Exception as exc:
                host.console.print(f"[red]Error reading logs for '{container_name}': {exc}[/red]")
    else:
        containers = host.client.containers.list(all=True)
        if not containers:
            host.console.print("[red]No containers found[/red]")
            return

        host.console.print("\nSelect a container to view logs:")
        for idx, container in enumerate(containers, start=1):
            host.console.print(f"{idx}. {container.name} ({container.status})")

        choice = input("Enter number: ")
        try:
            idx = int(choice) - 1
            container = containers[idx]
            logs = container.logs(tail=tail).decode()
            host.console.print(f"\n[cyan]Showing last {tail} lines of {container.name} logs:[/cyan]\n")
            host.console.print(logs)
        except (ValueError, IndexError):
            host.console.print("[red]Invalid selection[/red]")


def view_container_json(host: Any, container_name: str):
    """Display container information in JSON format."""
    try:
        container = host.client.containers.get(container_name)
        data = container.attrs
        json_str = json.dumps(data, indent=4, ensure_ascii=False)
        host.console.print(Panel(json_str, title=f"Container JSON: {container_name}", expand=True))
    except docker.errors.NotFound:
        host.console.print(f"[red]Container '{container_name}' not found[/red]")
    except Exception as exc:
        host.console.print(f"[red]Error fetching JSON for container '{container_name}': {exc}[/red]")
