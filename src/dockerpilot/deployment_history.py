"""Deployment history recording extracted from deployment services."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
import json

from rich.table import Table

from .models import DeploymentConfig


AppendRecord = Callable[[dict[str, Any]], None]
LogError = Callable[[str], None]
Now = Callable[[], datetime]


def record_deployment(
    deployment_id: str,
    config: DeploymentConfig,
    deployment_type: str,
    success: bool,
    duration: timedelta,
    target_env: Optional[str] = None,
    *,
    append_record: AppendRecord,
    log_error: LogError,
    now: Now,
    history_file: str = "deployment_history.json",
) -> None:
    """Append a deployment to memory and the legacy JSON history file."""
    deployment_record = {
        "id": deployment_id,
        "timestamp": now().isoformat(),
        "type": deployment_type,
        "image_tag": config.image_tag,
        "container_name": config.container_name,
        "success": success,
        "duration_seconds": duration.total_seconds(),
    }

    if target_env:
        deployment_record["environment"] = target_env

    append_record(deployment_record)

    try:
        history_data = []

        if Path(history_file).exists():
            with open(history_file, "r") as file:
                history_data = json.load(file)

        history_data.append(deployment_record)

        if len(history_data) > 100:
            history_data = history_data[-100:]

        with open(history_file, "w") as file:
            json.dump(history_data, file, indent=2)
    except Exception as exc:
        log_error(f"Failed to save deployment history: {exc}")


def show_deployment_history(console: Any, logger: Any, limit: int = 10, history_file: str = "deployment_history.json"):
    """Show deployment history"""

    if not Path(history_file).exists():
        console.print("[yellow]⚠️ No deployment history found[/yellow]")
        return

    try:
        with open(history_file, 'r') as f:
            history_data = json.load(f)

        # Sort by timestamp, most recent first
        history_data.sort(key=lambda x: x['timestamp'], reverse=True)
        history_data = history_data[:limit]

        table = Table(title="🚀 Deployment History", show_header=True)
        table.add_column("Date", style="cyan")
        table.add_column("ID", style="blue")
        table.add_column("Type", style="magenta")
        table.add_column("Image", style="yellow")
        table.add_column("Container", style="green")
        table.add_column("Status", style="bold")
        table.add_column("Duration", style="bright_blue")

        for record in history_data:
            timestamp = datetime.fromisoformat(record['timestamp']).strftime('%Y-%m-%d %H:%M')
            status = "[green]✅ Success[/green]" if record['success'] else "[red]❌ Failed[/red]"
            duration = f"{record['duration_seconds']:.1f}s"

            table.add_row(
                timestamp,
                record['id'][:12],
                record['type'],
                record['image_tag'],
                record['container_name'],
                status,
                duration
            )

        console.print(table)

    except Exception as e:
        logger.error(f"Failed to load deployment history: {e}")
        console.print(f"[red]❌ Error loading deployment history: {e}[/red]")
