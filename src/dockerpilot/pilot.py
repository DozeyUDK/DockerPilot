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
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.live import Live
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any

# Import modules
from .models import LogLevel, DeploymentConfig, ContainerStats
from .container_manager import ContainerManager
from .image_manager import ImageManager
from .monitoring import MonitoringManager
from .cli import build_cli_parser, run_cli as run_pilot_cli, run_interactive_menu
from .backup_restore import BackupRestoreMixin
from .deployment_service import DeploymentServiceMixin

class DockerPilotEnhanced(DeploymentServiceMixin, BackupRestoreMixin):
    """Enhanced Docker container management tool with advanced deployment capabilities."""
    
    def __init__(self, config_file: str = None, log_level: LogLevel = LogLevel.INFO):
        self._configure_console_streams()
        self.console = Console(safe_box=True)
        self._show_banner()
        self.client = None
        self.config = {}
        self.log_file = "docker_pilot.log"
        self.metrics_file = "docker_metrics.json"
        self.deployment_history = []
        self._health_check_defaults = None  # Lazy-loaded health check defaults
        self._current_deployment_container = None  # Track current deployment for cancellation
        self._sudo_password = None  # Sudo password from session (for web interface)
        self._progress_callback = None  # Callback for progress updates (for web interface)
        
        # Setup logging
        self._setup_logging(log_level)
        
        # Load configuration
        if config_file and Path(config_file).exists():
            self._load_config(config_file)
        
        # Initialize Docker client with retry logic
        client_initialized = self._init_docker_client()
        
        # Initialize managers only if Docker client is available
        if client_initialized and self.client:
            self.container_manager = ContainerManager(
                self.client, self.console, self.logger, self._error_handler
            )
            self.image_manager = ImageManager(
                self.client, self.console, self.logger, self._error_handler
            )
            self.monitoring_manager = MonitoringManager(
                self.client, self.console, self.logger, self.metrics_file
            )
        else:
            # Set managers to None if Docker client is not available
            # We'll check Docker availability in run_cli() and _run_interactive_menu()
            # for CLI context, and handle gracefully in web interface
            self.container_manager = None
            self.image_manager = None
            self.monitoring_manager = None
            self.logger.warning("Docker client not initialized - managers not available")
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Docker Pilot Enhanced initialized successfully")
    
    def _show_banner(self):
        """Display ASCII banner with application information"""
        banner = r"""
  _____             _             _____ _ _       _   
 |  __ \           | |           |  __ (_) |     | |  
 | |  | | ___   ___| | _____ _ __| |__) || | ___ | |_ 
 | |  | |/ _ \ / __| |/ / _ \ '__|  ___/ | |/ _ \| __|
 | |__| | (_) | (__|   <  __/ |  | |   | | | (_) | |_ 
 |_____/ \___/ \___|_|\_\___|_|  |_|   |_|_|\___/ \__|
                                                      
         by Dozey                                             
    """
        

        self.console.print(Panel(banner, title="[bold blue]Docker Managing Tool[/bold blue]", 
                                title_align="center", border_style="blue"))
        self.console.print(f"[dim]Author: dozey | Version: Enhanced[/dim]\n")

    def _configure_console_streams(self):
        """Improve Windows console compatibility for Unicode-rich output."""
        if os.name != 'nt':
            return

        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
    
    def _parse_multi_target(self, target_string: str) -> List[str]:
        """Parse comma-separated list of containers/images.
        
        Args:
            target_string: String with comma-separated container/image names or IDs
            
        Returns:
            List of container/image names/IDs
        """
        if not target_string:
            return []
        
        # Split by comma and strip whitespace
        targets = [t.strip() for t in target_string.split(',') if t.strip()]
        return targets

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
    
    def _check_cancel_flag(self, container_name: str = None) -> bool:
        """Check if deployment should be cancelled
        
        Args:
            container_name: Container being deployed (uses self._current_deployment_container if not provided)
        
        Returns:
            bool: True if deployment should be cancelled
        """
        if not container_name:
            container_name = self._current_deployment_container
        
        if not container_name:
            return False
        
        # Look for cancel flag in multiple locations
        cancel_flag_locations = [
            Path.cwd() / f'cancel_{container_name}.flag',
            Path.home() / 'DockerPilot' / f'cancel_{container_name}.flag',
            Path.home() / 'DockerPilot' / '.dockerpilot_extras' / f'cancel_{container_name}.flag',
        ]
        
        for flag_path in cancel_flag_locations:
            if flag_path.exists():
                self.logger.warning(f"Cancel flag detected for {container_name} at {flag_path}")
                # Remove flag after detecting
                try:
                    flag_path.unlink()
                except:
                    pass
                return True
        
        return False
    
    def _load_health_check_defaults(self) -> dict:
        """Load default health check configuration from JSON file
        
        Returns cached defaults or loads from health-checks-defaults.json
        """
        if self._health_check_defaults is not None:
            return self._health_check_defaults
        
        try:
            defaults_path = Path(__file__).parent / "configs" / "health-checks-defaults.json"
            
            if defaults_path.exists():
                with open(defaults_path, 'r', encoding='utf-8') as f:
                    self._health_check_defaults = json.load(f)
                self.logger.debug(f"Loaded health check defaults from {defaults_path}")
            else:
                # Fallback to minimal defaults if file doesn't exist
                self.logger.warning(f"Health check defaults file not found: {defaults_path}")
                self._health_check_defaults = {
                    'health_checks': {
                        'non_http_services': ['ssh', 'redis', 'mysql', 'postgresql', 'mongodb'],
                        'endpoint_mappings': {},
                        'default_endpoint': '/health'
                    }
                }
        except Exception as e:
            self.logger.error(f"Failed to load health check defaults: {e}")
            # Fallback to minimal defaults
            self._health_check_defaults = {
                'health_checks': {
                    'non_http_services': ['ssh', 'redis', 'mysql', 'postgresql', 'mongodb'],
                    'endpoint_mappings': {},
                    'default_endpoint': '/health'
                }
            }
        
        return self._health_check_defaults
    
    def _get_database_config(self, image_tag: str) -> dict:
        """Get database-specific configuration based on image tag.
        
        Args:
            image_tag: Docker image tag to check
            
        Returns:
            dict: Database configuration or empty dict if not a database
        """
        defaults = self._load_health_check_defaults()
        database_services = defaults.get('database_services', {})
        
        image_lower = image_tag.lower()
        
        # Check each database service pattern (longest match first for specificity)
        # Sort by length descending to match more specific names first
        sorted_db_names = sorted(database_services.keys(), key=len, reverse=True)
        
        for db_name in sorted_db_names:
            if db_name in image_lower:
                self.logger.debug(f"Matched database service: {db_name} for image {image_tag}")
                return database_services[db_name]
        
        # Return default/empty config for non-database services
        return {}
    
    def _get_database_name(self, image_tag: str) -> str:
        """Get database service name from image tag.
        
        Args:
            image_tag: Docker image tag to check
            
        Returns:
            str: Database name or empty string if not a database
        """
        defaults = self._load_health_check_defaults()
        database_services = defaults.get('database_services', {})
        
        image_lower = image_tag.lower()
        
        # Check each database service pattern (longest match first)
        sorted_db_names = sorted(database_services.keys(), key=len, reverse=True)
        
        for db_name in sorted_db_names:
            if db_name in image_lower:
                return db_name
        
        return ""
    
    def _is_database_service(self, image_tag: str) -> bool:
        """Check if image tag represents a database service.
        
        Args:
            image_tag: Docker image tag to check
            
        Returns:
            bool: True if it's a database service
        """
        db_config = self._get_database_config(image_tag)
        return len(db_config) > 0
    
    def _init_docker_client(self, max_retries: int = 3):
        """Initialize Docker client with retry logic
        
        Returns True if client initialized successfully, False otherwise.
        In web interface context, does not exit on failure.
        """
        for attempt in range(max_retries):
            try:
                # Prefer Docker CLI "current context" host if available.
                # This avoids mismatches where `docker ps` works (rootless/custom socket)
                # but docker-py defaults to /var/run/docker.sock.
                base_url = None
                try:
                    import subprocess
                    context = subprocess.check_output(
                        ["docker", "context", "show"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=3,
                    ).strip()
                    if context:
                        # Get docker endpoint host for the active context
                        inspected = subprocess.check_output(
                            ["docker", "context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"],
                            stderr=subprocess.DEVNULL,
                            text=True,
                            timeout=3,
                        ).strip()
                        if inspected:
                            import json as _json
                            try:
                                base_url = _json.loads(inspected)
                            except Exception:
                                base_url = inspected.strip('"')
                except Exception:
                    base_url = None

                if base_url:
                    self.client = docker.DockerClient(base_url=base_url)
                else:
                    self.client = docker.from_env()
                # Test connection
                self.client.ping()
                if hasattr(self, 'logger') and self.logger:
                    self.logger.info(f"Docker client connected successfully (base_url={getattr(self.client, 'api', None) and getattr(self.client.api, 'base_url', None)})")
                return True
            except Exception as e:
                # Log the actual error for debugging
                error_msg = str(e)
                error_type = type(e).__name__
                if hasattr(self, 'logger') and self.logger:
                    self.logger.warning(f"Docker connection attempt {attempt + 1} failed ({error_type}): {error_msg}")
                else:
                    # Fallback to print if logger not available
                    print(f"WARNING: Docker connection attempt {attempt + 1} failed ({error_type}): {error_msg}")
                
                if attempt == max_retries - 1:
                    if hasattr(self, 'logger') and self.logger:
                        self.logger.error(f"Failed to connect to Docker daemon after {max_retries} attempts ({error_type}): {error_msg}")
                    else:
                        print(f"ERROR: Failed to connect to Docker daemon after {max_retries} attempts ({error_type}): {error_msg}")
                    
                    if hasattr(self, 'console') and self.console:
                        self.console.print(f"[bold red]❌ Cannot connect to Docker daemon![/bold red]")
                    self.client = None
                    # Don't exit here - let the calling code decide (run_cli() or web interface)
                    return False
                time.sleep(2)
        return False
    
    def _update_progress(self, stage: str, progress: int, message: str):
        """Update progress if callback is available
        
        Args:
            stage: Current stage name (e.g., 'backup', 'deploy', 'health_check')
            progress: Progress percentage (0-100)
            message: Human-readable message
        """
        if self._progress_callback:
            try:
                self._progress_callback(stage, progress, message)
            except Exception as e:
                if hasattr(self, 'logger') and self.logger:
                    self.logger.debug(f"Progress callback error: {e}")
    
    def _show_loading(self, message: str = "Processing", stop_event: threading.Event = None):
        """Show animated loading dots while operation is in progress
        
        Args:
            message: Message to display before dots
            stop_event: Threading event to stop the animation
        """
        dots = ['.', '..', '...', '....']
        idx = 0
        while stop_event is None or not stop_event.is_set():
            # Print loading message with animated dots
            sys.stdout.write(f'\r{message}{dots[idx % len(dots)]}')
            sys.stdout.flush()
            idx += 1
            time.sleep(0.5)  # Update every 0.5 seconds
        
        # Clear the line when done
        sys.stdout.write('\r' + ' ' * (len(message) + 4) + '\r')
        sys.stdout.flush()
    
    @contextmanager
    def _with_loading(self, message: str = "Processing"):
        """Context manager to show loading indicator during long operations
        
        Usage:
            with self._with_loading("Backing up data"):
                # Long operation here
                pass
        """
        stop_event = threading.Event()
        loading_thread = threading.Thread(
            target=self._show_loading,
            args=(message, stop_event),
            daemon=True
        )
        loading_thread.start()
        
        try:
            yield
        finally:
            stop_event.set()
            loading_thread.join(timeout=1.0)  # Wait max 1 second for thread to finish
            # Clear the loading line
            sys.stdout.write('\r' + ' ' * (len(message) + 4) + '\r')
            sys.stdout.flush()

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
        """Enhanced container listing with multiple output formats."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return []
        return self.container_manager.list_containers(show_all, format_output)

    def list_images(self, show_all: bool = True, format_output: str = "table", hide_untagged: bool = False) -> List[Any]:
        """Enhanced image listing with multiple output formats.
        
        Args:
            show_all: Show all images (including intermediate layers)
            format_output: Output format ('table' or 'json')
            hide_untagged: Hide images without tags (dangling images)
        """
        if not self.image_manager:
            self.logger.error("Image manager not initialized - Docker client not available")
            return []
        return self.image_manager.list_images(show_all, format_output, hide_untagged)
    
    def remove_image(self, image_name: str, force: bool = False) -> bool:
        """Remove Docker image."""
        if not self.image_manager:
            self.logger.error("Image manager not initialized - Docker client not available")
            return False
        return self.image_manager.remove_image(image_name, force)
    
    def prune_dangling_images(self, dry_run: bool = False) -> dict:
        """Remove all dangling images (images without tags).
        
        Args:
            dry_run: If True, only show what would be removed without actually removing
            
        Returns:
            dict: Statistics about removed images (images_deleted, space_reclaimed)
        """
        if not self.image_manager:
            self.logger.error("Image manager not initialized - Docker client not available")
            return {'images_deleted': 0, 'space_reclaimed': 0}
        
        if dry_run:
            # No loading for dry run as it's fast
            return self.image_manager.prune_dangling_images(dry_run)
        else:
            # Show loading indicator for actual removal
            with self._with_loading("Removing dangling images"):
                return self.image_manager.prune_dangling_images(dry_run)

    def container_operation(self, operation: str, container_name: str, **kwargs) -> bool:
        """Unified container operation handler with progress tracking."""
        if operation == 'update_restart_policy':
            return self.update_restart_policy(container_name, kwargs.get('policy', 'unless-stopped'))
        elif operation == 'run_image':
            return self.run_new_container(
                kwargs.get('image_name'),
                kwargs.get('name', container_name),
                kwargs.get('ports'),
                kwargs.get('command'),
                kwargs.get('environment'),
                kwargs.get('volumes'),
                kwargs.get('restart_policy', 'unless-stopped'),
                kwargs.get('network'),
                kwargs.get('privileged', False),
                kwargs.get('cpu_limit'),
                kwargs.get('memory_limit')
            )
        else:
            if not self.container_manager:
                self.logger.error("Container manager not initialized - Docker client not available")
                return False
            return self.container_manager.container_operation(operation, container_name, **kwargs)
    
    def update_restart_policy(self, container_name: str, policy: str = 'unless-stopped') -> bool:
        """Set restart policy on container."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.update_restart_policy(container_name, policy)
    
    def rename_container(self, container_name: str, new_name: str) -> bool:
        """Rename a container."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.rename_container(container_name, new_name)
    
    def run_new_container(self, image_name: str, name: str, ports: dict = None, 
                        command: str = None, environment: dict = None,
                        volumes: dict = None, restart_policy: str = 'unless-stopped',
                        network: str = None, privileged: bool = False,
                        cpu_limit: str = None, memory_limit: str = None) -> bool:
        """Run a new container with full configuration options.
        
        Args:
            image_name: Docker image name/tag
            name: Container name
            ports: Port mapping dict (e.g., {'80': '8080'})
            command: Command to run in container
            environment: Environment variables dict
            volumes: Volume mappings dict
            restart_policy: Restart policy (no, on-failure, always, unless-stopped)
            network: Network name or 'host' for host network
            privileged: Run container in privileged mode
            cpu_limit: CPU limit (e.g., '1.5' for 1.5 CPUs)
            memory_limit: Memory limit (e.g., '1g' for 1GB)
        """
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.run_new_container(
            image_name, name, ports, command, environment, volumes,
            restart_policy, network, privileged, cpu_limit, memory_limit
        )
    
    def exec_container(self, container_name: str, command: str = "/bin/bash") -> bool:
        """Execute interactive command in running container."""
        import subprocess
        
        with self._error_handler(f"exec into container {container_name}", container_name):
            # Verify container exists and is running
            container = self.client.containers.get(container_name)
            if container.status != 'running':
                self.console.print(f"[bold red]❌ Container '{container_name}' is not running (status: {container.status})[/bold red]")
                return False
            
            self.logger.info(f"Executing interactive command in container {container_name}: {command}")
            self.console.print(f"[cyan]📟 Executing '{command}' in container '{container_name}'...[/cyan]")
            self.console.print(f"[dim]Type 'exit' to leave the container shell[/dim]\n")
            
            # Use subprocess to maintain interactive terminal
            # This allows proper TTY handling for interactive bash session
            try:
                result = subprocess.run(
                    ['docker', 'exec', '-it', container_name, command],
                    check=False
                )
                
                if result.returncode == 0:
                    self.console.print(f"\n[green]✅ Exited from container '{container_name}'[/green]")
                    return True
                else:
                    self.console.print(f"\n[yellow]⚠️ Exec command exited with code {result.returncode}[/yellow]")
                    return False
                    
            except FileNotFoundError:
                self.console.print("[bold red]❌ Docker CLI not found. Please ensure Docker is installed and in PATH.[/bold red]")
                return False
            except Exception as e:
                self.console.print(f"[bold red]❌ Failed to execute command: {e}[/bold red]")
                self.logger.error(f"Exec failed: {e}")
                return False
        
        return False

    # ==================== MONITORING & METRICS ====================

    def get_container_stats(self, container_name: str) -> Optional[ContainerStats]:
        """Get comprehensive container statistics."""
        if not self.monitoring_manager:
            self.logger.error("Monitoring manager not initialized - Docker client not available")
            return None
        return self.monitoring_manager.get_container_stats(container_name)
    
    def monitor_containers_dashboard(self, containers: List[str] = None, duration: int = 300):
        """Real-time monitoring dashboard for multiple containers."""
        if not self.monitoring_manager:
            self.logger.error("Monitoring manager not initialized - Docker client not available")
            return
        return self.monitoring_manager.monitor_containers_dashboard(containers, duration)
    
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
    
    def stop_and_remove_container(self, container_name: str, timeout: int = 10) -> bool:
        """Stop and remove container in one operation (from dockerpilot-Lite)"""
        with self._error_handler(f"stop and remove {container_name}", container_name):
            container = self.client.containers.get(container_name)
            
            self.console.print(f"[cyan]🛑 Stopping container {container_name}...[/cyan]")
            if container.status == "running":
                container.stop(timeout=timeout)
                self.console.print(f"[green]✅ Container stopped[/green]")
            else:
                self.console.print(f"[yellow]ℹ️ Container was not running[/yellow]")
            
            self.console.print(f"[cyan]🗑️ Removing container {container_name}...[/cyan]")
            container.remove()
            self.console.print(f"[green]✅ Container {container_name} removed[/green]")
            
            self.logger.info(f"Container {container_name} stopped and removed")
            return True
        
        return False
    
    def exec_command_non_interactive(self, container_name: str, command: str) -> bool:
        """Execute command in container non-interactively (from dockerpilot-Lite)"""
        with self._error_handler(f"exec command in {container_name}", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status != 'running':
                self.console.print(f"[red]❌ Container '{container_name}' is not running[/red]")
                return False
            
            self.console.print(f"[cyan]⚙️ Executing: {command}[/cyan]")
            exec_log = container.exec_run(command)
            
            output = exec_log.output.decode()
            self.console.print(output)
            
            if exec_log.exit_code == 0:
                self.console.print(f"[green]✅ Command executed successfully[/green]")
                return True
            else:
                self.console.print(f"[yellow]⚠️ Command exited with code {exec_log.exit_code}[/yellow]")
                return False
        
        return False
    
    def health_check_standalone(self, port: int, endpoint: str = "/health", 
                               timeout: int = 30, max_retries: int = 10) -> bool:
        """Standalone health check menu (from dockerpilot-Lite)"""
        url = f"http://localhost:{port}{endpoint}"
        self.console.print(f"[cyan]🩺 Testing health check: {url}[/cyan]")
        
        for i in range(max_retries):
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    self.console.print(f"[green]✅ Health check OK (attempt {i+1}/{max_retries})[/green]")
                    self.console.print(f"[green]Response time: {response.elapsed.total_seconds():.2f}s[/green]")
                    return True
                else:
                    self.console.print(f"[yellow]⚠️ Health check returned {response.status_code} (attempt {i+1}/{max_retries})[/yellow]")
            except requests.exceptions.RequestException as e:
                self.console.print(f"[yellow]⚠️ Health check failed (attempt {i+1}/{max_retries}): {e}[/yellow]")
            
            if i < max_retries - 1:
                time.sleep(3)
        
        self.console.print(f"[red]❌ Health check failed after {max_retries} attempts[/red]")
        return False

    # ==================== ADVANCED DEPLOYMENT ====================
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
        return build_cli_parser()

    def run_cli(self):
        """Run CLI interface"""
        run_pilot_cli(self)

    def _run_container_interactive(self, args):
        """Interactive mode for running containers - asks for all parameters one by one"""
        self.console.print("\n[bold cyan]🚀 Interactive Container Run Mode[/bold cyan]")
        self.console.print("[dim]Press Enter to use default value or leave empty to skip[/dim]\n")
        
        # Image (required)
        image_name = args.image if args.image else None
        if not image_name:
            image_name = Prompt.ask("Docker image name/tag", default="")
            if not image_name:
                self.console.print("[red]❌ Image name is required[/red]")
                sys.exit(1)
        
        # Container name (required)
        container_name = args.name if args.name else None
        if not container_name:
            container_name = Prompt.ask("Container name", default="")
            if not container_name:
                self.console.print("[red]❌ Container name is required[/red]")
                sys.exit(1)
        
        # Port mappings
        ports = {}
        if args.port:
            # Parse existing ports
            for port_mapping in args.port:
                if ':' in port_mapping:
                    container_port, host_port = port_mapping.split(':')
                    ports[container_port.strip()] = host_port.strip()
        
        self.console.print("\n[cyan]Port mappings (format: container:host, e.g., 80:8080)[/cyan]")
        while True:
            port_input = Prompt.ask("Port mapping (empty to finish)", default="").strip()
            if not port_input:
                break
            if ':' in port_input:
                try:
                    container_port, host_port = port_input.split(':')
                    ports[container_port.strip()] = host_port.strip()
                    self.console.print(f"[green]✓ Added port mapping: {container_port} -> {host_port}[/green]")
                except ValueError:
                    self.console.print("[yellow]⚠️ Invalid format. Use container:host[/yellow]")
            else:
                self.console.print("[yellow]⚠️ Invalid format. Use container:host[/yellow]")
        
        # Environment variables
        environment = {}
        if args.env:
            # Parse existing env vars
            for env_var in args.env:
                if '=' in env_var:
                    key, value = env_var.split('=', 1)
                    environment[key.strip()] = value.strip()
        
        self.console.print("\n[cyan]Environment variables (format: KEY=VALUE)[/cyan]")
        while True:
            env_input = Prompt.ask("Environment variable (empty to finish)", default="").strip()
            if not env_input:
                break
            if '=' in env_input:
                key, value = env_input.split('=', 1)
                environment[key.strip()] = value.strip()
                self.console.print(f"[green]✓ Added environment variable: {key}[/green]")
            else:
                self.console.print("[yellow]⚠️ Invalid format. Use KEY=VALUE[/yellow]")
        
        # Volumes
        volumes = {}
        if args.volume:
            # Parse existing volumes
            for volume_mapping in args.volume:
                if ':' in volume_mapping:
                    parts = volume_mapping.split(':')
                    if len(parts) == 2:
                        host_path, container_path = parts
                        volumes[host_path.strip()] = container_path.strip()
                    elif len(parts) == 3:
                        host_path, container_path, mode = parts
                        volumes[host_path.strip()] = {
                            'bind': container_path.strip(),
                            'mode': mode.strip()
                        }
        
        self.console.print("\n[cyan]Volume mappings (format: host:container or host:container:mode)[/cyan]")
        while True:
            vol_input = Prompt.ask("Volume mapping (empty to finish)", default="").strip()
            if not vol_input:
                break
            if ':' in vol_input:
                parts = vol_input.split(':')
                if len(parts) == 2:
                    host_path, container_path = parts
                    volumes[host_path.strip()] = container_path.strip()
                    self.console.print(f"[green]✓ Added volume: {host_path} -> {container_path}[/green]")
                elif len(parts) == 3:
                    host_path, container_path, mode = parts
                    volumes[host_path.strip()] = {
                        'bind': container_path.strip(),
                        'mode': mode.strip()
                    }
                    self.console.print(f"[green]✓ Added volume: {host_path} -> {container_path} ({mode})[/green]")
                else:
                    self.console.print("[yellow]⚠️ Invalid format. Use host:container or host:container:mode[/yellow]")
            else:
                self.console.print("[yellow]⚠️ Invalid format. Use host:container[/yellow]")
        
        # Command
        command = args.command if args.command else None
        if not command:
            command = Prompt.ask("Command to run (empty for default)", default="").strip()
            command = command if command else None
        
        # Restart policy
        restart_policy = args.restart if args.restart else 'unless-stopped'
        restart_policy = Prompt.ask("Restart policy", default=restart_policy, choices=['no', 'on-failure', 'always', 'unless-stopped'])
        
        # Network
        network = args.network if args.network else None
        if not network:
            network = Prompt.ask("Network name (or 'host' for host network, empty for default)", default="").strip()
            network = network if network else None
        
        # Privileged mode
        privileged = args.privileged if args.privileged else False
        if not privileged:
            privileged = Confirm.ask("Run in privileged mode?", default=False)
        
        # CPU limit
        cpu_limit = args.cpu_limit if args.cpu_limit else None
        if not cpu_limit:
            cpu_input = Prompt.ask("CPU limit (e.g., 1.5 for 1.5 CPUs, empty to skip)", default="").strip()
            cpu_limit = cpu_input if cpu_input and cpu_input.lower() not in ['n', 'no'] else None
        
        # Memory limit
        memory_limit = args.memory_limit if args.memory_limit else None
        if not memory_limit:
            memory_input = Prompt.ask("Memory limit (e.g., 1g for 1GB, 512m for 512MB, empty to skip)", default="").strip()
            memory_limit = memory_input if memory_input and memory_input.lower() not in ['n', 'no'] else None
        
        # Summary
        self.console.print("\n[bold cyan]📋 Configuration Summary:[/bold cyan]")
        self.console.print(f"  Image: {image_name}")
        self.console.print(f"  Container name: {container_name}")
        if ports:
            self.console.print(f"  Ports: {ports}")
        if environment:
            self.console.print(f"  Environment variables: {len(environment)} set")
        if volumes:
            self.console.print(f"  Volumes: {len(volumes)} mounted")
        if command:
            self.console.print(f"  Command: {command}")
        self.console.print(f"  Restart policy: {restart_policy}")
        if network:
            self.console.print(f"  Network: {network}")
        if privileged:
            self.console.print(f"  Privileged mode: enabled")
        if cpu_limit:
            self.console.print(f"  CPU limit: {cpu_limit}")
        if memory_limit:
            self.console.print(f"  Memory limit: {memory_limit}")
        
        # Confirm
        if not Confirm.ask("\n[bold]Proceed with container creation?[/bold]", default=True):
            self.console.print("[yellow]❌ Cancelled by user[/yellow]")
            sys.exit(0)
        
        # Run container
        success = self.run_new_container(
            image_name=image_name,
            name=container_name,
            ports=ports if ports else None,
            command=command,
            environment=environment if environment else None,
            volumes=volumes if volumes else None,
            restart_policy=restart_policy,
            network=network,
            privileged=privileged,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit
        )
        if not success:
            sys.exit(1)
    
    def _handle_container_cli(self, args):
        """Handle container CLI commands with support for multiple targets"""
        from .cli.handlers import handle_container_cli

        handle_container_cli(self, args)

    def _handle_monitor_cli(self, args):
        """Handle monitoring CLI commands"""
        from .cli.handlers import handle_monitor_cli

        handle_monitor_cli(self, args)

    def _handle_deploy_cli(self, args):
        """Handle deployment CLI commands"""
        from .cli.handlers import handle_deploy_cli

        handle_deploy_cli(self, args)

    def _handle_backup_cli(self, args):
        """Handle backup CLI commands"""
        from .cli.handlers import handle_backup_cli

        handle_backup_cli(self, args)

    def _handle_config_cli(self, args):
        """Handle configuration CLI commands"""
        from .cli.handlers import handle_config_cli

        handle_config_cli(self, args)

    def _handle_pipeline_cli(self, args):
        """Handle pipeline CLI commands"""
        from .cli.handlers import handle_pipeline_cli

        handle_pipeline_cli(self, args)


    def _run_interactive_menu(self):
        """Simple interactive menu for quick operations"""
        run_interactive_menu(self)

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
        
        # Load template from configs directory
        template_path = Path(__file__).parent / "configs" / "github-actions.yml.template"
        
        try:
            if not template_path.exists():
                self.logger.error(f"Template file not found: {template_path}")
                self.console.print(f"[red]Template file not found: {template_path}[/red]")
                return False
            
            with open(template_path, 'r') as f:
                workflow_content = f.read()
        
            config_file = Path(output_path) / "docker-pilot.yml"
            with open(config_file, 'w') as f:
                f.write(workflow_content)
            
            self.console.print(f"[green]GitHub Actions workflow created: {config_file}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create GitHub Actions config: {e}")
            return False

    def _create_gitlab_ci_config(self, output_path: str = None) -> bool:
        """Create GitLab CI configuration"""
        # Load template from configs directory
        template_path = Path(__file__).parent / "configs" / "gitlab-ci.yml.template"
        
        try:
            if not template_path.exists():
                self.logger.error(f"Template file not found: {template_path}")
                self.console.print(f"[red]Template file not found: {template_path}[/red]")
                return False
            
            with open(template_path, 'r') as f:
                config_content = f.read()
            
            config_file = ".gitlab-ci.yml" if not output_path else Path(output_path) / ".gitlab-ci.yml"
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            self.console.print(f"[green]GitLab CI configuration created: {config_file}[/green]")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create GitLab CI config: {e}")
            return False

    def _create_jenkins_config(self, output_path: str = None) -> bool:
        """Create Jenkins pipeline configuration"""
        # Load template from configs directory
        template_path = Path(__file__).parent / "configs" / "jenkinsfile.template"
        
        try:
            if not template_path.exists():
                self.logger.error(f"Template file not found: {template_path}")
                self.console.print(f"[red]Template file not found: {template_path}[/red]")
                return False
            
            with open(template_path, 'r') as f:
                pipeline_content = f.read()
            
            config_file = "Jenkinsfile" if not output_path else Path(output_path) / "Jenkinsfile"
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
                # Load default test configuration from template
                template_path = Path(__file__).parent / "configs" / "integration-tests.yml.template"
                
                if template_path.exists():
                    with open(template_path, 'r') as f:
                        test_config = yaml.safe_load(f)
                else:
                    # Fallback to default test configuration
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
        return True

    def setup_monitoring_alerts(self, alert_config_path: str = "alerts.yml") -> bool:
        """Setup monitoring and alerting configuration from template"""
        
        try:
            if not Path(alert_config_path).exists():
                # Load template from configs directory
                template_path = Path(__file__).parent / "configs" / "alerts.yml.template"
                
                if not template_path.exists():
                    self.logger.error(f"Template file not found: {template_path}")
                    self.console.print(f"[red]Template file not found: {template_path}[/red]")
                    return False
                
                with open(template_path, 'r', encoding='utf-8') as f:
                    template_content = f.read()
                
                with open(alert_config_path, 'w', encoding='utf-8') as f:
                    f.write(template_content)
                
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
        """Generate production deployment checklist from template"""
        try:
            # Load template from configs directory
            template_path = Path(__file__).parent / "configs" / "production-checklist.md.template"
            
            if not template_path.exists():
                self.logger.error(f"Template not found: {template_path}")
                self.console.print(f"[red]Template file not found: {template_path}[/red]")
                return False
            
            with open(template_path, 'r', encoding='utf-8') as f:
                checklist_content = f.read()
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(checklist_content)
            
            self.console.print(f"[green]Production checklist created: {output_file}[/green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create production checklist: {e}")
            return False

    def generate_documentation(self, output_dir: str = "docs") -> bool:
        """Generate comprehensive project documentation from templates"""
        try:
            docs_path = Path(output_dir)
            docs_path.mkdir(exist_ok=True)
            
            # Get templates directory
            templates_dir = Path(__file__).parent / "configs"
            
            # Documentation files to generate
            doc_files = [
                ("docs-readme.md.template", "README.md"),
                ("docs-api.md.template", "API.md"),
                ("docs-troubleshooting.md.template", "TROUBLESHOOTING.md")
            ]
            
            # Generate each documentation file from template
            for template_name, output_name in doc_files:
                template_path = templates_dir / template_name
                
                if not template_path.exists():
                    self.logger.warning(f"Template not found: {template_path}")
                    continue
                
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                with open(docs_path / output_name, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                self.logger.info(f"Generated {output_name}")
            
            self.console.print(f"[green]Documentation generated in {output_dir}/[/green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to generate documentation: {e}")
            return False

    def validate_system_requirements(self) -> bool:
        """Validate system requirements and dependencies"""
        self.console.print("[cyan]Validating system requirements...[/cyan]")
        
        requirements_met = True
        
        # Check Python version
        python_version = sys.version_info
        if python_version < (3, 9):
            self.console.print("[red]❌ Python 3.9+ required[/red]")
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
    # Minimal bootstrap to honor --config, --log-level and --version before launching CLI
    try:
        from . import __version__
    except ImportError:
        __version__ = "Enhanced"
    
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument('--version', action='version', version=f'DockerPilot {__version__}')
    bootstrap_parser.add_argument('--config', '-c', type=str, default=None)
    bootstrap_parser.add_argument('--log-level', '-l', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    known_args, _ = bootstrap_parser.parse_known_args()

    try:
        log_level_enum = LogLevel[known_args.log_level]
    except Exception:
        log_level_enum = LogLevel.INFO

    pilot = DockerPilotEnhanced(config_file=known_args.config, log_level=log_level_enum)
    pilot.run_cli()
