"""One-shot container monitoring snapshot extracted from MonitoringManager."""

import time
from typing import Any


def get_container_stats_once(host: Any, container_name: str) -> bool:
    """Get one-time container statistics snapshot (from dockerpilot-Lite)"""
    with host._error_handler(f"get stats for {container_name}", container_name):
        container = host.client.containers.get(container_name)

        # Get two measurements 1 second apart for accurate CPU calculation
        host.console.print(f"[cyan]📊 Collecting statistics for {container_name}...[/cyan]")

        stats1 = container.stats(stream=False)
        time.sleep(1)
        stats2 = container.stats(stream=False)

        # Calculate CPU percentage
        cpu_percent = 0.0
        try:
            cpu1_total = stats1['cpu_stats']['cpu_usage']['total_usage']
            cpu1_system = stats1['cpu_stats'].get('system_cpu_usage', 0)

            cpu2_total = stats2['cpu_stats']['cpu_usage']['total_usage']
            cpu2_system = stats2['cpu_stats'].get('system_cpu_usage', 0)

            cpu_delta = cpu2_total - cpu1_total
            system_delta = cpu2_system - cpu1_system

            online_cpus = len(stats2['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))

            if system_delta > 0 and cpu_delta >= 0:
                cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
        except (KeyError, ZeroDivisionError) as e:
            host.logger.warning(f"CPU calculation error: {e}")
            cpu_percent = 0.0

        # Memory statistics
        mem_usage = stats2['memory_stats'].get('usage', 0)
        mem_limit = stats2['memory_stats'].get('limit', 1)
        mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0

        # Network statistics
        network_stats = stats2.get('networks', {})
        rx_bytes = 0
        tx_bytes = 0
        for interface, net_data in network_stats.items():
            rx_bytes += net_data.get('rx_bytes', 0)
            tx_bytes += net_data.get('tx_bytes', 0)

        # Display results
        host.console.print(f"\n[bold cyan]📊 Container Statistics: {container_name}[/bold cyan]")
        host.console.print(f"[green]🖥️  CPU Usage: {cpu_percent:.2f}%[/green]")
        host.console.print(f"[blue]💾 Memory: {mem_usage/(1024*1024):.2f} MB / {mem_limit/(1024*1024):.2f} MB ({mem_percent:.2f}%)[/blue]")

        if rx_bytes > 0 or tx_bytes > 0:
            host.console.print(f"[magenta]🌐 Network RX: {rx_bytes/(1024*1024):.2f} MB, TX: {tx_bytes/(1024*1024):.2f} MB[/magenta]")

        # Process count
        if 'pids_stats' in stats2:
            pids = stats2['pids_stats'].get('current', 0)
            host.console.print(f"[yellow]⚡ Processes: {pids}[/yellow]")

        return True

    return False
