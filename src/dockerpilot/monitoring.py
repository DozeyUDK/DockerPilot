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
        try:
            container = self.client.containers.get(container_name)
            
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
            self.logger.error(f"Failed to get stats for {container_name}: {e}")
            return None
    
    def monitor_containers_dashboard(self, containers: List[str] = None, duration: int = 300):
        """Real-time monitoring dashboard for multiple containers."""
        if containers is None:
            # Monitor all running containers
            running_containers = [c.name for c in self.client.containers.list() if c.status == "running"]
            if not running_containers:
                self.console.print("[yellow]⚠️ No running containers found[/yellow]")
                return
            containers = running_containers
        
        self.console.print(f"[cyan]🔍 Starting monitoring dashboard for {len(containers)} containers[/cyan]")
        self.console.print(f"[yellow]Duration: {duration}s | Press Ctrl+C to stop[/yellow]\n")
        
        start_time = time.time()
        metrics_history = {name: [] for name in containers}
        
        try:
            with Live(console=self.console, refresh_per_second=1) as live:
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
                            container = self.client.containers.get(container_name)
                            stats = self.get_container_stats(container_name)
                            
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
            self.console.print("\n[yellow]⚠️ Monitoring stopped by user[/yellow]")
        
        # Save metrics to file
        self._save_metrics_history(metrics_history)
        
        # Show summary statistics
        self._show_monitoring_summary(metrics_history)
    
    def _save_metrics_history(self, metrics_history: Dict):
        """Save metrics history to file."""
        try:
            # Convert to serializable format
            serializable_data = {}
            for container, stats_list in metrics_history.items():
                serializable_data[container] = [asdict(stats) for stats in stats_list]
                # Convert datetime to string
                for stats in serializable_data[container]:
                    stats['timestamp'] = stats['timestamp'].isoformat()
            
            with open(self.metrics_file, 'w') as f:
                json.dump(serializable_data, f, indent=2)
            
            self.logger.info(f"Metrics history saved to {self.metrics_file}")
        except Exception as e:
            self.logger.error(f"Failed to save metrics: {e}")
    
    def _show_monitoring_summary(self, metrics_history: Dict):
        """Show monitoring summary statistics."""
        self.console.print("\n[bold cyan]📈 Monitoring Summary[/bold cyan]")
        
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
        
        self.console.print(summary_table)

    def get_container_stats_once(self, container_name: str) -> bool:
        """Get one-time container statistics snapshot (from dockerpilot-Lite)"""
        with self._error_handler(f"get stats for {container_name}", container_name):
            container = self.client.containers.get(container_name)

            # Get two measurements 1 second apart for accurate CPU calculation
            self.console.print(f"[cyan]📊 Collecting statistics for {container_name}...[/cyan]")

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
                self.logger.warning(f"CPU calculation error: {e}")
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
            self.console.print(f"\n[bold cyan]📊 Container Statistics: {container_name}[/bold cyan]")
            self.console.print(f"[green]🖥️  CPU Usage: {cpu_percent:.2f}%[/green]")
            self.console.print(f"[blue]💾 Memory: {mem_usage/(1024*1024):.2f} MB / {mem_limit/(1024*1024):.2f} MB ({mem_percent:.2f}%)[/blue]")

            if rx_bytes > 0 or tx_bytes > 0:
                self.console.print(f"[magenta]🌐 Network RX: {rx_bytes/(1024*1024):.2f} MB, TX: {tx_bytes/(1024*1024):.2f} MB[/magenta]")

            # Process count
            if 'pids_stats' in stats2:
                pids = stats2['pids_stats'].get('current', 0)
                self.console.print(f"[yellow]⚡ Processes: {pids}[/yellow]")

            return True

        return False

    def monitor_container_live(self, container_name: str, duration: int = 30) -> bool:
        """Live monitoring with screen clearing (from dockerpilot-Lite)"""
        with self._error_handler(f"live monitor {container_name}", container_name):
            container = self.client.containers.get(container_name)

            self.console.print(f"[cyan]Starting live monitoring for {container_name} ({duration}s)...[/cyan]")
            self.console.print(f"[yellow]Press Ctrl+C to stop[/yellow]\n")

            stats_stream = container.stats(stream=True)
            start_time = time.time()
            prev_stats = None

            try:
                for raw_stats in stats_stream:
                    current_time = time.time()
                    if current_time - start_time > duration:
                        break

                    try:
                        # Parse stats data
                        if isinstance(raw_stats, bytes):
                            stats = json.loads(raw_stats.decode('utf-8'))
                        elif isinstance(raw_stats, str):
                            stats = json.loads(raw_stats)
                        else:
                            stats = raw_stats

                        if not isinstance(stats, dict):
                            time.sleep(1)
                            continue

                        # Calculate CPU if we have previous measurement
                        cpu_percent = 0.0
                        if prev_stats and isinstance(prev_stats, dict):
                            try:
                                cpu_stats = stats.get('cpu_stats', {})
                                prev_cpu_stats = prev_stats.get('cpu_stats', {})

                                if 'cpu_usage' in cpu_stats and 'cpu_usage' in prev_cpu_stats:
                                    current_total = cpu_stats['cpu_usage'].get('total_usage', 0)
                                    prev_total = prev_cpu_stats['cpu_usage'].get('total_usage', 0)

                                    current_system = cpu_stats.get('system_cpu_usage', 0)
                                    prev_system = prev_cpu_stats.get('system_cpu_usage', 0)

                                    cpu_delta = current_total - prev_total
                                    system_delta = current_system - prev_system

                                    online_cpus = len(cpu_stats['cpu_usage'].get('percpu_usage', [1]))

                                    if system_delta > 0 and cpu_delta >= 0:
                                        cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
                            except (KeyError, ZeroDivisionError, TypeError):
                                cpu_percent = 0.0

                        # Memory stats
                        memory_stats = stats.get('memory_stats', {})
                        mem_usage = memory_stats.get('usage', 0) / (1024*1024)
                        mem_limit = memory_stats.get('limit', 1) / (1024*1024)
                        mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0

                        # Clear screen and display current stats
                        os.system('clear' if os.name == 'posix' else 'cls')
                        self.console.print(f"[bold cyan]📊 Live Monitoring: {container_name}[/bold cyan]")
                        self.console.print(f"[green]🖥️  CPU: {cpu_percent:.2f}%[/green]")
                        self.console.print(f"[blue]💾 RAM: {mem_usage:.1f}MB / {mem_limit:.1f}MB ({mem_percent:.1f}%)[/blue]")
                        self.console.print(f"[yellow]⏱️  Time: {int(current_time - start_time)}/{duration}s[/yellow]")
                        self.console.print(f"[dim]Press Ctrl+C to stop[/dim]")

                        prev_stats = stats

                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        self.logger.warning(f"Stats parsing error: {e}")
                        continue
                    except Exception as e:
                        self.logger.warning(f"Stats processing error: {e}")
                        continue

                    time.sleep(1)

                self.console.print(f"\n[green]✅ Live monitoring completed[/green]")
                return True

            except KeyboardInterrupt:
                self.console.print(f"\n[yellow]⚠️ Monitoring interrupted by user[/yellow]")
                return True

        return False
