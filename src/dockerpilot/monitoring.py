"""Monitoring and statistics operations."""
import time
import os
from contextlib import nullcontext
import json
from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import asdict
import docker
from rich.table import Table
from rich.live import Live

from .models import ContainerStats
from .utils import calculate_cpu_percent, get_trend_indicator, calculate_uptime
from .monitoring_stats import get_container_stats as _get_container_stats_impl
from .monitoring_snapshot import get_container_stats_once as _get_container_stats_once_impl
from .monitoring_live import monitor_container_live as _monitor_container_live_impl
from .monitoring_dashboard import (
    monitor_containers_dashboard as _monitor_containers_dashboard_impl,
    save_metrics_history as _save_metrics_history_impl,
    show_monitoring_summary as _show_monitoring_summary_impl,
)


class MonitoringManager:
    """Manages container monitoring and statistics."""
    
    def __init__(self, client, console, logger, metrics_file: str = "docker_metrics.json", error_handler=None):
        """Initialize monitoring manager."""
        self.client = client
        self.console = console
        self.logger = logger
        self.metrics_file = metrics_file
        self._error_handler = error_handler or (lambda *_args, **_kwargs: nullcontext())
    
    def get_container_stats(self, container_name: str) -> Optional[ContainerStats]:
        """Get comprehensive container statistics."""
        return _get_container_stats_impl(self, container_name)
    
    def monitor_containers_dashboard(self, containers: List[str] = None, duration: int = 300):
        """Real-time monitoring dashboard for multiple containers."""
        return _monitor_containers_dashboard_impl(self, containers, duration)
    
    def _save_metrics_history(self, metrics_history: Dict):
        """Save metrics history to file."""
        return _save_metrics_history_impl(self, metrics_history)
    
    def _show_monitoring_summary(self, metrics_history: Dict):
        """Show monitoring summary statistics."""
        return _show_monitoring_summary_impl(self, metrics_history)

    def get_container_stats_once(self, container_name: str) -> bool:
        """Get one-time container statistics snapshot (from dockerpilot-Lite)"""
        return _get_container_stats_once_impl(self, container_name)

    def monitor_container_live(self, container_name: str, duration: int = 30) -> bool:
        """Live monitoring with screen clearing (from dockerpilot-Lite)"""
        return _monitor_container_live_impl(self, container_name, duration)
