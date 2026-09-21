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
from .cli import build_cli_parser, run_cli as run_pilot_cli, run_interactive_menu, run_container_interactive
from .backup_restore import BackupRestoreMixin
from .deployment_service import DeploymentServiceMixin
from .services.templates import create_production_checklist as create_production_checklist_from_template
from .services.templates import generate_documentation as generate_documentation_from_templates
from .services.pipeline import integrate_with_git as integrate_with_git_service
from .services.pipeline import create_pipeline_config as create_pipeline_config_service
from .services.configuration_archive import export_configuration as export_configuration_service
from .services.configuration_archive import import_configuration as import_configuration_service
from .services.system_validation import validate_system_requirements as validate_system_requirements_service
from .services.integration_testing import (
    run_integration_tests as run_integration_tests_service,
    run_single_integration_test as run_single_integration_test_service,
    run_http_test as run_http_test_service,
    run_database_test as run_database_test_service,
    run_custom_test as run_custom_test_service,
    generate_test_report as generate_test_report_service,
    save_test_report as save_test_report_service,
)
from .services.alerts import AlertService
from .services.health_checks import health_check_standalone as health_check_standalone_service
from .deployment_history import show_deployment_history as show_deployment_history_service

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
        self.alert_service = AlertService(self.console, self.logger)
        
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
                self.client, self.console, self.logger, self.metrics_file, self._error_handler
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
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.exec_container(container_name, command)

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
        """Get one-time container statistics snapshot."""
        if not self.monitoring_manager:
            self.logger.error("Monitoring manager not initialized - Docker client not available")
            return False
        return self.monitoring_manager.get_container_stats_once(container_name)
    
    def monitor_container_live(self, container_name: str, duration: int = 30) -> bool:
        """Live container monitoring."""
        if not self.monitoring_manager:
            self.logger.error("Monitoring manager not initialized - Docker client not available")
            return False
        return self.monitoring_manager.monitor_container_live(container_name, duration)
    
    def stop_and_remove_container(self, container_name: str, timeout: int = 10) -> bool:
        """Stop and remove a container in one operation."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.stop_and_remove_container(container_name, timeout)
    
    def exec_command_non_interactive(self, container_name: str, command: str) -> bool:
        """Execute a command in a container non-interactively."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return False
        return self.container_manager.exec_command_non_interactive(container_name, command)
    
    def health_check_standalone(self, port: int, endpoint: str = "/health", timeout: int = 30, max_retries: int = 10) -> bool:
        """Run the standalone HTTP health check."""
        return health_check_standalone_service(self.console, port, endpoint, timeout, max_retries)

    # ==================== ADVANCED DEPLOYMENT ====================
    def show_deployment_history(self, limit: int = 10):
        """Show deployment history."""
        return show_deployment_history_service(self.console, self.logger, limit)

    # ==================== CLI INTERFACE ====================

    def create_cli_parser(self) -> argparse.ArgumentParser:
        """Create comprehensive CLI parser"""
        return build_cli_parser()

    def run_cli(self):
        """Run CLI interface"""
        run_pilot_cli(self)

    def _run_container_interactive(self, args):
        """Compatibility delegate for the interactive container-run flow."""
        return run_container_interactive(self, args)
    
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
        """Integrate with Git for automated deployments."""
        return integrate_with_git_service(self.console, self.logger, repo_path)

    def create_pipeline_config(self, pipeline_type: str = "github", output_path: str = None) -> bool:
        """Generate CI/CD pipeline configuration files."""
        return create_pipeline_config_service(
            self.console,
            self.logger,
            pipeline_type,
            output_path,
        )

    def run_integration_tests(self, test_config_path: str = "integration-tests.yml") -> bool:
        """Run comprehensive integration tests."""
        return run_integration_tests_service(self.console, self.logger, test_config_path)

    def _run_single_integration_test(self, test_config: dict) -> dict:
        """Run a single integration test."""
        return run_single_integration_test_service(test_config)

    def _run_http_test(self, test_config: dict, start_time: float) -> dict:
        """Run HTTP-based integration test."""
        return run_http_test_service(test_config, start_time)

    def _run_database_test(self, test_config: dict, start_time: float) -> dict:
        """Run database connectivity test."""
        return run_database_test_service(test_config, start_time)

    def _run_custom_test(self, test_config: dict, start_time: float) -> dict:
        """Run custom test script."""
        return run_custom_test_service(test_config, start_time)

    def _generate_test_report(self, test_results: List[dict]):
        """Generate comprehensive test report."""
        return generate_test_report_service(self.console, self.logger, test_results)

    def _save_test_report(self, test_results: List[dict], passed: int, failed: int):
        """Save test report to file."""
        return save_test_report_service(self.logger, test_results, passed, failed)

    def setup_monitoring_alerts(self, alert_config_path: str = "alerts.yml") -> bool:
        """Setup monitoring and alerting configuration from template."""
        result = self.alert_service.setup_monitoring_alerts(alert_config_path)
        self.alert_rules = self.alert_service.alert_rules
        self.notification_channels = self.alert_service.notification_channels
        return result

    def _initialize_alert_monitoring(self, alert_config_path: str) -> bool:
        """Initialize alert monitoring system."""
        result = self.alert_service.initialize_alert_monitoring(alert_config_path)
        self.alert_rules = self.alert_service.alert_rules
        self.notification_channels = self.alert_service.notification_channels
        return result

    def check_alerts(self, container_stats: ContainerStats, container_name: str):
        """Check if any alerts should be triggered."""
        if not hasattr(self, "alert_rules"):
            return
        self.alert_service.alert_rules = self.alert_rules
        self.alert_service.notification_channels = getattr(self, "notification_channels", [])
        return self.alert_service.check_alerts(container_stats, container_name)

    def _trigger_alert(self, rule: dict, container_name: str, details: str):
        """Trigger an alert notification."""
        self.alert_service.notification_channels = getattr(self, "notification_channels", [])
        return self.alert_service.trigger_alert(rule, container_name, details)

    def _send_notification(self, channel: dict, message: str):
        """Send notification through configured channel."""
        return self.alert_service.send_notification(channel, message)

    def create_production_checklist(self, output_file: str = "production-checklist.md") -> bool:
        """Generate production deployment checklist from template."""
        return create_production_checklist_from_template(
            self.console,
            self.logger,
            output_file,
        )

    def generate_documentation(self, output_dir: str = "docs") -> bool:
        """Generate comprehensive project documentation from templates."""
        return generate_documentation_from_templates(
            self.console,
            self.logger,
            output_dir,
        )

    def validate_system_requirements(self) -> bool:
        """Validate system requirements and dependencies."""
        return validate_system_requirements_service(self.console, self.client)

    def export_configuration(self, config_name: str = "docker-pilot-config.tar.gz") -> bool:
        """Export all configuration files as a backup."""
        return export_configuration_service(self.console, self.logger, config_name)

    def import_configuration(self, config_archive: str) -> bool:
        """Import configuration from backup archive."""
        return import_configuration_service(self.console, self.logger, config_archive)
        

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
