"""Container statistics sampling extracted from MonitoringManager."""

import time
from datetime import datetime
from typing import Any, Optional

from .models import ContainerStats
from .utils import calculate_cpu_percent


def get_container_stats(host: Any, container_name: str) -> Optional[ContainerStats]:
    """Get comprehensive container statistics."""
    try:
        container = host.client.containers.get(container_name)

        # Get two measurements for accurate CPU calculation
        stats1 = container.stats(stream=False)
        time.sleep(1)
        stats2 = container.stats(stream=False)

        # Calculate CPU percentage
        cpu_percent = calculate_cpu_percent(stats1, stats2)

        # Memory statistics
        memory_stats = stats2.get('memory_stats', {})
        memory_usage = memory_stats.get('usage', 0) / (1024 * 1024)  # MB
        memory_limit = memory_stats.get('limit', 1) / (1024 * 1024)  # MB
        memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0

        # Network statistics
        networks = stats2.get('networks', {})
        rx_bytes = sum(net.get('rx_bytes', 0) for net in networks.values()) / (1024 * 1024)  # MB
        tx_bytes = sum(net.get('tx_bytes', 0) for net in networks.values()) / (1024 * 1024)  # MB

        # Process count
        pids = stats2.get('pids_stats', {}).get('current', 0)

        return ContainerStats(
            cpu_percent=cpu_percent,
            memory_usage_mb=memory_usage,
            memory_limit_mb=memory_limit,
            memory_percent=memory_percent,
            network_rx_mb=rx_bytes,
            network_tx_mb=tx_bytes,
            pids=pids,
            timestamp=datetime.now()
        )

    except Exception as e:
        host.logger.error(f"Failed to get stats for {container_name}: {e}")
        return None
