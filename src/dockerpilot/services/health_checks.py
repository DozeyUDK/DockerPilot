"""Standalone health-check operations."""

from __future__ import annotations
from dockerpilot.ui_icons import icon

import time
from typing import Any

import requests


def health_check_standalone(
    console: Any,
    port: int,
    endpoint: str = "/health",
    timeout: int = 30,
    max_retries: int = 10,
) -> bool:
    """Run the legacy standalone HTTP health-check flow."""
    url = f"http://localhost:{port}{endpoint}"
    console.print(f"[cyan]{icon('health')} Testing health check: {url}[/cyan]")

    for i in range(max_retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                console.print(f"[green]{icon('success')} Health check OK (attempt {i+1}/{max_retries})[/green]")
                console.print(f"[green]Response time: {response.elapsed.total_seconds():.2f}s[/green]")
                return True
            console.print(f"[yellow]{icon('warning')} Health check returned {response.status_code} (attempt {i+1}/{max_retries})[/yellow]")
        except requests.exceptions.RequestException as exc:
            console.print(f"[yellow]{icon('warning')} Health check failed (attempt {i+1}/{max_retries}): {exc}[/yellow]")

        if i < max_retries - 1:
            time.sleep(3)

    console.print(f"[red]{icon('error')} Health check failed after {max_retries} attempts[/red]")
    return False
