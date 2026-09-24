"""Container listing and presentation helpers."""

from typing import Any, List

from rich.panel import Panel
from rich.table import Table

from .utils import calculate_uptime, format_ports, get_container_size


def container_image_label(attrs: dict) -> str:
    """Return an image reference for display without inspecting the image."""
    if not attrs:
        return "none"
    cfg = attrs.get("Config") or {}
    ref = cfg.get("Image")
    if ref:
        return ref
    image_field = attrs.get("Image")
    if isinstance(image_field, str) and image_field.startswith("sha256:"):
        return image_field[7:19] + "…"
    if image_field:
        return image_field
    return "none"


def list_containers(host: Any, show_all: bool = True, format_output: str = "table") -> List[Any]:
    """List containers using the existing JSON or responsive table presentation."""
    with host._error_handler("list containers"):
        containers = host.client.containers.list(all=show_all)

        if format_output == "json":
            container_data = []
            for container in containers:
                state = container.attrs.get('State', {}).get('Status', container.status).lower()
                container_data.append({
                    'id': container.short_id,
                    'name': container.name,
                    'status': container.status,
                    'state': state,
                    'image': container_image_label(container.attrs),
                    'ports': container.ports,
                    'created': container.attrs['Created'],
                    'size': get_container_size(container),
                })
            return container_data

        terminal_width = host.console.width if hasattr(host.console, 'width') else 120
        available_width = max(80, terminal_width - 20)

        table = Table(
            title="🐳 Docker Containers",
            show_header=True,
            header_style="bold blue",
            expand=True,
            show_lines=False,
        )

        include_size_uptime = available_width >= 100

        if available_width >= 140:
            table.add_column("Nr", style="bold blue", width=4, overflow="fold")
            table.add_column("ID", style="cyan", width=12, overflow="fold")
            table.add_column("Name", style="green", width=min(25, int(available_width * 0.15)), overflow="fold")
            table.add_column("Status", style="magenta", width=10, overflow="fold")
            table.add_column("Image", style="yellow", width=min(30, int(available_width * 0.20)), overflow="fold")
            table.add_column("Ports", style="bright_blue", width=min(30, int(available_width * 0.20)), overflow="fold")
            table.add_column("Size", style="white", width=10, overflow="fold")
            table.add_column("Uptime", style="bright_green", width=12, overflow="fold")
        elif available_width >= 100:
            table.add_column("Nr", style="bold blue", width=3, overflow="fold")
            table.add_column("ID", style="cyan", width=10, overflow="fold")
            table.add_column("Name", style="green", width=min(20, int(available_width * 0.18)), overflow="fold")
            table.add_column("Status", style="magenta", width=8, overflow="fold")
            table.add_column("Image", style="yellow", width=min(25, int(available_width * 0.22)), overflow="fold")
            table.add_column("Ports", style="bright_blue", width=min(25, int(available_width * 0.22)), overflow="fold")
            table.add_column("Size", style="white", width=8, overflow="fold")
            table.add_column("Uptime", style="bright_green", width=10, overflow="fold")
        else:
            table.add_column("Nr", style="bold blue", width=3, overflow="fold")
            table.add_column("ID", style="cyan", width=8, overflow="fold")
            table.add_column("Name", style="green", width=min(18, int(available_width * 0.25)), overflow="fold")
            table.add_column("Status", style="magenta", width=7, overflow="fold")
            table.add_column("Image", style="yellow", width=min(20, int(available_width * 0.30)), overflow="fold")
            table.add_column("Ports", style="bright_blue", width=min(20, int(available_width * 0.30)), overflow="fold")

        for idx, container in enumerate(containers, start=1):
            status_color = "green" if container.status == "running" else "red" if container.status == "exited" else "yellow"
            status = f"[{status_color}]{container.status}[/{status_color}]"
            ports = format_ports(container.ports)
            row_data = [
                str(idx),
                container.short_id,
                container.name,
                status,
                container_image_label(container.attrs),
                ports,
            ]
            if include_size_uptime:
                size = get_container_size(container)
                uptime = calculate_uptime(container)
                row_data.extend([size, uptime])
            table.add_row(*row_data)

        host.console.print(table)

        running = len([container for container in containers if container.status == "running"])
        stopped = len([container for container in containers if container.status == "exited"])
        total = len(containers)
        summary = f"📊 Summary: {total} total, {running} running, {stopped} stopped"
        host.console.print(Panel(summary, style="bright_blue"))

        return containers
