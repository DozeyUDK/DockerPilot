"""Monitoring dashboard, history persistence and summary rendering."""

import json
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List

import docker
from rich.live import Live
from rich.table import Table

from .utils import calculate_uptime, get_trend_indicator


def monitor_containers_dashboard(host: Any, containers: List[str] = None, duration: int = 300):
    """Real-time monitoring dashboard for multiple containers."""
    if containers is None:
        # Monitor all running containers
        running_containers = [c.name for c in host.client.containers.list() if c.status == "running"]
        if not running_containers:
            host.console.print("[yellow]⚠️ No running containers found[/yellow]")
            return
        containers = running_containers

    host.console.print(f"[cyan]🔍 Starting monitoring dashboard for {len(containers)} containers[/cyan]")
    host.console.print(f"[yellow]Duration: {duration}s | Press Ctrl+C to stop[/yellow]\n")

    start_time = time.time()
    metrics_history = {name: [] for name in containers}

    try:
        with Live(console=host.console, refresh_per_second=1) as live:
            while time.time() - start_time < duration:
                # Create dynamic table
                table = Table(title="📊 Container Monitoring Dashboard", show_header=True)
                table.add_column("Container", style="bold green", width=15)
                table.add_column("Status", style="bright_blue", width=10)
                table.add_column("CPU %", style="red", width=8)
                table.add_column("Memory", style="blue", width=15)
                table.add_column("Network I/O", style="magenta", width=15)
                table.add_column("PIDs", style="yellow", width=6)
                table.add_column("Uptime", style="bright_green", width=10)

                for container_name in containers:
                    try:
                        container = host.client.containers.get(container_name)
                        stats = host.get_container_stats(container_name)

                        if stats:
                            # Store metrics for trending
                            metrics_history[container_name].append(stats)
                            if len(metrics_history[container_name]) > 60:  # Keep last 60 measurements
                                metrics_history[container_name].pop(0)

                            # Status with color
                            status_color = "green" if container.status == "running" else "red"
                            status = f"[{status_color}]{container.status}[/{status_color}]"

                            # CPU with trending indicator
                            cpu_trend = get_trend_indicator(
                                [s.cpu_percent for s in metrics_history[container_name][-5:]]
                            )
                            cpu_display = f"{stats.cpu_percent:.1f}% {cpu_trend}"

                            # Memory display
                            memory_display = f"{stats.memory_usage_mb:.0f}MB ({stats.memory_percent:.1f}%)"

                            # Network I/O
                            network_display = f"↓{stats.network_rx_mb:.1f} ↑{stats.network_tx_mb:.1f}"

                            # Uptime
                            uptime = calculate_uptime(container)

                            table.add_row(
                                container_name,
                                status,
                                cpu_display,
                                memory_display,
                                network_display,
                                str(stats.pids),
                                uptime
                            )
                        else:
                            table.add_row(
                                container_name,
                                "[red]error[/red]",
                                "N/A",
                                "N/A",
                                "N/A",
                                "N/A",
                                "N/A"
                            )
                    except docker.errors.NotFound:
                        table.add_row(
                            container_name,
                            "[red]not found[/red]",
                            "N/A",
                            "N/A",
                            "N/A",
                            "N/A",
                            "N/A"
                        )

                # Add timestamp and remaining time
                elapsed = int(time.time() - start_time)
                remaining = duration - elapsed
                timestamp = datetime.now().strftime("%H:%M:%S")

                footer = f"🕐 {timestamp} | ⏱️ Remaining: {remaining}s | 📈 Collecting metrics..."
                table.caption = footer

                live.update(table)
                time.sleep(1)

    except KeyboardInterrupt:
        host.console.print("\n[yellow]⚠️ Monitoring stopped by user[/yellow]")

    # Save metrics to file
    host._save_metrics_history(metrics_history)

    # Show summary statistics
    host._show_monitoring_summary(metrics_history)


def save_metrics_history(host: Any, metrics_history: Dict):
    """Save metrics history to file."""
    try:
        # Convert to serializable format
        serializable_data = {}
        for container, stats_list in metrics_history.items():
            serializable_data[container] = [asdict(stats) for stats in stats_list]
            # Convert datetime to string
            for stats in serializable_data[container]:
                stats['timestamp'] = stats['timestamp'].isoformat()

        with open(host.metrics_file, 'w') as f:
            json.dump(serializable_data, f, indent=2)

        host.logger.info(f"Metrics history saved to {host.metrics_file}")
    except Exception as e:
        host.logger.error(f"Failed to save metrics: {e}")


def show_monitoring_summary(host: Any, metrics_history: Dict):
    """Show monitoring summary statistics."""
    host.console.print("\n[bold cyan]📈 Monitoring Summary[/bold cyan]")

    summary_table = Table(show_header=True, header_style="bold blue")
    summary_table.add_column("Container", style="green")
    summary_table.add_column("Avg CPU %", style="red")
    summary_table.add_column("Avg Memory %", style="blue")
    summary_table.add_column("Peak CPU %", style="yellow")
    summary_table.add_column("Peak Memory %", style="magenta")

    for container_name, stats_list in metrics_history.items():
        if stats_list:
            avg_cpu = sum(s.cpu_percent for s in stats_list) / len(stats_list)
            avg_memory = sum(s.memory_percent for s in stats_list) / len(stats_list)
            peak_cpu = max(s.cpu_percent for s in stats_list)
            peak_memory = max(s.memory_percent for s in stats_list)

            summary_table.add_row(
                container_name,
                f"{avg_cpu:.1f}%",
                f"{avg_memory:.1f}%",
                f"{peak_cpu:.1f}%",
                f"{peak_memory:.1f}%"
            )

    host.console.print(summary_table)
