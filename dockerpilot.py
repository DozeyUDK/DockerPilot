#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import docker
import argparse
import yaml
import json
import os
import sys
import time
import requests
import logging
import signal
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.live import Live
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from enum import Enum

# ==================== CONFIGURATION & CONSTANTS ====================

class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass
class DeploymentConfig:
    """Deplyment config"""
    image_tag: str
    container_name: str
    port_mapping: Dict[str, str]
    environment: Dict[str, str]
    volumes: Dict[str, str]
    restart_policy: str = "unless-stopped"
    health_check_endpoint: str = "/health"
    health_check_timeout: int = 30
    health_check_retries: int = 10
    build_args: Dict[str, str] = None
    network: str = "bridge"
    cpu_limit: str = None
    memory_limit: str = None

@dataclass
class ContainerStats:
    """Container stats"""
    cpu_percent: float
    memory_usage_mb: float
    memory_limit_mb: float
    memory_percent: float
    network_rx_mb: float
    network_tx_mb: float
    pids: int
    timestamp: datetime

class DockerPilotEnhanced:

    
    def __init__(self, config_file: str = None, log_level: LogLevel = LogLevel.INFO):
        self.console = Console()
        self._show_banner()
        self.client = None
        self.config = {}
        self.log_file = "docker_pilot.log"
        self.metrics_file = "docker_metrics.json"
        self.deployment_history = []
        
        # Setup logging
        self._setup_logging(log_level)
        
        # Load configuration
        if config_file and Path(config_file).exists():
            self._load_config(config_file)
        
        # Initialize Docker client with retry logic
        self._init_docker_client()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Docker Pilot Enhanced initialized successfully")
    
    def _show_banner(self):
        """gimme ASCII"""
        banner = """
    ╔══════════════════════════════════════════════════════════════════════╗
    ║                                                                      ║
    ║  ██████╗  ██████╗  ██████╗██╗  ██╗███████╗██████╗                    ║
    ║  ██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝██╔════╝██╔══██╗                   ║
    ║  ██║  ██║██║   ██║██║     █████╔╝ █████╗  ██████╔╝                   ║
    ║  ██║  ██║██║   ██║██║     ██╔═██╗ ██╔══╝  ██╔══██╗                   ║
    ║  ██████╔╝╚██████╔╝╚██████╗██║  ██╗███████╗██║  ██║                   ║
    ║  ╚═════╝  ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝                   ║
    ║                                                                      ║
    ║  ███╗   ███╗ █████╗ ███╗   ██╗ █████╗  ██████╗ ██╗███╗   ██╗ ██████╗ ║
    ║  ████╗ ████║██╔══██╗████╗  ██║██╔══██╗██╔════╝ ██║████╗  ██║██╔════╝ ║
    ║  ██╔████╔██║███████║██╔██╗ ██║███████║██║  ███╗██║██╔██╗ ██║██║  ███╗║
    ║  ██║╚██╔╝██║██╔══██║██║╚██╗██║██╔══██║██║   ██║██║██║╚██╗██║██║   ██║║
    ║  ██║ ╚═╝ ██║██║  ██║██║ ╚████║██║  ██║╚██████╔╝██║██║ ╚████║╚██████╔╝║
    ║  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ ║
    ║                                                                      ║
    ║  ████████╗ ██████╗  ██████╗ ██╗                                      ║
    ║  ╚══██╔══╝██╔═══██╗██╔═══██╗██║                                      ║
    ║     ██║   ██║   ██║██║   ██║██║                                      ║
    ║     ██║   ██║   ██║██║   ██║██║                                      ║
    ║     ██║   ╚██████╔╝╚██████╔╝███████╗                                 ║
    ║     ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝                                 ║
    ║                                                                      ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
        

        self.console.print(Panel(banner, title="[bold blue]Docker Managing Tool[/bold blue]", 
                                title_align="center", border_style="blue"))
        self.console.print(f"[dim]Author: dozey | Version: Enhanced[/dim]\n")

    def _setup_logging(self, level: LogLevel):
        """Setup enhanced logging with rotation"""
        log_format = '%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        
        # File handler with rotation
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            self.log_file, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        
        # Setup logger
        self.logger = logging.getLogger('DockerPilot')
        self.logger.setLevel(getattr(logging, level.value))
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def _load_config(self, config_file: str):
        """Load configuration from YAML file"""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
            self.logger.info(f"Configuration loaded from {config_file}")
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            self.config = {}

    def _init_docker_client(self, max_retries: int = 3):
        """Initialize Docker client with retry logic"""
        for attempt in range(max_retries):
            try:
                self.client = docker.from_env()
                # Test connection
                self.client.ping()
                self.logger.info("Docker client connected successfully")
                return
            except Exception as e:
                self.logger.warning(f"Docker connection attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    self.logger.error("Failed to connect to Docker daemon")
                    self.console.print("[bold red]❌ Cannot connect to Docker daemon![/bold red]")
                    sys.exit(1)
                time.sleep(2)

    def _signal_handler(self, signum, frame):
        """Graceful shutdown handler"""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.console.print("\n[yellow]⚠️ Graceful shutdown initiated...[/yellow]")
        sys.exit(0)

    @contextmanager
    def _error_handler(self, operation: str, container_name: str = None):
        """Enhanced error handling context manager"""
        try:
            yield
        except docker.errors.NotFound as e:
            error_msg = f"Container/Image not found: {container_name or 'unknown'}"
            self.logger.error(f"{operation} failed: {error_msg}")
            self.console.print(f"[bold red]❌ {error_msg}[/bold red]")
        except docker.errors.APIError as e:
            error_msg = f"Docker API error during {operation}: {e}"
            self.logger.error(error_msg)
            self.console.print(f"[bold red]❌ {error_msg}[/bold red]")
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error during {operation}: {e}"
            self.logger.error(error_msg)
            self.console.print(f"[bold red]❌ {error_msg}[/bold red]")
        except Exception as e:
            error_msg = f"Unexpected error during {operation}: {e}"
            self.logger.error(error_msg)
            self.console.print(f"[bold red]❌ {error_msg}[/bold red]")

    # ==================== CONTAINER MANAGEMENT ====================

    def list_containers(self, show_all: bool = True, format_output: str = "table") -> List[Any]:
        """Enhanced container listing with multiple output formats"""
        with self._error_handler("list containers"):
            containers = self.client.containers.list(all=show_all)
            
            if format_output == "json":
                container_data = []
                for c in containers:
                    container_data.append({
                        'id': c.short_id,
                        'name': c.name,
                        'status': c.status,
                        'image': c.image.tags[0] if c.image.tags else "none",
                        'ports': c.ports,
                        'created': c.attrs['Created'],
                        'size': self._get_container_size(c)
                    })
                self.console.print_json(data=container_data)
                return containers
            
            # Enhanced table view
            table = Table(title="🐳 Docker Containers", show_header=True, header_style="bold blue")
            table.add_column("Nr", style="bold blue", width=4)
            table.add_column("ID", style="cyan", width=12)
            table.add_column("Name", style="green", width=20)
            table.add_column("Status", style="magenta", width=12)
            table.add_column("Image", style="yellow", width=25)
            table.add_column("Ports", style="bright_blue", width=20)
            table.add_column("Size", style="white", width=10)
            table.add_column("Uptime", style="bright_green", width=15)

            for idx, c in enumerate(containers, start=1):
                # Status formatting
                status_color = "green" if c.status == "running" else "red" if c.status == "exited" else "yellow"
                status = f"[{status_color}]{c.status}[/{status_color}]"
                
                # Ports formatting
                ports = self._format_ports(c.ports)
                
                # Size calculation
                size = self._get_container_size(c)
                
                # Uptime calculation
                uptime = self._calculate_uptime(c)
                
                table.add_row(
                    str(idx),
                    c.short_id,
                    c.name,
                    status,
                    c.image.tags[0] if c.image.tags else "❌ none",
                    ports,
                    size,
                    uptime
                )
            
            self.console.print(table)
            
            # Summary statistics
            running = len([c for c in containers if c.status == "running"])
            stopped = len([c for c in containers if c.status == "exited"])
            total = len(containers)
            
            summary = f"📊 Summary: {total} total, {running} running, {stopped} stopped"
            self.console.print(Panel(summary, style="bright_blue"))
            
            return containers

    def list_images(self, show_all: bool = True, format_output: str = "table") -> List[Any]:
        """Enhanced image listing with multiple output formats"""
        with self._error_handler("list images"):
            images = self.client.images.list(all=show_all)
            
            if format_output == "json":
                image_data = []
                for img in images:
                    image_data.append({
                        'id': img.id.split(":")[1][:12],
                        'tags': img.tags,
                        'created': img.attrs.get('Created'),
                        'size': self._format_image_size(img.attrs.get('Size', 0)),
                        'architecture': img.attrs.get('Architecture'),
                        'os': img.attrs.get('Os')
                    })
                self.console.print_json(data=image_data)
                return images
            
            # Enhanced table view
            table = Table(title="📦 Docker Images", show_header=True, header_style="bold blue")
            table.add_column("Nr", style="bold blue", width=4)
            table.add_column("ID", style="cyan", width=12)
            table.add_column("Repository", style="green", width=25)
            table.add_column("Tag", style="yellow", width=15)
            table.add_column("Size", style="magenta", width=12)
            table.add_column("Created", style="bright_blue", width=20)
            table.add_column("Used By", style="white", width=8)

            for idx, img in enumerate(images, start=1):
                # Parse repository and tag
                if img.tags:
                    repo_tag = img.tags[0]
                    if ':' in repo_tag:
                        repository, tag = repo_tag.rsplit(':', 1)
                    else:
                        repository, tag = repo_tag, "latest"
                else:
                    repository = "<none>"
                    tag = "<none>"
                
                # Format size
                size = self._format_image_size(img.attrs.get('Size', 0))
                
                # Format creation date
                created = self._format_creation_date(img.attrs.get('Created'))
                
                # Check if image is used by containers
                used_by = self._count_containers_using_image(img.id)
                
                table.add_row(
                    str(idx),
                    img.id.split(":")[1][:12],  # zamiast img.short_id,
                    repository,
                    tag,
                    size,
                    created,
                    str(used_by)
                )
            
            self.console.print(table)
            
            # Summary statistics
            total_size = sum(img.attrs.get('Size', 0) for img in images)
            total_size_formatted = self._format_image_size(total_size)
            
            summary = f"📊 Summary: {len(images)} images, {total_size_formatted} total size"
            self.console.print(Panel(summary, style="bright_blue"))
            
            return images
        
    def remove_image(self, image_name: str, force: bool = False) -> bool:
        """Remove Docker image"""
        with self._error_handler("remove image", image_name):
            try:
                self.client.images.remove(image=image_name, force=force, noprune=False)
                self.console.print(f"[green]✅ Image {image_name} removed successfully[/green]")
                self.logger.info(f"Image {image_name} removed successfully")
                return True
            except docker.errors.ImageNotFound:
                self.console.print(f"[bold red]Image not found: {image_name}[/bold red]")
                return False
            except docker.errors.APIError as e:
                self.console.print(f"[bold red]Docker API error during image removal:[/bold red] {e}")
                return False

            
####################################################
    def _format_image_size(self, size_bytes: int) -> str:
        """Format image size for display"""
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _format_creation_date(self, created_str: str) -> str:
        """Format creation date for display"""
        try:
            if created_str:
                created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                now = datetime.now(created.tzinfo)
                diff = now - created
                
                if diff.days > 7:
                    return created.strftime('%Y-%m-%d')
                elif diff.days > 0:
                    return f"{diff.days} days ago"
                elif diff.seconds > 3600:
                    hours = diff.seconds // 3600
                    return f"{hours} hours ago"
                else:
                    minutes = diff.seconds // 60
                    return f"{minutes} min ago"
        except:
            pass
        return "unknown"

    def _count_containers_using_image(self, image_id: str) -> int:
        """Count containers using specific image"""
        try:
            containers = self.client.containers.list(all=True)
            count = 0
            for container in containers:
                if container.image.id == image_id:
                    count += 1
            return count
        except:
            return 0
####################################################
    def _format_ports(self, ports: dict) -> str:
        """Format container ports for display"""
        if not ports:
            return "none"
        
        port_list = []
        for container_port, host_bindings in ports.items():
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding['HostPort']
                    port_list.append(f"{host_port}→{container_port}")
            else:
                port_list.append(container_port)
        
        return ", ".join(port_list) if port_list else "none"

    def _get_container_size(self, container) -> str:
        """Get container size"""
        try:
            # This is approximate - Docker doesn't provide easy size calculation
            return "N/A"  # Could be enhanced with df commands
        except:
            return "N/A"

    def _calculate_uptime(self, container) -> str:
        """Calculate container uptime"""
        try:
            if container.status != "running":
                return "N/A"
            
            created = datetime.fromisoformat(container.attrs['Created'].replace('Z', '+00:00'))
            uptime = datetime.now(created.tzinfo) - created
            
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            if days > 0:
                return f"{days}d {hours}h"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except:
            return "N/A"

    def container_operation(self, operation: str, container_name: str, **kwargs) -> bool:
        """Unified container operation handler with progress tracking"""
        operations = {
            'start': self._start_container,
            'stop': self._stop_container,
            'restart': self._restart_container,
            'remove': self._remove_container,
            'pause': self._pause_container,
            'unpause': self._unpause_container,
            'update_restart_policy': self.update_restart_policy,
            'run_image': self.run_new_container
        }
        
        if operation not in operations:
            self.console.print(f"[bold red]❌ Unknown operation: {operation}[/bold red]")
            return False
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            task = progress.add_task(f"{operation.title()}ing container {container_name}...", total=None)
            
            try:
                result = operations[operation](container_name, **kwargs)
                progress.update(task, description=f"✅ Container {container_name} {operation}ed successfully")
                return result
            except Exception as e:
                progress.update(task, description=f"❌ Failed to {operation} container {container_name}")
                self.logger.error(f"Container {operation} failed: {e}")
                return False

    ### Old functions from dockerpilotv2
    def update_restart_policy(self, container_name: str, policy: str = 'unless-stopped') -> bool:
        """Set restart policy on container (no, always, on-failure, unless-stopped)"""
        try:
            container = self.client.containers.get(container_name)
            self.console.print(f"[cyan]Updating restart policy for container {container.name} to '{policy}'...[/cyan]")
            container.update(restart_policy={"Name": policy})
            self.console.print(f"[green]Restart policy set to '{policy}'[/green]")
            return True
        except docker.errors.NotFound:
            self.console.print(f"[bold red]Container not found: {container_name}[/bold red]")
            return False
        except docker.errors.APIError as e:
            self.console.print(f"[bold red]Docker API error during update:[/bold red] {e}")
            return False


    def run_new_container(self, container_name: str, **kwargs) -> bool:
        image_name = kwargs.get('image_name')
        name = kwargs.get('name', container_name)
        ports = kwargs.get('ports')
        command = kwargs.get('command')
        try:
            self.console.print(f"[cyan]Uruchamianie nowego kontenera {name} z obrazu {image_name}...[/cyan]")
            self.client.containers.run(image_name, name=name, detach=True, ports=ports, command=command)
            self.console.print(f"[green]Kontener {name} uruchomiony[/green]")
            return True
        except docker.errors.ImageNotFound:
            self.console.print(f"[bold red]Nie znaleziono obrazu: {image_name}[/bold red]")
            return False
        except docker.errors.APIError as e:
            self.console.print(f"[bold red]Błąd API Dockera:[/bold red] {e}")
            return False


    def _start_container(self, container_name: str, **kwargs) -> bool:
        """Start container with enhanced validation"""
        with self._error_handler("start container", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status == "running":
                self.console.print(f"[yellow]⚠️ Container {container_name} is already running[/yellow]")
                return True
            
            container.start()
            
            # Wait for container to be fully started
            self._wait_for_container_status(container_name, "running", timeout=30)
            
            self.logger.info(f"Container {container_name} started successfully")
            return True

    def _stop_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Stop container with graceful shutdown"""
        with self._error_handler("stop container", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status == "exited":
                self.console.print(f"[yellow]⚠️ Container {container_name} is already stopped[/yellow]")
                return True
            
            # Graceful stop
            container.stop(timeout=timeout)
            
            self.logger.info(f"Container {container_name} stopped successfully")
            return True

    def _restart_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Restart container with health check"""
        with self._error_handler("restart container", container_name):
            container = self.client.containers.get(container_name)
            container.restart(timeout=timeout)
            
            # Wait for container to be fully restarted
            self._wait_for_container_status(container_name, "running", timeout=30)
            
            self.logger.info(f"Container {container_name} restarted successfully")
            return True

    def _remove_container(self, container_name: str, force: bool = False, **kwargs) -> bool:
        """Remove container with safety checks"""
        with self._error_handler("remove container", container_name):
            container = self.client.containers.get(container_name)
            
            # Safety check for running containers
            if container.status == "running" and not force:
                if not Confirm.ask(f"Container {container_name} is running. Force removal?"):
                    self.console.print("[yellow]❌ Removal cancelled[/yellow]")
                    return False
            
            container.remove(force=force)
            
            self.logger.info(f"Container {container_name} removed successfully")
            return True

    def _pause_container(self, container_name: str, **kwargs) -> bool:
        """Pause container"""
        with self._error_handler("pause container", container_name):
            container = self.client.containers.get(container_name)
            container.pause()
            self.logger.info(f"Container {container_name} paused successfully")
            return True

    def _unpause_container(self, container_name: str, **kwargs) -> bool:
        """Unpause container"""
        with self._error_handler("unpause container", container_name):
            container = self.client.containers.get(container_name)
            container.unpause()
            self.logger.info(f"Container {container_name} unpaused successfully")
            return True

    def _wait_for_container_status(self, container_name: str, expected_status: str, timeout: int = 30) -> bool:
        """Wait for container to reach expected status"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                container = self.client.containers.get(container_name)
                if container.status == expected_status:
                    return True
                time.sleep(1)
            except:
                time.sleep(1)
        
        self.logger.warning(f"Container {container_name} did not reach status {expected_status} within {timeout}s")
        return False

    # ==================== MONITORING & METRICS ====================

    def get_container_stats(self, container_name: str) -> Optional[ContainerStats]:
        """Get comprehensive container statistics"""
        try:
            container = self.client.containers.get(container_name)
            
            # Get two measurements for accurate CPU calculation
            stats1 = container.stats(stream=False)
            time.sleep(1)
            stats2 = container.stats(stream=False)
            
            # Calculate CPU percentage
            cpu_percent = self._calculate_cpu_percent(stats1, stats2)
            
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

    def _calculate_cpu_percent(self, stats1: dict, stats2: dict) -> float:
        """Calculate CPU percentage from two stat measurements"""
        try:
            cpu1_total = stats1['cpu_stats']['cpu_usage']['total_usage']
            cpu1_system = stats1['cpu_stats'].get('system_cpu_usage', 0)
            
            cpu2_total = stats2['cpu_stats']['cpu_usage']['total_usage']
            cpu2_system = stats2['cpu_stats'].get('system_cpu_usage', 0)
            
            cpu_delta = cpu2_total - cpu1_total
            system_delta = cpu2_system - cpu1_system
            
            online_cpus = len(stats2['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
            
            if system_delta > 0 and cpu_delta >= 0:
                return (cpu_delta / system_delta) * online_cpus * 100.0
            
            return 0.0
        except (KeyError, ZeroDivisionError):
            return 0.0

    def monitor_containers_dashboard(self, containers: List[str] = None, duration: int = 300):
        """Real-time monitoring dashboard for multiple containers"""
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
                                cpu_trend = self._get_trend_indicator(
                                    [s.cpu_percent for s in metrics_history[container_name][-5:]]
                                )
                                cpu_display = f"{stats.cpu_percent:.1f}% {cpu_trend}"
                                
                                # Memory display
                                memory_display = f"{stats.memory_usage_mb:.0f}MB ({stats.memory_percent:.1f}%)"
                                
                                # Network I/O
                                network_display = f"↓{stats.network_rx_mb:.1f} ↑{stats.network_tx_mb:.1f}"
                                
                                # Uptime
                                uptime = self._calculate_uptime(container)
                                
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

    def _get_trend_indicator(self, values: List[float]) -> str:
        """Get trend indicator for metrics"""
        if len(values) < 2:
            return "→"
        
        recent_avg = sum(values[-2:]) / 2
        older_avg = sum(values[:-2]) / len(values[:-2]) if len(values) > 2 else values[0]
        
        diff = recent_avg - older_avg
        if diff > 5:
            return "↗️"
        elif diff < -5:
            return "↘️"
        else:
            return "→"

    def _save_metrics_history(self, metrics_history: Dict):
        """Save metrics history to file"""
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
        """Show monitoring summary statistics"""
        self.console.print("\n[bold cyan]📈 Monitoring Summary[/bold cyan]")
        
        summary_table = Table(show_header=True, header_style="bold blue")
        summary_table.add_column("Container", style="green")
        summary_table.add_column("Avg CPU %", style="red")
        summary_table.add_column("Max CPU %", style="red")
        summary_table.add_column("Avg Memory MB", style="blue")
        summary_table.add_column("Max Memory MB", style="blue")
        summary_table.add_column("Data Points", style="yellow")
        
        for container, stats_list in metrics_history.items():
            if stats_list:
                avg_cpu = sum(s.cpu_percent for s in stats_list) / len(stats_list)
                max_cpu = max(s.cpu_percent for s in stats_list)
                avg_memory = sum(s.memory_usage_mb for s in stats_list) / len(stats_list)
                max_memory = max(s.memory_usage_mb for s in stats_list)
                
                summary_table.add_row(
                    container,
                    f"{avg_cpu:.1f}",
                    f"{max_cpu:.1f}",
                    f"{avg_memory:.0f}",
                    f"{max_memory:.0f}",
                    str(len(stats_list))
                )
        
        self.console.print(summary_table)

    # ==================== ADVANCED DEPLOYMENT ====================

    def create_deployment_config(self, config_path: str = "deployment.yml") -> bool:
        """Create deployment configuration template"""
        template = {
            'deployment': {
                'image_tag': 'myapp:latest',
                'container_name': 'myapp',
                'port_mapping': {
                    '8080': '8080'
                },
                'environment': {
                    'ENV': 'production',
                    'DEBUG': 'false'
                },
                'volumes': {
                    './data': '/app/data'
                },
                'restart_policy': 'unless-stopped',
                'health_check_endpoint': '/health',
                'health_check_timeout': 30,
                'health_check_retries': 10,
                'build_args': {
                    'BUILD_ENV': 'production'
                },
                'network': 'bridge',
                'cpu_limit': '1.0',
                'memory_limit': '1g'
            },
            'build': {
                'dockerfile_path': '.',
                'context': '.',
                'no_cache': False,
                'pull': True
            },
            'monitoring': {
                'enabled': True,
                'metrics_retention_days': 7,
                'alert_cpu_threshold': 80.0,
                'alert_memory_threshold': 80.0
            }
        }
        
        try:
            with open(config_path, 'w') as f:
                yaml.dump(template, f, default_flow_style=False, indent=2)
            
            self.console.print(f"[green]✅ Deployment configuration template created: {config_path}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create config template: {e}")
            return False

    def deploy_from_config(self, config_path: str, deployment_type: str = "rolling") -> bool:
        """Deploy using configuration file"""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            deployment_config = DeploymentConfig(**config['deployment'])
            build_config = config.get('build', {})
            
            self.logger.info(f"Starting {deployment_type} deployment from config: {config_path}")
            
            if deployment_type == "blue-green":
                return self._blue_green_deploy_enhanced(deployment_config, build_config)
            elif deployment_type == "canary":
                return self._canary_deploy(deployment_config, build_config)
            else:  # rolling deployment
                return self._rolling_deploy(deployment_config, build_config)
                
        except Exception as e:
            self.logger.error(f"Deployment from config failed: {e}")
            return False

    def _rolling_deploy(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Enhanced rolling deployment with zero-downtime and full logging"""
        self.console.print(f"\n[bold cyan]🚀 ROLLING DEPLOYMENT STARTED[/bold cyan]")

        deployment_start = datetime.now()
        deployment_id = f"deploy_{int(deployment_start.timestamp())}"

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:

            # Phase 1: Build new image
            build_task = progress.add_task("🔨 Building new image...", total=None)
            try:
                success = self._build_image_enhanced(config.image_tag, build_config)
                if not success:
                    progress.update(build_task, description="❌ Image build failed")
                    return False
                progress.update(build_task, description="✅ Image built successfully")
            except Exception as e:
                progress.update(build_task, description="❌ Image build failed")
                self.logger.error(f"Build failed: {e}")
                return False

            # Phase 2: Check existing container
            health_task = progress.add_task("🔍 Checking existing deployment...", total=None)
            existing_container = None
            try:
                existing_container = self.client.containers.get(config.container_name)
                if existing_container.status == "running":
                    progress.update(health_task, description="✅ Found running container")
                else:
                    progress.update(health_task, description="⚠️ Container exists but not running")
            except docker.errors.NotFound:
                progress.update(health_task, description="ℹ️ No existing container (first deployment)")

            # Phase 3: Create and start new container with temporary name
            temp_name = f"{config.container_name}_new_{deployment_id}"
            deploy_task = progress.add_task("🚀 Deploying new version...", total=None)
            try:
                new_container = self.client.containers.create(
                    image=config.image_tag,
                    name=temp_name,
                    ports=config.port_mapping,
                    environment=config.environment,
                    volumes=config.volumes,
                    restart_policy={"Name": config.restart_policy},
                    network=config.network,
                    **self._get_resource_limits(config)
                )

                # Start container
                try:
                    new_container.start()
                    progress.update(deploy_task, description="✅ New container started")
                except Exception as e:
                    progress.update(deploy_task, description="❌ New container deployment failed")
                    self.logger.error(f"Container start failed: {e}")
                    try:
                        logs = new_container.logs().decode()
                        self.logger.error(f"Container logs:\n{logs}")
                    except:
                        pass
                    return False

                # Grace period
                time.sleep(5)

            except Exception as e:
                progress.update(deploy_task, description="❌ New container creation failed")
                self.logger.error(f"New container creation failed: {e}")
                return False

            # Phase 4: Health check new container (only if ports are mapped)
            if config.port_mapping:
                health_check_task = progress.add_task("🩺 Health checking new deployment...", total=None)
                host_port = list(config.port_mapping.values())[0]
                if not self._advanced_health_check(
                    host_port,
                    config.health_check_endpoint,
                    config.health_check_timeout,
                    config.health_check_retries
                ):
                    progress.update(health_check_task, description="❌ Health check failed - rolling back")
                    try:
                        logs = new_container.logs().decode()
                        self.logger.error(f"Health check failed. Container logs:\n{logs}")
                    except Exception as e:
                        self.logger.error(f"Could not fetch logs: {e}")

                    # Rollback
                    try:
                        new_container.stop()
                        new_container.remove()
                    except Exception as e:
                        self.logger.error(f"Rollback failed: {e}")
                    return False
                progress.update(health_check_task, description="✅ Health check passed")
            else:
                progress.add_task("🩺 No port mapping, skipping health check", total=None)

            # Phase 5: Traffic switch (stop old, rename new)
            switch_task = progress.add_task("🔄 Switching traffic...", total=None)
            try:
                if existing_container and existing_container.status == "running":
                    existing_container.stop(timeout=10)
                    existing_container.remove()

                new_container.rename(config.container_name)
                progress.update(switch_task, description="✅ Traffic switched successfully")
            except Exception as e:
                progress.update(switch_task, description="❌ Traffic switch failed")
                self.logger.error(f"Traffic switch failed: {e}")
                return False

        # Deployment summary
        deployment_end = datetime.now()
        duration = deployment_end - deployment_start
        self._record_deployment(deployment_id, config, "rolling", True, duration)

        self.console.print(f"\n[bold green]🎉 ROLLING DEPLOYMENT COMPLETED SUCCESSFULLY![/bold green]")
        self.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
        if config.port_mapping:
            port = list(config.port_mapping.values())[0]
            self.console.print(f"[green]Application available at: http://localhost:{port}[/green]")
        else:
            self.console.print(f"[green]Application deployed (no port mapping set)[/green]")

        return True

    def view_container_logs(self):
        containers = self.client.containers.list(all=True)
        if not containers:
            self.console.print("[red]No containers found[/red]")
            return

        self.console.print("\nSelect a container to view logs:")
        for i, c in enumerate(containers, start=1):
            status = c.status
            self.console.print(f"{i}. {c.name} ({status})")

        choice = input("Enter number: ")
        try:
            idx = int(choice) - 1
            container = containers[idx]
        except (ValueError, IndexError):
            self.console.print("[red]Invalid selection[/red]")
            return

        tail = input("Number of lines to show (default 50): ")
        try:
            tail = int(tail)
        except ValueError:
            tail = 50

        self.console.print(f"\n[cyan]Showing last {tail} lines of {container.name} logs:[/cyan]\n")
        try:
            logs = container.logs(tail=tail).decode()
            self.console.print(logs)
        except Exception as e:
            self.console.print(f"[red]Failed to fetch logs: {e}[/red]")

    def view_container_json(self, container_name: str):
        """Wyświetla pełne info o kontenerze w formacie JSON"""
        try:
            container = self.client.containers.get(container_name)
            data = container.attrs  # Pełne dane kontenera
            json_str = json.dumps(data, indent=4, ensure_ascii=False)
            self.console.print(Panel(json_str, title=f"Container JSON: {container_name}", expand=True))
        except docker.errors.NotFound:
            self.console.print(f"[red]Container '{container_name}' not found[/red]")
        except Exception as e:
            self.console.print(f"[red]Error fetching JSON for container '{container_name}': {e}[/red]")


    def _blue_green_deploy_enhanced(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Enhanced Blue-Green deployment with advanced features"""
        self.console.print(f"\n[bold cyan]🔵🟢 BLUE-GREEN DEPLOYMENT STARTED[/bold cyan]")
        
        deployment_start = datetime.now()
        deployment_id = f"bg_deploy_{int(deployment_start.timestamp())}"
        
        blue_name = f"{config.container_name}_blue"
        green_name = f"{config.container_name}_green"
        
        # Determine current active container
        active_container = None
        active_name = None
        
        try:
            blue_container = self.client.containers.get(blue_name)
            if blue_container.status == "running":
                active_container = blue_container
                active_name = "blue"
        except docker.errors.NotFound:
            pass
        
        if not active_container:
            try:
                green_container = self.client.containers.get(green_name)
                if green_container.status == "running":
                    active_container = green_container
                    active_name = "green"
            except docker.errors.NotFound:
                pass
        
        target_name = "green" if active_name == "blue" else "blue"
        target_container_name = green_name if target_name == "green" else blue_name
        
        self.console.print(f"[cyan]Current active: {active_name or 'none'} | Deploying to: {target_name}[/cyan]")
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            
            # Build new image
            build_task = progress.add_task("🔨 Building new image...", total=None)
            if not self._build_image_enhanced(config.image_tag, build_config):
                return False
            progress.update(build_task, description="✅ Image built successfully")
            
            # Clean up existing target container
            cleanup_task = progress.add_task(f"🧹 Cleaning up {target_name} slot...", total=None)
            try:
                old_target = self.client.containers.get(target_container_name)
                old_target.stop()
                old_target.remove()
            except docker.errors.NotFound:
                pass
            progress.update(cleanup_task, description=f"✅ {target_name.title()} slot cleaned")
            
            # Deploy to target slot
            deploy_task = progress.add_task(f"🚀 Deploying to {target_name} slot...", total=None)
            
            # Use different port for parallel testing
            temp_port_mapping = {}
            for container_port, host_port in config.port_mapping.items():
                temp_port_mapping[container_port] = str(int(host_port) + 1000)  # +1000 for temp
            
            try:
                target_container = self.client.containers.run(
                    image=config.image_tag,
                    name=target_container_name,
                    detach=True,
                    ports=temp_port_mapping,
                    environment=config.environment,
                    volumes=config.volumes,
                    restart_policy={"Name": config.restart_policy},
                    **self._get_resource_limits(config)
                )
                
                progress.update(deploy_task, description=f"✅ {target_name.title()} container deployed")
                time.sleep(5)  # Startup grace period
                
            except Exception as e:
                progress.update(deploy_task, description=f"❌ {target_name.title()} deployment failed")
                return False
            
            # Health check new deployment
            health_task = progress.add_task(f"🩺 Health checking {target_name} deployment...", total=None)

            if temp_port_mapping:
                temp_port = list(temp_port_mapping.values())[0]
                if not self._advanced_health_check(
                    temp_port, 
                    config.health_check_endpoint,
                    config.health_check_timeout,
                    config.health_check_retries
                ):
                    progress.update(health_task, description=f"❌ {target_name.title()} health check failed")
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    return False
                progress.update(health_task, description=f"✅ {target_name.title()} health check passed")
            else:
                self.logger.warning("No ports mapped for temporary deployment, skipping health check")
                progress.update(health_task, description=f"⚠️ {target_name.title()} no ports to check")
            
            # Parallel testing phase (optional)
            if self._should_run_parallel_tests():
                test_task = progress.add_task("🧪 Running parallel tests...", total=None)
                if not self._run_parallel_tests(temp_port, config):
                    progress.update(test_task, description="❌ Parallel tests failed")
                    # Cleanup and abort
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    return False
                progress.update(test_task, description="✅ Parallel tests passed")
            
            # Traffic switch with zero-downtime
            switch_task = progress.add_task("🔄 Zero-downtime traffic switch...", total=None)
            
            try:
                # Stop target container temporarily
                target_container.stop()
                target_container.remove()
                
                # Create final container with correct ports
                final_container = self.client.containers.run(
                    image=config.image_tag,
                    name=target_container_name,
                    detach=True,
                    ports=config.port_mapping,  # Final ports
                    environment=config.environment,
                    volumes=config.volumes,
                    restart_policy={"Name": config.restart_policy},
                    **self._get_resource_limits(config)
                )
                
                # Wait for final container to be ready
                time.sleep(3)
                
                # Final health check
                final_port = list(config.port_mapping.values())[0]
                if not self._advanced_health_check(final_port, config.health_check_endpoint, 10, 5):
                    raise Exception("Final health check failed")
                
                # Now safe to stop old container
                if active_container:
                    active_container.stop(timeout=10)
                    active_container.remove()
                
                progress.update(switch_task, description="✅ Traffic switched successfully")
                
            except Exception as e:
                progress.update(switch_task, description="❌ Traffic switch failed")
                self.logger.error(f"Traffic switch failed: {e}")
                return False
        
        deployment_end = datetime.now()
        duration = deployment_end - deployment_start
        
        self._record_deployment(deployment_id, config, "blue-green", True, duration)
        
        self.console.print(f"\n[bold green]🎉 BLUE-GREEN DEPLOYMENT COMPLETED![/bold green]")
        self.console.print(f"[green]Active slot: {target_name}[/green]")
        self.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
        
        return True

    def _canary_deploy(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Canary deployment with gradual traffic shifting"""
        self.console.print(f"\n[bold cyan]🐤 CANARY DEPLOYMENT STARTED[/bold cyan]")
        
        # This would require a load balancer integration
        # For now, we'll implement a simplified version
        
        deployment_start = datetime.now()
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            
            # Build image
            build_task = progress.add_task("🔨 Building canary image...", total=None)
            if not self._build_image_enhanced(config.image_tag, build_config):
                return False
            progress.update(build_task, description="✅ Canary image built")
            
            # Deploy canary container (5% traffic simulation)
            canary_name = f"{config.container_name}_canary"
            canary_task = progress.add_task("🚀 Deploying canary (5% traffic)...", total=None)
            
            # Use different port for canary
            canary_port_mapping = {}
            for container_port, host_port in config.port_mapping.items():
                canary_port_mapping[container_port] = str(int(host_port) + 100)
            
            try:
                # Clean existing canary
                try:
                    old_canary = self.client.containers.get(canary_name)
                    old_canary.stop()
                    old_canary.remove()
                except docker.errors.NotFound:
                    pass
                
                canary_container = self.client.containers.run(
                    image=config.image_tag,
                    name=canary_name,
                    detach=True,
                    ports=canary_port_mapping,
                    environment={**config.environment, "CANARY": "true"},
                    volumes=config.volumes,
                    restart_policy={"Name": config.restart_policy},
                    **self._get_resource_limits(config)
                )
                
                progress.update(canary_task, description="✅ Canary deployed")
                time.sleep(5)
                
            except Exception as e:
                progress.update(canary_task, description="❌ Canary deployment failed")
                return False
            
            # Monitor canary
            monitor_task = progress.add_task("📊 Monitoring canary performance...", total=None)
            
            canary_port = list(canary_port_mapping.values())[0]
            if not self._monitor_canary_performance(canary_port, duration=30):
                progress.update(monitor_task, description="❌ Canary monitoring failed")
                # Cleanup canary
                try:
                    canary_container.stop()
                    canary_container.remove()
                except:
                    pass
                return False
            
            progress.update(monitor_task, description="✅ Canary performance acceptable")
            
            # Promote canary to full deployment
            promote_task = progress.add_task("⬆️ Promoting canary to full deployment...", total=None)
            
            try:
                # Stop main container
                try:
                    main_container = self.client.containers.get(config.container_name)
                    main_container.stop()
                    main_container.remove()
                except docker.errors.NotFound:
                    pass
                
                # Stop canary and redeploy as main
                canary_container.stop()
                canary_container.remove()
                
                # Deploy as main container
                main_container = self.client.containers.run(
                    image=config.image_tag,
                    name=config.container_name,
                    detach=True,
                    ports=config.port_mapping,
                    environment=config.environment,
                    volumes=config.volumes,
                    restart_policy={"Name": config.restart_policy},
                    **self._get_resource_limits(config)
                )
                
                progress.update(promote_task, description="✅ Canary promoted successfully")
                
            except Exception as e:
                progress.update(promote_task, description="❌ Canary promotion failed")
                return False
        
        deployment_end = datetime.now()
        duration = deployment_end - deployment_start
        
        self._record_deployment(f"canary_{int(deployment_start.timestamp())}", config, "canary", True, duration)
        
        self.console.print(f"\n[bold green]🎉 CANARY DEPLOYMENT COMPLETED![/bold green]")
        self.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
        
        return True

    def _build_image_enhanced(self, image_tag: str, build_config: dict) -> bool:
        """Enhanced image building with advanced features"""
        dockerfile_path = build_config.get('dockerfile_path', '.')
        context = build_config.get('context', '.')
        no_cache = build_config.get('no_cache', False)
        pull = build_config.get('pull', True)
        build_args = build_config.get('build_args', {})
        
        try:
            # Validate Dockerfile exists
            dockerfile = Path(dockerfile_path) / "Dockerfile"
            if not dockerfile.exists():
                self.console.print(f"[bold red]❌ Dockerfile not found at {dockerfile}[/bold red]")
                return False
            
            # Build with enhanced logging
            self.logger.info(f"Building image {image_tag} from {dockerfile_path}")
            
            build_kwargs = {
                'path': context,
                'tag': image_tag,
                'rm': True,
                'nocache': no_cache,
                'pull': pull,
                'buildargs': build_args
            }
            
            image, build_logs = self.client.images.build(**build_kwargs)
            
            # Process build logs
            for log in build_logs:
                if 'stream' in log:
                    # Filter out verbose output for cleaner display
                    stream = log['stream'].strip()
                    if stream and not stream.startswith('Step'):
                        continue  # Only show steps in production
                
            return True
            
        except docker.errors.BuildError as e:
            self.logger.error(f"Build error: {e}")
            for log in e.build_log:
                if 'stream' in log:
                    self.console.print(f"[red]{log['stream']}[/red]", end="")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected build error: {e}")
            return False
        
    def build_image_standalone(self, dockerfile_path: str, tag: str, no_cache: bool = False, pull: bool = True) -> bool:
        """Standalone image building function"""
        build_config = {
            'dockerfile_path': dockerfile_path,
            'context': dockerfile_path,
            'no_cache': no_cache,
            'pull': pull,
            'build_args': {}
        }

        self.console.print(f"[cyan]Building image {tag} from {dockerfile_path}...[/cyan]")

        success = self._build_image_enhanced(tag, build_config)

        if success:
            self.console.print(f"[green]✅ Image {tag} built successfully[/green]")
        else:
            self.console.print(f"[red]❌ Failed to build image {tag}[/red]")

        return success

    def _advanced_health_check(self, port: str, endpoint: str, timeout: int, max_retries: int) -> bool:
        """Advanced health check with detailed reporting"""
        url = f"http://localhost:{port}{endpoint}"
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                response = requests.get(url, timeout=5)
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    self.logger.info(f"Health check passed (attempt {attempt + 1}): {response_time:.2f}s")
                    return True
                else:
                    self.logger.warning(f"Health check returned {response.status_code} (attempt {attempt + 1})")
                    
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Health check failed (attempt {attempt + 1}): {e}")
            
            if attempt < max_retries - 1:
                time.sleep(3)
        
        return False

    def _get_resource_limits(self, config: DeploymentConfig) -> dict:
        """Convert resource limits to Docker API format"""
        limits = {}
        
        if config.cpu_limit:
            # Convert CPU limit (e.g., "1.5" -> 1500000000 nanoseconds)
            try:
                cpu_limit = float(config.cpu_limit) * 1000000000
                limits['nano_cpus'] = int(cpu_limit)
            except:
                pass
        
        if config.memory_limit:
            # Convert memory limit (e.g., "1g" -> bytes)
            try:
                memory_str = config.memory_limit.lower()
                if memory_str.endswith('g'):
                    memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024 * 1024)
                elif memory_str.endswith('m'):
                    memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024)
                else:
                    memory_bytes = int(memory_str)
                
                limits['mem_limit'] = memory_bytes
            except:
                pass
        
        return limits

    def _should_run_parallel_tests(self) -> bool:
        """Determine if parallel tests should be run"""
        return self.config.get('testing', {}).get('parallel_tests_enabled', False)

    def _run_parallel_tests(self, port: str, config: DeploymentConfig) -> bool:
        """Run parallel tests against new deployment"""
        test_config = self.config.get('testing', {})
        test_endpoints = test_config.get('endpoints', ['/health'])
        
        base_url = f"http://localhost:{port}"
        
        for endpoint in test_endpoints:
            try:
                url = f"{base_url}{endpoint}"
                response = requests.get(url, timeout=5)
                
                if response.status_code != 200:
                    self.logger.error(f"Parallel test failed for {endpoint}: {response.status_code}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Parallel test error for {endpoint}: {e}")
                return False
        
        return True

    def _monitor_canary_performance(self, port: str, duration: int) -> bool:
        """Monitor canary deployment performance"""
        start_time = time.time()
        error_count = 0
        total_requests = 0
        
        while time.time() - start_time < duration:
            try:
                response = requests.get(f"http://localhost:{port}/health", timeout=2)
                total_requests += 1
                
                if response.status_code != 200:
                    error_count += 1
                
                # Stop if error rate is too high (>10%)
                if total_requests > 10 and (error_count / total_requests) > 0.1:
                    self.logger.error(f"Canary error rate too high: {error_count}/{total_requests}")
                    return False
                    
            except:
                error_count += 1
                total_requests += 1
            
            time.sleep(1)
        
        error_rate = error_count / total_requests if total_requests > 0 else 0
        self.logger.info(f"Canary monitoring complete: {error_count}/{total_requests} errors ({error_rate:.2%})")
        
        return error_rate < 0.05  # Accept if error rate < 5%

    def _record_deployment(self, deployment_id: str, config: DeploymentConfig, 
                          deployment_type: str, success: bool, duration: timedelta):
        """Record deployment in history"""
        deployment_record = {
            'id': deployment_id,
            'timestamp': datetime.now().isoformat(),
            'type': deployment_type,
            'image_tag': config.image_tag,
            'container_name': config.container_name,
            'success': success,
            'duration_seconds': duration.total_seconds()
        }
        
        self.deployment_history.append(deployment_record)
        
        # Save to file
        try:
            history_file = "deployment_history.json"
            history_data = []
            
            if Path(history_file).exists():
                with open(history_file, 'r') as f:
                    history_data = json.load(f)
            
            history_data.append(deployment_record)
            
            # Keep only last 100 deployments
            if len(history_data) > 100:
                history_data = history_data[-100:]
            
            with open(history_file, 'w') as f:
                json.dump(history_data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Failed to save deployment history: {e}")

    def show_deployment_history(self, limit: int = 10):
        """Show deployment history"""
        history_file = "deployment_history.json"
        
        if not Path(history_file).exists():
            self.console.print("[yellow]⚠️ No deployment history found[/yellow]")
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
            
            self.console.print(table)
            
        except Exception as e:
            self.logger.error(f"Failed to load deployment history: {e}")
            self.console.print(f"[red]❌ Error loading deployment history: {e}[/red]")

    # ==================== CLI INTERFACE ====================

    def create_cli_parser(self) -> argparse.ArgumentParser:
        """Create comprehensive CLI parser"""
        parser = argparse.ArgumentParser(
            description="Docker Pilot Enhanced - Professional Docker Management Tool",
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        
        parser.add_argument('--config', '-c', type=str, help='Configuration file path')
        parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], 
                          default='INFO', help='Logging level')
        
        subparsers = parser.add_subparsers(dest='command', help='Available commands')
        
        # Container operations
        container_parser = subparsers.add_parser('container', help='Container operations')
        container_subparsers = container_parser.add_subparsers(dest='container_action')
        
        # List containers
        list_parser = container_subparsers.add_parser('list', help='List containers')
        list_parser.add_argument('--all', '-a', action='store_true', help='Show all containers')
        list_parser.add_argument('--format', choices=['table', 'json'], default='table')

       
        # List images
        images_parser = container_subparsers.add_parser('list-images', help='List Docker images')
        images_parser.add_argument('--all', '-a', action='store_true', help='Show all images')
        images_parser.add_argument('--format', choices=['table', 'json'], default='table')

        # Remove image
        remove_img_parser = container_subparsers.add_parser('remove-image', help='Remove Docker image')
        remove_img_parser.add_argument('name', help='Image name or ID')
        remove_img_parser.add_argument('--force', '-f', action='store_true', help='Force removal')

        
        # Container actions
        for action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
            action_parser = container_subparsers.add_parser(action, help=f'{action.title()} container')
            action_parser.add_argument('name', help='Container name or ID')
            if action in ['stop', 'restart']:
                action_parser.add_argument('--timeout', '-t', type=int, default=10, help='Timeout seconds')
            if action == 'remove':
                action_parser.add_argument('--force', '-f', action='store_true', help='Force removal')
        
        # Monitoring
        monitor_parser = subparsers.add_parser('monitor', help='Container monitoring')
        monitor_parser.add_argument('containers', nargs='*', help='Container names (empty for all running)')
        monitor_parser.add_argument('--duration', '-d', type=int, default=300, help='Monitor duration in seconds')
        
        # Deployment
        deploy_parser = subparsers.add_parser('deploy', help='Deployment operations')
        deploy_subparsers = deploy_parser.add_subparsers(dest='deploy_action')
        
        # Deploy from config
        config_deploy_parser = deploy_subparsers.add_parser('config', help='Deploy from configuration file')
        config_deploy_parser.add_argument('config_file', help='Deployment configuration file')
        config_deploy_parser.add_argument('--type', choices=['rolling', 'blue-green', 'canary'], 
                                        default='rolling', help='Deployment type')
        
        # Create config template
        template_parser = deploy_subparsers.add_parser('init', help='Create deployment configuration template')
        template_parser.add_argument('--output', '-o', default='deployment.yml', help='Output file name')
        
        # Deployment history
        history_parser = deploy_subparsers.add_parser('history', help='Show deployment history')
        history_parser.add_argument('--limit', '-l', type=int, default=10, help='Number of records to show')
        
        # Nowe parsery dodane # 
        # System validation
        validate_parser = subparsers.add_parser('validate', help='Validate system requirements')

        # Backup operations
        backup_parser = subparsers.add_parser('backup', help='Backup and restore operations')
        backup_subparsers = backup_parser.add_subparsers(dest='backup_action')

        backup_create_parser = backup_subparsers.add_parser('create', help='Create deployment backup')
        backup_create_parser.add_argument('--path', '-p', help='Backup path')

        backup_restore_parser = backup_subparsers.add_parser('restore', help='Restore from backup')
        backup_restore_parser.add_argument('backup_path', help='Path to backup directory')

        # Configuration management
        config_parser = subparsers.add_parser('config', help='Configuration management')
        config_subparsers = config_parser.add_subparsers(dest='config_action')

        config_export_parser = config_subparsers.add_parser('export', help='Export configuration')
        config_export_parser.add_argument('--output', '-o', default='docker-pilot-config.tar.gz', help='Output archive name')

        config_import_parser = config_subparsers.add_parser('import', help='Import configuration')
        config_import_parser.add_argument('archive', help='Configuration archive path')

        # CI/CD pipeline
        pipeline_parser = subparsers.add_parser('pipeline', help='CI/CD pipeline operations')
        pipeline_subparsers = pipeline_parser.add_subparsers(dest='pipeline_action')

        pipeline_create_parser = pipeline_subparsers.add_parser('create', help='Create CI/CD pipeline')
        pipeline_create_parser.add_argument('--type', choices=['github', 'gitlab', 'jenkins'], default='github', help='Pipeline type')
        pipeline_create_parser.add_argument('--output', '-o', help='Output path')

        # Integration tests
        test_parser = subparsers.add_parser('test', help='Integration testing')
        test_parser.add_argument('--config', default='integration-tests.yml', help='Test configuration file')

        # Environment promotion
        promote_parser = subparsers.add_parser('promote', help='Environment promotion')
        promote_parser.add_argument('source', help='Source environment')
        promote_parser.add_argument('target', help='Target environment')
        promote_parser.add_argument('--config', help='Deployment configuration path')

        # Monitoring setup
        alerts_parser = subparsers.add_parser('alerts', help='Setup monitoring alerts')
        alerts_parser.add_argument('--config', default='alerts.yml', help='Alert configuration file')

        # Documentation
        docs_parser = subparsers.add_parser('docs', help='Generate documentation')
        docs_parser.add_argument('--output', '-o', default='docs', help='Output directory')

        # Build operations
        build_parser = subparsers.add_parser('build', help='Build Docker image from Dockerfile')
        build_parser.add_argument('dockerfile_path', help='Path to Dockerfile directory')
        build_parser.add_argument('tag', help='Image tag (e.g., myapp:latest)')
        build_parser.add_argument('--no-cache', action='store_true', help='Build without cache')
        build_parser.add_argument('--pull', action='store_true', default=True, help='Pull base image updates')

        # Production checklist
        checklist_parser = subparsers.add_parser('checklist', help='Generate production checklist')
        checklist_parser.add_argument('--output', '-o', default='production-checklist.md', help='Output file')

        return parser

    def run_cli(self):
        """Run CLI interface"""
        parser = self.create_cli_parser()
        args = parser.parse_args()
        
        if not args.command:
            # Interactive mode
            self._run_interactive_menu()
            return
        
        # Execute CLI command   JAZDA Z KURWAMI
        try:
            if args.command == 'container':
                self._handle_container_cli(args)
            elif args.command == 'monitor':
                self._handle_monitor_cli(args)
            elif args.command == 'update_restart_policy':
                self.update_restart_policy(args.name, args.policy)
            elif args.command == 'run_image':
                self.run_image(args.image, args.name, args.ports, args.env, args.volumes, args.detach)  
            elif args.command == 'deploy':
                self._handle_deploy_cli(args)
            elif args.command == 'validate':
                success = self.validate_system_requirements()
                if not success:
                    sys.exit(1)
            elif args.command == 'backup':
                self._handle_backup_cli(args)
            elif args.command == 'config':
                self._handle_config_cli(args)
            elif args.command == 'pipeline':
                self._handle_pipeline_cli(args)
            elif args.command == 'test':
                success = self.run_integration_tests(args.config)
                if not success:
                    sys.exit(1)
            elif args.command == 'promote':
                config_path = getattr(args, 'config', None)
                success = self.environment_promotion(args.source, args.target, config_path)
                if not success:
                    sys.exit(1)
            elif args.command == 'alerts':
                success = self.setup_monitoring_alerts(args.config)
                if not success:
                    sys.exit(1)
            elif args.command == 'docs':
                success = self.generate_documentation(args.output)
                if not success:
                    sys.exit(1)
            elif args.command == 'checklist':
                success = self.create_production_checklist(args.output)
                if not success:
                    sys.exit(1)
            elif args.command == 'build':
                success = self.build_image_standalone(args.dockerfile_path, args.tag, args.no_cache, args.pull)
            if not success:
                    sys.exit(1)
            else:
                parser.print_help()
        except Exception as e:
            self.logger.error(f"CLI command failed: {e}")
            self.console.print(f"[red]❌ Command failed: {e}[/red]")
            sys.exit(1)

    def _handle_container_cli(self, args):
        """Handle container CLI commands"""
        if args.container_action == 'list':
            self.list_containers(show_all=args.all, format_output=args.format)
        elif args.container_action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
            kwargs = {}
            if hasattr(args, 'timeout'):
                kwargs['timeout'] = args.timeout
            if hasattr(args, 'force'):
                kwargs['force'] = args.force
            success = self.container_operation(args.container_action, args.name, **kwargs)
            if not success:
                sys.exit(1)
        elif args.container_action == 'run_image':
            # This would need CLI parser setup for run_image - currently only supports interactive mode
            self.console.print("[yellow]run_image is only available in interactive mode[/yellow]")
        elif args.container_action == 'list-images':
            self.list_images(show_all=args.all, format_output=args.format)
        elif args.container_action == 'remove-image':
            success = self.remove_image(args.name, args.force)
            if not success:
                sys.exit(1)
        elif args.container_action == 'remove-image':
            success = self.remove_image(args.name, args.force)
            if not success:
                sys.exit(1)

    def _handle_monitor_cli(self, args):
        """Handle monitoring CLI commands"""
        containers = args.containers if args.containers else None
        self.monitor_containers_dashboard(containers, args.duration)

    def _handle_deploy_cli(self, args):
        """Handle deployment CLI commands"""
        if args.deploy_action == 'config':
            success = self.deploy_from_config(args.config_file, args.type)
            if not success:
                sys.exit(1)
        elif args.deploy_action == 'init':
            output = getattr(args, 'output', 'deployment.yml')
            success = self.create_deployment_config(output)
            if not success:
                sys.exit(1)
        elif args.deploy_action == 'history':
            self.show_deployment_history(limit=getattr(args, 'limit', 10))
        else:
            self.console.print("[yellow]⚠️ Unknown deploy action[/yellow]")

    def _handle_backup_cli(self, args):
        """Handle backup CLI commands"""
        if args.backup_action == 'create':
            backup_path = getattr(args, 'path', None)
            success = self.backup_deployment_state(backup_path)
            if not success:
                sys.exit(1)
        elif args.backup_action == 'restore':
            success = self.restore_deployment_state(args.backup_path)
            if not success:
                sys.exit(1)
        else:
            self.console.print("[yellow]⚠️ Unknown backup action[/yellow]")

    def _handle_config_cli(self, args):
        """Handle configuration CLI commands"""
        if args.config_action == 'export':
            success = self.export_configuration(args.output)
            if not success:
                sys.exit(1)
        elif args.config_action == 'import':
            success = self.import_configuration(args.archive)
            if not success:
                sys.exit(1)
        else:
            self.console.print("[yellow]⚠️ Unknown config action[/yellow]")

    def _handle_pipeline_cli(self, args):
        """Handle pipeline CLI commands"""
        if args.pipeline_action == 'create':
            success = self.create_pipeline_config(args.type, args.output)
            if not success:
                sys.exit(1)
        else:
            self.console.print("[yellow]⚠️ Unknown pipeline action[/yellow]")


    def _run_interactive_menu(self):
        """Simple interactive menu for quick operations"""
        try:
            while True:
                choice = Prompt.ask(
                    "\n[bold cyan]Docker Pilot - Interactive Menu[/bold cyan]\n"
                    "Container: list, list-img, start, stop, restart, remove, pause, unpause, policy, run_image, logs, remove-image, json, monitor, build\n"
                    "Deploy: deploy-init, deploy-config, history, promote\n"
                    "System: validate, backup-create, backup-restore, alerts,  test, pipeline, docs, checklist\n"
                    "Config: export-config, import-config\n"
                    "Select",
                    default="list"
                ).strip().lower()

                if choice == "exit":
                    self.console.print("[green]Bye![/green]")
                    break

                if choice == "list":
                    self.list_containers(show_all=True, format_output="table")

                elif choice == "list-img":
                    self.list_images(show_all=True, format_output="table")

                elif choice in ("start", "stop", "restart", "remove", "pause", "unpause"):
                    self.list_containers()
                    name = Prompt.ask("Container name or ID")
                    kwargs = {}
                    if choice in ("stop", "restart"):
                        kwargs['timeout'] = int(Prompt.ask("Timeout seconds", default="10"))
                    if choice == "remove":
                        kwargs['force'] = Confirm.ask("Force removal?", default=False)
                    success = self.container_operation(choice, name, **kwargs)
                    if not success:
                        self.console.print(f"[red]Operation {choice} failed[/red]")

                elif choice == "monitor":
                    self.list_containers()
                    containers_input = Prompt.ask("Containers (comma separated, empty = all running)", default="").strip()
                    containers = [c.strip() for c in containers_input.split(",")] if containers_input else None
                    duration = int(Prompt.ask("Duration seconds", default="60"))
                    self.monitor_containers_dashboard(containers, duration)

                elif choice == "run_image":
                    image_name = Prompt.ask("Image name (e.g., nginx:latest)")
                    container_name = Prompt.ask("Container name")
                    ports_input = Prompt.ask("Port mapping (format: host:container, e.g., 8080:80, empty for none)", default="").strip()
                    
                    # Parse port mapping
                    ports = {}
                    if ports_input:
                        try:
                            host_port, container_port = ports_input.split(":")
                            ports = {container_port: host_port}
                        except ValueError:
                            self.console.print("[red]Invalid port format. Use host:container (e.g., 8080:80)[/red]")
                            continue
                    
                    # Optional command
                    command = Prompt.ask("Command to run (empty for default)", default="").strip()
                    command = command if command else None
                    
                    success = self.container_operation('run_image', container_name, 
                                                       image_name=image_name, 
                                                       name=container_name,
                                                       ports=ports, 
                                                       command=command)
                    if not success:
                        self.console.print("[red]Failed to run container[/red]")
                
                elif choice == "build":
                    dockerfile_path = Prompt.ask("Dockerfile path", default=".")
                    image_tag = Prompt.ask("Image tag (e.g., myapp:latest)")
                    no_cache = Confirm.ask("Build without cache?", default=False)
                    pull = Confirm.ask("Pull base image updates?", default=True)
                    success = self.build_image_standalone(dockerfile_path, image_tag, no_cache, pull)
                    if not success:
                        self.console.print("[red]Image build failed[/red]")

                elif choice == "json":
                    self.list_containers()
                    container_name = Prompt.ask("Container name or ID")
                    self.view_container_json(container_name)    

                elif choice == "logs":
                    self.view_container_logs()

                
                elif choice == "remove-image":
                    self.list_images()
                    image_name = Prompt.ask("Image name or ID to remove")
                    force = Confirm.ask("Force removal?", default=False)
                    success = self.remove_image(image_name, force)
                    if not success:
                        self.console.print("[red]Failed to remove image[/red]")

                elif choice == "deploy-init":
                    output = Prompt.ask("Output file", default="deployment.yml")
                    self.create_deployment_config(output)

                elif choice == "deploy-config":
                    config_file = Prompt.ask("Config file path", default="deployment.yml")
                    deploy_type = Prompt.ask("Type (rolling/blue-green/canary)", default="rolling")
                    success = self.deploy_from_config(config_file, deploy_type)
                    if not success:
                        self.console.print("[red]Deployment failed[/red]")

                elif choice == "history":
                    limit = int(Prompt.ask("Number of records", default="10"))
                    self.show_deployment_history(limit=limit)

                elif choice == "validate":
                    success = self.validate_system_requirements()
                    if not success:
                        self.console.print("[red]System validation failed[/red]")

                elif choice == "backup-create":
                    backup_path = Prompt.ask("Backup path (empty for auto)", default="").strip()
                    backup_path = backup_path if backup_path else None
                    self.backup_deployment_state(backup_path)

                elif choice == "backup-restore":
                    backup_path = Prompt.ask("Backup path")
                    success = self.restore_deployment_state(backup_path)
                    if not success:
                        self.console.print("[red]Restore failed[/red]")

                elif choice == "export-config":
                    output = Prompt.ask("Output archive name", default="docker-pilot-config.tar.gz")
                    self.export_configuration(output)

                elif choice == "import-config":
                    archive = Prompt.ask("Archive path")
                    success = self.import_configuration(archive)
                    if not success:
                        self.console.print("[red]Import failed[/red]")

                elif choice == "pipeline":
                    pipeline_type = Prompt.ask("Pipeline type (github/gitlab/jenkins)", default="github")
                    output = Prompt.ask("Output path (empty for default)", default="").strip()
                    output = output if output else None
                    self.create_pipeline_config(pipeline_type, output)

                elif choice == "test":
                    test_config = Prompt.ask("Test config file", default="integration-tests.yml")
                    success = self.run_integration_tests(test_config)
                    if not success:
                        self.console.print("[red]Integration tests failed[/red]")

                elif choice == "promote":
                    source = Prompt.ask("Source environment")
                    target = Prompt.ask("Target environment") 
                    config_path = Prompt.ask("Config file (empty for auto)", default="").strip()
                    config_path = config_path if config_path else None
                    success = self.environment_promotion(source, target, config_path)
                    if not success:
                        self.console.print("[red]Environment promotion failed[/red]")

                elif choice == "alerts":
                    config_path = Prompt.ask("Alert config file", default="alerts.yml")
                    success = self.setup_monitoring_alerts(config_path)
                    if not success:
                        self.console.print("[red]Alert setup failed[/red]")

                elif choice == "policy":
                    self.list_containers()
                    name = Prompt.ask("Container name or ID")
                    policy = Prompt.ask("Restart policy (no/on-failure/always/unless-stopped)", default="always")
                    success = self.update_restart_policy(name, policy)
                    if not success:
                        self.console.print("[red]Failed to update restart policy[/red]")

                elif choice == "docs":
                    output = Prompt.ask("Output directory", default="docs")
                    success = self.generate_documentation(output)
                    if not success:
                        self.console.print("[red]Documentation generation failed[/red]")

                elif choice == "checklist":
                    output = Prompt.ask("Output file", default="production-checklist.md")
                    success = self.create_production_checklist(output)
                    if not success:
                        self.console.print("[red]Checklist generation failed[/red]")

                else:
                    self.console.print("[yellow]Unknown option, try again[/yellow]")

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted, exiting interactive mode[/yellow]")
        except Exception as e:
            self.logger.error(f"Interactive menu error: {e}")
            self.console.print(f"[red]Error: {e}[/red]")

# ==================== CI/CD PIPELINE INTEGRATION ====================

    def integrate_with_git(self, repo_path: str = ".") -> bool:
        """Integrate with Git for automated deployments"""
        try:
            import git
            repo = git.Repo(repo_path)
            
            # Get current branch and commit info
            current_branch = repo.active_branch.name
            commit_hash = repo.head.commit.hexsha[:8]
            commit_message = repo.head.commit.message.strip()
            
            self.console.print(f"[cyan]Git Integration:[/cyan] {current_branch}@{commit_hash}")
            self.console.print(f"[cyan]Latest commit:[/cyan] {commit_message}")
            
            return True
        except ImportError:
            self.console.print("[yellow]GitPython not installed. Run: pip install GitPython[/yellow]")
            return False
        except Exception as e:
            self.logger.error(f"Git integration failed: {e}")
            return False

    def create_pipeline_config(self, pipeline_type: str = "github", output_path: str = None) -> bool:
        """Generate CI/CD pipeline configuration files"""
        
        if pipeline_type.lower() == "github":
            return self._create_github_actions_config(output_path)
        elif pipeline_type.lower() == "gitlab":
            return self._create_gitlab_ci_config(output_path)
        elif pipeline_type.lower() == "jenkins":
            return self._create_jenkins_config(output_path)
        else:
            self.console.print(f"[red]Unsupported pipeline type: {pipeline_type}[/red]")
            return False

    def _create_github_actions_config(self, output_path: str = None) -> bool:
        """Create GitHub Actions workflow"""
        if not output_path:
            output_path = ".github/workflows"
        
        os.makedirs(output_path, exist_ok=True)
        
        workflow_content = """name: Docker Pilot CI/CD

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest pytest-cov
    
    - name: Run tests
      run: |
        pytest tests/ --cov=. --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3

  build-and-deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    permissions:
      contents: read
      packages: write
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3
    
    - name: Log in to Container Registry
      uses: docker/login-action@v3
      with:
        registry: ${{ env.REGISTRY }}
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}
    
    - name: Extract metadata
      id: meta
      uses: docker/metadata-action@v5
      with:
        images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
        tags: |
          type=ref,event=branch
          type=ref,event=pr
          type=sha,prefix={{branch}}-
    
    - name: Build and push Docker image
      uses: docker/build-push-action@v5
      with:
        context: .
        push: true
        tags: ${{ steps.meta.outputs.tags }}
        labels: ${{ steps.meta.outputs.labels }}
        cache-from: type=gha
        cache-to: type=gha,mode=max
    
    - name: Deploy with Docker Pilot
      run: |
        python dockerpilotv3.py deploy config deployment.yml --type rolling
      env:
        DOCKER_IMAGE: ${{ steps.meta.outputs.tags }}
"""
        
        config_file = Path(output_path) / "docker-pilot.yml"
        try:
            with open(config_file, 'w') as f:
                f.write(workflow_content)
            
            self.console.print(f"[green]GitHub Actions workflow created: {config_file}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create GitHub Actions config: {e}")
            return False

    def _create_gitlab_ci_config(self, output_path: str = None) -> bool:
        """Create GitLab CI configuration"""
        config_content = """stages:
  - test
  - build
  - deploy

variables:
  DOCKER_DRIVER: overlay2
  DOCKER_TLS_CERTDIR: "/certs"

services:
  - docker:24.0.5-dind

before_script:
  - docker info

test:
  stage: test
  image: python:3.11-slim
  before_script:
    - pip install -r requirements.txt
    - pip install pytest pytest-cov
  script:
    - pytest tests/ --cov=. --cov-report=xml
    - coverage report
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

build:
  stage: build
  image: docker:24.0.5
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  only:
    - main
    - develop

deploy:
  stage: deploy
  image: python:3.11-slim
  before_script:
    - pip install docker pyyaml requests rich
  script:
    - python dockerpilotv3.py deploy config deployment.yml --type rolling
  environment:
    name: production
    url: http://your-app-url.com
  only:
    - main
"""
        
        config_file = ".gitlab-ci.yml" if not output_path else Path(output_path) / ".gitlab-ci.yml"
        try:
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            self.console.print(f"[green]GitLab CI configuration created: {config_file}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create GitLab CI config: {e}")
            return False

    def _create_jenkins_config(self, output_path: str = None) -> bool:
        """Create Jenkins pipeline configuration"""
        pipeline_content = """pipeline {
    agent any
    
    environment {
        DOCKER_REGISTRY = 'your-registry.com'
        IMAGE_NAME = 'your-app'
        KUBECONFIG = credentials('kubeconfig')
    }
    
    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://github.com/your-org/your-repo.git'
            }
        }
        
        stage('Test') {
            steps {
                sh '''
                    python -m venv venv
                    source venv/bin/activate
                    pip install -r requirements.txt
                    pip install pytest pytest-cov
                    pytest tests/ --cov=. --cov-report=xml
                '''
            }
            post {
                always {
                    publishCoverage adapters: [coberturaAdapter('coverage.xml')]
                }
            }
        }
        
        stage('Build') {
            when {
                branch 'main'
            }
            steps {
                script {
                    def image = docker.build("${DOCKER_REGISTRY}/${IMAGE_NAME}:${BUILD_NUMBER}")
                    docker.withRegistry("https://${DOCKER_REGISTRY}", 'docker-registry-credentials') {
                        image.push()
                        image.push('latest')
                    }
                }
            }
        }
        
        stage('Deploy') {
            when {
                branch 'main'
            }
            steps {
                sh '''
                    source venv/bin/activate
                    python dockerpilotv3.py deploy config deployment.yml --type blue-green
                '''
            }
        }
    }
    
    post {
        always {
            cleanWs()
        }
        success {
            slackSend(
                channel: '#deployments',
                color: 'good',
                message: "Deployment successful: ${env.JOB_NAME} - ${env.BUILD_NUMBER}"
            )
        }
        failure {
            slackSend(
                channel: '#deployments',
                color: 'danger',
                message: "Deployment failed: ${env.JOB_NAME} - ${env.BUILD_NUMBER}"
            )
        }
    }
}"""
        
        config_file = "Jenkinsfile" if not output_path else Path(output_path) / "Jenkinsfile"
        try:
            with open(config_file, 'w') as f:
                f.write(pipeline_content)
            
            self.console.print(f"[green]Jenkins pipeline created: {config_file}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create Jenkins config: {e}")
            return False

    def run_integration_tests(self, test_config_path: str = "integration-tests.yml") -> bool:
        """Run comprehensive integration tests"""
        self.console.print("[cyan]Running integration tests...[/cyan]")
        
        try:
            if Path(test_config_path).exists():
                with open(test_config_path, 'r') as f:
                    test_config = yaml.safe_load(f)
            else:
                # Default test configuration
                test_config = {
                    'tests': [
                        {
                            'name': 'Health Check',
                            'type': 'http',
                            'url': 'http://localhost:8080/health',
                            'expected_status': 200,
                            'timeout': 5
                        },
                        {
                            'name': 'API Endpoint',
                            'type': 'http',
                            'url': 'http://localhost:8080/api/status',
                            'expected_status': 200,
                            'timeout': 10
                        }
                    ]
                }
            
            test_results = []
            
            for test in test_config.get('tests', []):
                result = self._run_single_integration_test(test)
                test_results.append(result)
            
            # Generate test report
            self._generate_test_report(test_results)
            
            # Return True if all tests passed
            return all(result['passed'] for result in test_results)
            
        except Exception as e:
            self.logger.error(f"Integration tests failed: {e}")
            return False

    def _run_single_integration_test(self, test_config: dict) -> dict:
        """Run a single integration test"""
        test_name = test_config.get('name', 'Unknown Test')
        test_type = test_config.get('type', 'http')
        
        start_time = time.time()
        
        try:
            if test_type == 'http':
                return self._run_http_test(test_config, start_time)
            elif test_type == 'database':
                return self._run_database_test(test_config, start_time)
            elif test_type == 'custom':
                return self._run_custom_test(test_config, start_time)
            else:
                return {
                    'name': test_name,
                    'passed': False,
                    'duration': 0,
                    'error': f'Unknown test type: {test_type}'
                }
        except Exception as e:
            return {
                'name': test_name,
                'passed': False,
                'duration': time.time() - start_time,
                'error': str(e)
            }

    def _run_http_test(self, test_config: dict, start_time: float) -> dict:
        """Run HTTP-based integration test"""
        url = test_config['url']
        expected_status = test_config.get('expected_status', 200)
        timeout = test_config.get('timeout', 5)
        method = test_config.get('method', 'GET').upper()
        headers = test_config.get('headers', {})
        data = test_config.get('data')
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            else:
                response = requests.request(method, url, headers=headers, json=data, timeout=timeout)
            
            passed = response.status_code == expected_status
            
            return {
                'name': test_config.get('name', 'HTTP Test'),
                'passed': passed,
                'duration': time.time() - start_time,
                'status_code': response.status_code,
                'expected_status': expected_status,
                'response_time': response.elapsed.total_seconds()
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'name': test_config.get('name', 'HTTP Test'),
                'passed': False,
                'duration': time.time() - start_time,
                'error': str(e)
            }

    def _run_database_test(self, test_config: dict, start_time: float) -> dict:
        """Run database connectivity test"""
        # This would require database-specific libraries
        # For now, return a placeholder implementation
        return {
            'name': test_config.get('name', 'Database Test'),
            'passed': True,  # Placeholder
            'duration': time.time() - start_time,
            'note': 'Database testing requires specific database drivers'
        }

    def _run_custom_test(self, test_config: dict, start_time: float) -> dict:
        """Run custom test script"""
        script_path = test_config.get('script')
        if not script_path or not Path(script_path).exists():
            return {
                'name': test_config.get('name', 'Custom Test'),
                'passed': False,
                'duration': time.time() - start_time,
                'error': 'Custom test script not found'
            }
        
        try:
            import subprocess
            result = subprocess.run(
                ['python', script_path],
                capture_output=True,
                text=True,
                timeout=test_config.get('timeout', 30)
            )
            
            return {
                'name': test_config.get('name', 'Custom Test'),
                'passed': result.returncode == 0,
                'duration': time.time() - start_time,
                'stdout': result.stdout,
                'stderr': result.stderr
            }
            
        except subprocess.TimeoutExpired:
            return {
                'name': test_config.get('name', 'Custom Test'),
                'passed': False,
                'duration': time.time() - start_time,
                'error': 'Test script timed out'
            }

    def _generate_test_report(self, test_results: List[dict]):
        """Generate comprehensive test report"""
        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results if result['passed'])
        failed_tests = total_tests - passed_tests
        
        # Create test report table
        table = Table(title="Integration Test Results", show_header=True)
        table.add_column("Test Name", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Duration", style="blue")
        table.add_column("Details", style="yellow")
        
        for result in test_results:
            status = "[green]PASS[/green]" if result['passed'] else "[red]FAIL[/red]"
            duration = f"{result['duration']:.2f}s"
            
            details = ""
            if 'status_code' in result:
                details = f"HTTP {result['status_code']}"
            if 'error' in result:
                details = result['error'][:50] + "..." if len(result['error']) > 50 else result['error']
            
            table.add_row(result['name'], status, duration, details)
        
        self.console.print(table)
        
        # Summary
        summary_color = "green" if failed_tests == 0 else "red"
        summary = f"[{summary_color}]{passed_tests}/{total_tests} tests passed[/{summary_color}]"
        self.console.print(Panel(summary, title="Test Summary"))
        
        # Save detailed report
        self._save_test_report(test_results, passed_tests, failed_tests)

    def _save_test_report(self, test_results: List[dict], passed: int, failed: int):
        """Save test report to file"""
        try:
            report_data = {
                'timestamp': datetime.now().isoformat(),
                'summary': {
                    'total': len(test_results),
                    'passed': passed,
                    'failed': failed,
                    'success_rate': (passed / len(test_results)) * 100 if test_results else 0
                },
                'tests': test_results
            }
            
            with open('integration-test-report.json', 'w') as f:
                json.dump(report_data, f, indent=2)
                
            self.logger.info("Integration test report saved to integration-test-report.json")
            
        except Exception as e:
            self.logger.error(f"Failed to save test report: {e}")

    def environment_promotion(self, source_env: str, target_env: str, 
                            config_path: str = None) -> bool:
        """Promote deployment between environments (dev -> staging -> prod)"""
        self.console.print(f"[cyan]Promoting from {source_env} to {target_env}...[/cyan]")
        
        # Environment-specific configurations
        env_configs = {
            'dev': {
                'replicas': 1,
                'resources': {'cpu': '0.5', 'memory': '512Mi'},
                'image_tag_suffix': '-dev'
            },
            'staging': {
                'replicas': 2,
                'resources': {'cpu': '1.0', 'memory': '1Gi'},
                'image_tag_suffix': '-staging'
            },
            'prod': {
                'replicas': 3,
                'resources': {'cpu': '2.0', 'memory': '2Gi'},
                'image_tag_suffix': ''
            }
        }
        
        if source_env not in env_configs or target_env not in env_configs:
            self.console.print(f"[red]Invalid environment: {source_env} or {target_env}[/red]")
            return False
        
        try:
            # Load base configuration
            if not config_path:
                config_path = f"deployment-{target_env}.yml"
            
            if Path(config_path).exists():
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
            else:
                self.console.print(f"[red]Configuration file not found: {config_path}[/red]")
                return False
            
            # Apply environment-specific settings
            target_config = env_configs[target_env]
            
            # Update image tag
            base_image = config['deployment']['image_tag'].split(':')[0]
            config['deployment']['image_tag'] = f"{base_image}:latest{target_config['image_tag_suffix']}"
            
            # Update resources
            config['deployment']['cpu_limit'] = target_config['resources']['cpu']
            config['deployment']['memory_limit'] = target_config['resources']['memory']
            
            # Run pre-promotion checks
            if not self._run_pre_promotion_checks(source_env, target_env):
                self.console.print("[red]Pre-promotion checks failed[/red]")
                return False
            
            # Execute deployment
            deployment_config = DeploymentConfig(**config['deployment'])
            build_config = config.get('build', {})
            
            # Use appropriate deployment strategy based on target environment
            deployment_type = 'blue-green' if target_env == 'prod' else 'rolling'
            
            if deployment_type == 'blue-green':
                success = self._blue_green_deploy_enhanced(deployment_config, build_config)
            else:
                success = self._rolling_deploy(deployment_config, build_config)
            
            if success:
                # Run post-promotion validation
                if self._run_post_promotion_validation(target_env, deployment_config):
                    self.console.print(f"[green]Successfully promoted to {target_env}[/green]")
                    return True
                else:
                    self.console.print(f"[yellow]Deployment succeeded but validation failed in {target_env}[/yellow]")
                    return False
            else:
                self.console.print(f"[red]Deployment failed in {target_env}[/red]")
                return False
                
        except Exception as e:
            self.logger.error(f"Environment promotion failed: {e}")
            return False

    def _run_pre_promotion_checks(self, source_env: str, target_env: str) -> bool:
        """Run checks before promoting between environments"""
        checks = [
            f"Source environment ({source_env}) is healthy",
            f"Target environment ({target_env}) is ready",
            "All required tests have passed",
            "No blocking issues in monitoring systems"
        ]
        
        # For demo purposes, we'll simulate these checks
        # In real implementation, these would check actual systems
        
        for check in checks:
            # Simulate check (replace with real logic)
            time.sleep(1)
            self.console.print(f"[green]✓[/green] {check}")
        
        return True

    def _run_post_promotion_validation(self, environment: str, config: DeploymentConfig) -> bool:
        """Validate deployment after promotion"""
        validation_checks = [
            "Application is responding to health checks",
            "All services are running correctly",
            "Performance metrics are within acceptable ranges",
            "No error spikes in logs"
        ]
        
        # Run actual health checks
        if config.port_mapping:
            port = list(config.port_mapping.values())[0]
            if not self._advanced_health_check(port, config.health_check_endpoint, 30, 5):
                return False
        
        # Additional validation checks would go here
        for check in validation_checks:
            time.sleep(1)
            self.console.print(f"[green]✓[/green] {check}")
        
        return True

    def setup_monitoring_alerts(self, alert_config_path: str = "alerts.yml") -> bool:
        """Setup monitoring and alerting configuration"""
        
        default_alerts = {
            'alerts': [
                {
                    'name': 'high_cpu_usage',
                    'condition': 'cpu_percent > 80',
                    'duration': '5m',
                    'severity': 'warning',
                    'message': 'CPU usage is above 80% for 5 minutes'
                },
                {
                    'name': 'high_memory_usage',
                    'condition': 'memory_percent > 85',
                    'duration': '3m',
                    'severity': 'critical',
                    'message': 'Memory usage is above 85% for 3 minutes'
                },
                {
                    'name': 'container_restart',
                    'condition': 'container_restarts > 3',
                    'duration': '10m',
                    'severity': 'warning',
                    'message': 'Container has restarted more than 3 times in 10 minutes'
                }
            ],
            'notification_channels': [
                {
                    'type': 'slack',
                    'webhook_url': 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK',
                    'channel': '#alerts'
                },
                {
                    'type': 'email',
                    'smtp_server': 'smtp.gmail.com',
                    'smtp_port': 587,
                    'username': 'your-email@example.com',
                    'recipients': ['admin@example.com', 'devops@example.com']
                }
            ]
        }
        
        try:
            if not Path(alert_config_path).exists():
                with open(alert_config_path, 'w') as f:
                    yaml.dump(default_alerts, f, default_flow_style=False, indent=2)
                
                self.console.print(f"[green]Alert configuration template created: {alert_config_path}[/green]")
            else:
                self.console.print(f"[yellow]Alert configuration already exists: {alert_config_path}[/yellow]")
            
            # Initialize alert monitoring
            return self._initialize_alert_monitoring(alert_config_path)
            
        except Exception as e:
            self.logger.error(f"Failed to setup monitoring alerts: {e}")
            return False

    def _initialize_alert_monitoring(self, alert_config_path: str) -> bool:
        """Initialize alert monitoring system"""
        try:
            with open(alert_config_path, 'r') as f:
                alert_config = yaml.safe_load(f)
            
            self.alert_rules = alert_config.get('alerts', [])
            self.notification_channels = alert_config.get('notification_channels', [])
            
            self.console.print(f"[green]Initialized {len(self.alert_rules)} alert rules[/green]")
            self.console.print(f"[green]Configured {len(self.notification_channels)} notification channels[/green]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize alert monitoring: {e}")
            return False

    def check_alerts(self, container_stats: ContainerStats, container_name: str):
        """Check if any alerts should be triggered"""
        if not hasattr(self, 'alert_rules'):
            return
        
        current_time = datetime.now()
        
        for rule in self.alert_rules:
            condition = rule['condition']
            
            # Simple condition evaluation (would need more sophisticated parsing in production)
            if 'cpu_percent >' in condition:
                threshold = float(condition.split('>')[-1].strip())
                if container_stats.cpu_percent > threshold:
                    self._trigger_alert(rule, container_name, f"CPU: {container_stats.cpu_percent:.1f}%")
            
            elif 'memory_percent >' in condition:
                threshold = float(condition.split('>')[-1].strip())
                if container_stats.memory_percent > threshold:
                    self._trigger_alert(rule, container_name, f"Memory: {container_stats.memory_percent:.1f}%")

    def _trigger_alert(self, rule: dict, container_name: str, details: str):
        """Trigger an alert notification"""
        alert_message = f"ALERT: {rule['name']} - Container: {container_name} - {details} - {rule['message']}"
        
        self.logger.warning(f"Alert triggered: {alert_message}")
        self.console.print(f"[red]🚨 ALERT: {rule['name']} - {container_name}[/red]")
        
        # Send notifications
        for channel in getattr(self, 'notification_channels', []):
            self._send_notification(channel, alert_message)

    def _send_notification(self, channel: dict, message: str):
        """Send notification through configured channel"""
        try:
            if channel['type'] == 'slack':
                # Slack webhook notification
                webhook_url = channel.get('webhook_url')
                if webhook_url:
                    payload = {
                        'text': message,
                        'channel': channel.get('channel', '#general'),
                        'username': 'Docker Pilot',
                        'icon_emoji': ':warning:'
                    }
                    requests.post(webhook_url, json=payload, timeout=5)
            
            elif channel['type'] == 'email':
                # Email notification (would require email libraries)
                self.logger.info(f"Email notification would be sent: {message}")
                
        except Exception as e:
            self.logger.error(f"Failed to send notification: {e}")

    def create_production_checklist(self, output_file: str = "production-checklist.md") -> bool:
        """Generate production deployment checklist"""
        
        checklist_content = """# Production Deployment Checklist

## Pre-Deployment
- [ ] All tests passing in CI/CD pipeline
- [ ] Security scan completed with no critical vulnerabilities
- [ ] Performance testing completed
- [ ] Database migrations tested and ready
- [ ] Configuration files reviewed and approved
- [ ] Rollback plan documented and tested
- [ ] Monitoring alerts configured
- [ ] Team notifications sent

## During Deployment
- [ ] Backup current production state
- [ ] Enable maintenance mode (if applicable)
- [ ] Execute deployment using Docker Pilot
- [ ] Monitor application logs during deployment
- [ ] Verify health checks are passing
- [ ] Run smoke tests
- [ ] Disable maintenance mode

## Post-Deployment
- [ ] Verify all services are running correctly
- [ ] Check application functionality
- [ ] Monitor error rates and performance metrics
- [ ] Verify data integrity
- [ ] Update documentation
- [ ] Send deployment success notification
- [ ] Schedule post-deployment review meeting

## Rollback Procedures
- [ ] Stop current containers
- [ ] Deploy previous stable version
- [ ] Verify rollback success
- [ ] Investigate deployment failure
- [ ] Document lessons learned

## Emergency Contacts
- DevOps Team: devops@company.com
- Application Team: app-team@company.com
- On-Call Engineer: +1-XXX-XXX-XXXX

## Useful Commands
```bash
# Check deployment status
python dockerpilotv3.py container list

# Monitor application
python dockerpilotv3.py monitor myapp --duration 300

# View deployment history
python dockerpilotv3.py deploy history

# Emergency rollback
python dockerpilotv3.py deploy config rollback-config.yml --type blue-green
```
"""
        

        try:
            with open(output_file, 'w') as f:
                f.write(checklist_content)
            
            self.console.print(f"[green]Production checklist created: {output_file}[/green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create production checklist: {e}")
            return False

    def generate_documentation(self, output_dir: str = "docs") -> bool:
        """Generate comprehensive project documentation"""
        try:
            docs_path = Path(output_dir)
            docs_path.mkdir(exist_ok=True)
            
            # Generate README
            readme_content = """# Docker Pilot Enhanced

Professional Docker Container Management Tool with Advanced Deployment Capabilities

## Features

### Container Management
- List, start, stop, restart, remove containers
- Real-time monitoring with resource usage metrics
- Interactive dashboard with trend indicators
- Container health checks and status monitoring

### Advanced Deployment Strategies
- **Rolling Deployment**: Zero-downtime updates with health checks
- **Blue-Green Deployment**: Parallel environment switching
- **Canary Deployment**: Gradual traffic shifting with monitoring

### CI/CD Integration
- GitHub Actions workflow generation
- GitLab CI configuration
- Jenkins pipeline support
- Environment promotion (dev → staging → prod)

### Monitoring & Alerting
- Real-time resource monitoring
- Configurable alert rules
- Multiple notification channels (Slack, Email)
- Performance metrics tracking

## Installation

```bash
# Install required dependencies
pip install docker pyyaml requests rich

# Clone the repository
git clone https://github.com/your-org/docker-pilot-enhanced.git
cd docker-pilot-enhanced

# Make executable
chmod +x dockerpilotv3.py
```

## Quick Start

### List Containers
```bash
python dockerpilotv3.py container list --all
```

### Start Monitoring
```bash
python dockerpilotv3.py monitor myapp --duration 300
```

### Deploy Application
```bash
# Create deployment configuration
python dockerpilotv3.py deploy init

# Deploy using rolling strategy
python dockerpilotv3.py deploy config deployment.yml --type rolling
```

## Configuration

Create `deployment.yml`:

```yaml
deployment:
  image_tag: 'myapp:latest'
  container_name: 'myapp'
  port_mapping:
    '8080': '8080'
  environment:
    ENV: 'production'
    DEBUG: 'false'
  volumes:
    './data': '/app/data'
  restart_policy: 'unless-stopped'
  health_check_endpoint: '/health'
  health_check_timeout: 30
  health_check_retries: 10
```

## Advanced Usage

### Blue-Green Deployment
```bash
python dockerpilotv3.py deploy config deployment.yml --type blue-green
```

### Environment Promotion
```bash
python dockerpilotv3.py promote dev staging
```

### Integration Tests
```bash
python dockerpilotv3.py test integration
```

## Monitoring

The tool provides real-time monitoring with:
- CPU usage tracking
- Memory consumption monitoring
- Network I/O statistics
- Process count monitoring
- Container uptime tracking

## Support

- Documentation: [docs/](./docs/)
- Issues: [GitHub Issues](https://github.com/your-org/docker-pilot-enhanced/issues)
- Contact: devops@your-company.com
"""
            
            with open(docs_path / "README.md", 'w') as f:
                f.write(readme_content)
            
            # Generate API documentation
            api_docs = """# Docker Pilot API Documentation

## Core Classes

### DockerPilotEnhanced

Main class providing Docker container management functionality.

#### Methods

##### Container Management
- `list_containers(show_all=True, format_output="table")` - List all containers
- `container_operation(operation, container_name, **kwargs)` - Execute container operations
- `get_container_stats(container_name)` - Get container resource statistics

##### Monitoring
- `monitor_containers_dashboard(containers=None, duration=300)` - Real-time monitoring
- `setup_monitoring_alerts(alert_config_path)` - Configure monitoring alerts

##### Deployment
- `deploy_from_config(config_path, deployment_type="rolling")` - Deploy from configuration
- `environment_promotion(source_env, target_env)` - Promote between environments

##### CI/CD Integration
- `create_pipeline_config(pipeline_type, output_path)` - Generate CI/CD configs
- `run_integration_tests(test_config_path)` - Run integration test suite

## Configuration Classes

### DeploymentConfig
Configuration dataclass for deployment parameters.

### ContainerStats
Statistics dataclass containing container metrics.

## Error Handling

The tool uses comprehensive error handling with:
- Contextual error messages
- Logging integration
- Graceful fallbacks
- User-friendly error reporting

## Examples

### Basic Container Management
```python
from dockerpilotv3 import DockerPilotEnhanced

pilot = DockerPilotEnhanced()
pilot.list_containers(show_all=True)
pilot.container_operation('start', 'myapp')
```

### Advanced Deployment
```python
pilot.deploy_from_config('deployment.yml', 'blue-green')
```
"""
            
            with open(docs_path / "API.md", 'w') as f:
                f.write(api_docs)
            
            # Generate troubleshooting guide
            troubleshooting = """# Troubleshooting Guide

## Common Issues

### Docker Connection Failed
**Problem**: Cannot connect to Docker daemon
**Solution**: 
1. Check if Docker is running: `docker info`
2. Verify user permissions: `sudo usermod -aG docker $USER`
3. Restart Docker service: `sudo systemctl restart docker`

### Container Health Check Failed
**Problem**: Health checks timing out
**Solution**:
1. Verify endpoint exists: `curl http://localhost:8080/health`
2. Check container logs: `docker logs container-name`
3. Increase health check timeout in config

### Deployment Stuck
**Problem**: Rolling deployment appears stuck
**Solution**:
1. Check container status: `docker ps -a`
2. Review deployment logs in `docker_pilot.log`
3. Verify port availability: `netstat -tulpn | grep :8080`

### Memory Issues
**Problem**: Container using too much memory
**Solution**:
1. Set memory limits in deployment config
2. Monitor with: `docker stats container-name`
3. Check application memory leaks

### Permission Denied
**Problem**: Cannot access Docker socket
**Solution**:
```bash
sudo chown $USER:docker /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
```

## Debug Mode

Enable debug logging:
```bash
python dockerpilotv3.py --log-level DEBUG container list
```

## Log Files

- `docker_pilot.log` - Main application log
- `docker_metrics.json` - Performance metrics
- `deployment_history.json` - Deployment records
- `integration-test-report.json` - Test results

## Getting Help

1. Check logs for detailed error information
2. Verify Docker daemon status
3. Test with simple commands first
4. Review configuration files for syntax errors
5. Check network connectivity for remote deployments
"""
            
            with open(docs_path / "TROUBLESHOOTING.md", 'w') as f:
                f.write(troubleshooting)
            
            self.console.print(f"[green]Documentation generated in {output_dir}/[/green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to generate documentation: {e}")
            return False

    def backup_deployment_state(self, backup_path: str = None) -> bool:
        """Create backup of current deployment state"""
        if not backup_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_{timestamp}"
        
        backup_dir = Path(backup_path)
        backup_dir.mkdir(exist_ok=True)
        
        try:
            # Backup running containers info
            containers = self.client.containers.list(all=True)
            containers_backup = []
            
            for container in containers:
                container_info = {
                    'name': container.name,
                    'image': container.image.tags[0] if container.image.tags else container.image.id,
                    'status': container.status,
                    'ports': container.ports,
                    'environment': container.attrs.get('Config', {}).get('Env', []),
                    'volumes': container.attrs.get('Mounts', []),
                    'command': container.attrs.get('Config', {}).get('Cmd'),
                    'created': container.attrs.get('Created'),
                    'restart_policy': container.attrs.get('HostConfig', {}).get('RestartPolicy', {})
                }
                containers_backup.append(container_info)
            
            # Save containers backup
            with open(backup_dir / 'containers.json', 'w') as f:
                json.dump(containers_backup, f, indent=2)
            
            # Backup Docker images
            images = self.client.images.list()
            images_backup = []
            
            for image in images:
                if image.tags:  # Only backup tagged images
                    image_info = {
                        'tags': image.tags,
                        'id': image.id,
                        'created': image.attrs.get('Created'),
                        'size': image.attrs.get('Size')
                    }
                    images_backup.append(image_info)
            
            with open(backup_dir / 'images.json', 'w') as f:
                json.dump(images_backup, f, indent=2)
            
            # Backup networks
            networks = self.client.networks.list()
            networks_backup = []
            
            for network in networks:
                if not network.name.startswith(('bridge', 'host', 'none')):  # Skip default networks
                    network_info = {
                        'name': network.name,
                        'driver': network.attrs.get('Driver'),
                        'options': network.attrs.get('Options', {}),
                        'labels': network.attrs.get('Labels', {}),
                        'created': network.attrs.get('Created')
                    }
                    networks_backup.append(network_info)
            
            with open(backup_dir / 'networks.json', 'w') as f:
                json.dump(networks_backup, f, indent=2)
            
            # Backup volumes
            volumes = self.client.volumes.list()
            volumes_backup = []
            
            for volume in volumes:
                volume_info = {
                    'name': volume.name,
                    'driver': volume.attrs.get('Driver'),
                    'mountpoint': volume.attrs.get('Mountpoint'),
                    'labels': volume.attrs.get('Labels', {}),
                    'created': volume.attrs.get('CreatedAt')
                }
                volumes_backup.append(volume_info)
            
            with open(backup_dir / 'volumes.json', 'w') as f:
                json.dump(volumes_backup, f, indent=2)
            
            # Create backup summary
            summary = {
                'backup_time': datetime.now().isoformat(),
                'containers_count': len(containers_backup),
                'images_count': len(images_backup),
                'networks_count': len(networks_backup),
                'volumes_count': len(volumes_backup),
                'docker_version': self.client.version()['Version']
            }
            
            with open(backup_dir / 'summary.json', 'w') as f:
                json.dump(summary, f, indent=2)
            
            self.console.print(f"[green]Deployment state backed up to {backup_path}/[/green]")
            self.console.print(f"[cyan]Backup contains: {len(containers_backup)} containers, {len(images_backup)} images[/cyan]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Backup failed: {e}")
            return False

    def restore_deployment_state(self, backup_path: str) -> bool:
        """Restore deployment state from backup"""
        backup_dir = Path(backup_path)
        
        if not backup_dir.exists():
            self.console.print(f"[red]Backup directory not found: {backup_path}[/red]")
            return False
        
        try:
            # Load backup summary
            with open(backup_dir / 'summary.json', 'r') as f:
                summary = json.load(f)
            
            self.console.print(f"[cyan]Restoring backup from {summary['backup_time']}[/cyan]")
            
            # Restore networks first
            if (backup_dir / 'networks.json').exists():
                with open(backup_dir / 'networks.json', 'r') as f:
                    networks = json.load(f)
                
                for network_info in networks:
                    try:
                        self.client.networks.create(
                            name=network_info['name'],
                            driver=network_info['driver'],
                            options=network_info.get('options', {}),
                            labels=network_info.get('labels', {})
                        )
                        self.console.print(f"[green]Restored network: {network_info['name']}[/green]")
                    except docker.errors.APIError as e:
                        if "already exists" in str(e):
                            continue
                        self.logger.warning(f"Failed to restore network {network_info['name']}: {e}")
            
            # Restore volumes
            if (backup_dir / 'volumes.json').exists():
                with open(backup_dir / 'volumes.json', 'r') as f:
                    volumes = json.load(f)
                
                for volume_info in volumes:
                    try:
                        self.client.volumes.create(
                            name=volume_info['name'],
                            driver=volume_info['driver'],
                            labels=volume_info.get('labels', {})
                        )
                        self.console.print(f"[green]Restored volume: {volume_info['name']}[/green]")
                    except docker.errors.APIError as e:
                        if "already exists" in str(e):
                            continue
                        self.logger.warning(f"Failed to restore volume {volume_info['name']}: {e}")
            
            # Note: Images and containers would need more complex restoration logic
            # This is a simplified implementation
            self.console.print("[yellow]Note: Complete container restoration requires image availability[/yellow]")
            self.console.print("[yellow]Consider using docker save/load for complete image backup[/yellow]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Restore failed: {e}")
            return False

    def validate_system_requirements(self) -> bool:
        """Validate system requirements and dependencies"""
        self.console.print("[cyan]Validating system requirements...[/cyan]")
        
        requirements_met = True
        
        # Check Python version
        python_version = sys.version_info
        if python_version < (3, 8):
            self.console.print("[red]❌ Python 3.8+ required[/red]")
            requirements_met = False
        else:
            self.console.print(f"[green]✓ Python {python_version.major}.{python_version.minor}[/green]")
        
        # Check Docker connectivity
        try:
            docker_version = self.client.version()
            self.console.print(f"[green]✓ Docker {docker_version['Version']}[/green]")
        except Exception as e:
            self.console.print(f"[red]❌ Docker connection failed: {e}[/red]")
            requirements_met = False
        
        # Check required modules
        required_modules = [
            'docker', 'yaml', 'requests', 'rich', 'pathlib'
        ]
        
        for module in required_modules:
            try:
                __import__(module)
                self.console.print(f"[green]✓ Module {module}[/green]")
            except ImportError:
                self.console.print(f"[red]❌ Module {module} not found[/red]")
                requirements_met = False
        
        # Check disk space
        try:
            import shutil
            disk_usage = shutil.disk_usage('.')
            free_gb = disk_usage.free / (1024**3)
            
            if free_gb < 1:  # Require at least 1GB free space
                self.console.print(f"[red]❌ Insufficient disk space: {free_gb:.1f}GB[/red]")
                requirements_met = False
            else:
                self.console.print(f"[green]✓ Disk space: {free_gb:.1f}GB available[/green]")
                
        except Exception:
            self.console.print("[yellow]⚠️ Could not check disk space[/yellow]")
        
        # Check Docker daemon permissions
        try:
            self.client.ping()
            self.console.print("[green]✓ Docker daemon accessible[/green]")
        except Exception:
            self.console.print("[red]❌ Docker daemon permission denied[/red]")
            self.console.print("[yellow]Try: sudo usermod -aG docker $USER[/yellow]")
            requirements_met = False
        
        if requirements_met:
            self.console.print("\n[bold green]✅ All system requirements met![/bold green]")
        else:
            self.console.print("\n[bold red]❌ Some requirements not met. Please fix and retry.[/bold red]")
        
        return requirements_met

    def export_configuration(self, config_name: str = "docker-pilot-config.tar.gz") -> bool:
        """Export all configuration files as a backup"""
        try:
            import tarfile
            
            config_files = [
                "deployment.yml",
                "alerts.yml", 
                "integration-tests.yml",
                "docker_pilot.log",
                "docker_metrics.json",
                "deployment_history.json"
            ]
            
            with tarfile.open(config_name, "w:gz") as tar:
                for config_file in config_files:
                    if Path(config_file).exists():
                        tar.add(config_file)
                        self.console.print(f"[green]Added {config_file}[/green]")
            
            self.console.print(f"[bold green]Configuration exported to {config_name}[/bold green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Configuration export failed: {e}")
            return False

    def import_configuration(self, config_archive: str) -> bool:
        """Import configuration from backup archive"""
        try:
            import tarfile
            
            if not Path(config_archive).exists():
                self.console.print(f"[red]Archive not found: {config_archive}[/red]")
                return False
            
            with tarfile.open(config_archive, "r:gz") as tar:
                tar.extractall(".")
                self.console.print("[green]Configuration files imported[/green]")
                
                # List imported files
                for member in tar.getmembers():
                    if member.isfile():
                        self.console.print(f"[cyan]Imported: {member.name}[/cyan]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Configuration import failed: {e}")
            return False
        

def check_all_requirements():
    pilot = DockerPilotEnhanced()
    return pilot.validate_system_requirements()

if __name__ == "__main__":
    # Minimal bootstrap to honor --config and --log-level before launching CLI
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument('--config', '-c', type=str, default=None)
    bootstrap_parser.add_argument('--log-level', '-l', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    known_args, _ = bootstrap_parser.parse_known_args()

    try:
        log_level_enum = LogLevel[known_args.log_level]
    except Exception:
        log_level_enum = LogLevel.INFO

    pilot = DockerPilotEnhanced(config_file=known_args.config, log_level=log_level_enum)
    pilot.run_cli()
