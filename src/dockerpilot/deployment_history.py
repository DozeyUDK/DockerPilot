"""Deployment history recording extracted from deployment services."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
import json

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
