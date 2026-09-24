#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import argparse
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Optional

from rich.console import Console

# Import modules
from .models import LogLevel, ContainerStats
from .container_manager import ContainerManager
from .image_manager import ImageManager
from .monitoring import MonitoringManager
from .cli import build_cli_parser, run_cli as run_pilot_cli, run_interactive_menu, run_container_interactive
from .backup_restore import BackupRestoreMixin
from .deployment_service import DeploymentServiceMixin
from .services.templates import create_production_checklist as create_production_checklist_from_template
from .services.templates import generate_documentation as generate_documentation_from_templates
from .services.pipeline import integrate_with_git as integrate_with_git_service
from .services.pipeline import _create_github_actions_config as create_github_actions_config_service
from .services.pipeline import _create_gitlab_ci_config as create_gitlab_ci_config_service
from .services.pipeline import _create_jenkins_config as create_jenkins_config_service
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
from .services.runtime_support import (
    check_cancel_flag as check_cancel_flag_service,
    load_health_check_defaults as load_health_check_defaults_service,
    get_database_config as get_database_config_service,
    get_database_name as get_database_name_service,
    update_progress as update_progress_service,
    show_loading as show_loading_service,
    loading_context,
    error_context,
    parse_multi_target as parse_multi_target_service,
)
from .deployment_history import show_deployment_history as show_deployment_history_service
from .services.bootstrap import (
    configure_console_streams as configure_console_streams_service,
    show_banner as show_banner_service,
    setup_logging as setup_logging_service,
    load_config as load_config_service,
    initialize_docker_client as initialize_docker_client_service,
)

class DockerPilotEnhanced(DeploymentServiceMixin, BackupRestoreMixin):
    """Enhanced Docker container management tool with advanced deployment capabilities."""
    
    def __init__(
        self,
        config_file: str = None,
        log_level: LogLevel = LogLevel.INFO,
        register_signal_handlers: bool = True,
    ):
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
        
        # CLI processes own their signal handlers. Embedded/threaded callers
        # can opt out without process-global monkeypatching.
        if register_signal_handlers and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Docker Pilot Enhanced initialized successfully")
    
    def _show_banner(self):
        """Display ASCII banner with application information."""
        return show_banner_service(self.console)

    def _configure_console_streams(self):
        """Improve Windows console compatibility for Unicode-rich output."""
        return configure_console_streams_service()
    
    def _parse_multi_target(self, target_string: str) -> List[str]:
        """Parse comma-separated container/image targets."""
        return parse_multi_target_service(target_string)

    def _setup_logging(self, level: LogLevel):
        """Setup enhanced logging with rotation."""
        self.logger = setup_logging_service(self.log_file, level)

    def _load_config(self, config_file: str):
        """Load configuration from YAML file."""
        self.config = load_config_service(self.logger, config_file)
    
    def _check_cancel_flag(self, container_name: str = None) -> bool:
        """Check if deployment should be cancelled."""
        target = container_name or self._current_deployment_container
        return check_cancel_flag_service(self.logger, target)
    
    def _load_health_check_defaults(self) -> dict:
        """Load and cache default health-check configuration."""
        if self._health_check_defaults is None:
            self._health_check_defaults = load_health_check_defaults_service(self.logger)
        return self._health_check_defaults
    
    def _get_database_config(self, image_tag: str) -> dict:
        """Get database-specific configuration based on image tag."""
        return get_database_config_service(self._load_health_check_defaults(), image_tag, self.logger)
    
    def _get_database_name(self, image_tag: str) -> str:
        """Get database service name from image tag."""
        return get_database_name_service(self._load_health_check_defaults(), image_tag)
    
    def _is_database_service(self, image_tag: str) -> bool:
        """Check if image tag represents a database service."""
        return bool(self._get_database_config(image_tag))
    
    def _init_docker_client(self, max_retries: int = 3):
        """Initialize Docker client with retry logic."""
        self.client = initialize_docker_client_service(self.console, self.logger, max_retries)
        return self.client is not None
    
    def _update_progress(self, stage: str, progress: int, message: str):
        """Update progress if a callback is available."""
        return update_progress_service(self._progress_callback, self.logger, stage, progress, message)
    
    def _show_loading(self, message: str = "Processing", stop_event=None):
        """Show animated loading dots while an operation is in progress."""
        return show_loading_service(message, stop_event)
    
    @contextmanager
    def _with_loading(self, message: str = "Processing"):
        """Compatibility context manager for the loading indicator."""
        with loading_context(message, loader=self._show_loading):
            yield

    def _signal_handler(self, signum, frame):
        """Graceful shutdown handler"""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.console.print("\n[yellow]⚠️ Graceful shutdown initiated...[/yellow]")
        sys.exit(0)

    @contextmanager
    def _error_handler(self, operation: str, container_name: str = None):
        """Compatibility context manager for shared operation error handling."""
        with error_context(self.console, self.logger, operation, container_name):
            yield

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
        normalized = pipeline_type.lower()
        if normalized == "github":
            return self._create_github_actions_config(output_path)
        if normalized == "gitlab":
            return self._create_gitlab_ci_config(output_path)
        if normalized == "jenkins":
            return self._create_jenkins_config(output_path)
        self.console.print(f"[red]Unsupported pipeline type: {pipeline_type}[/red]")
        return False

    def _create_github_actions_config(self, output_path: str = None) -> bool:
        """Compatibility delegate for GitHub Actions pipeline generation."""
        return create_github_actions_config_service(self.console, self.logger, output_path)

    def _create_gitlab_ci_config(self, output_path: str = None) -> bool:
        """Compatibility delegate for GitLab CI pipeline generation."""
        return create_gitlab_ci_config_service(self.console, self.logger, output_path)

    def _create_jenkins_config(self, output_path: str = None) -> bool:
        """Compatibility delegate for Jenkins pipeline generation."""
        return create_jenkins_config_service(self.console, self.logger, output_path)

    def run_integration_tests(self, test_config_path: str = "integration-tests.yml") -> bool:
        """Run comprehensive integration tests."""
        return run_integration_tests_service(
            self.console, self.logger, test_config_path,
            run_single=self._run_single_integration_test,
            generate_report=self._generate_test_report,
        )

    def _run_single_integration_test(self, test_config: dict) -> dict:
        """Run a single integration test."""
        return run_single_integration_test_service(
            test_config,
            run_http=self._run_http_test,
            run_database=self._run_database_test,
            run_custom=self._run_custom_test,
        )

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
        return generate_test_report_service(
            self.console, self.logger, test_results, save_report=self._save_test_report
        )

    def _save_test_report(self, test_results: List[dict], passed: int, failed: int):
        """Save test report to file."""
        return save_test_report_service(self.logger, test_results, passed, failed)

    def setup_monitoring_alerts(self, alert_config_path: str = "alerts.yml") -> bool:
        """Setup monitoring and alerting configuration from template."""
        result = self.alert_service.setup_monitoring_alerts(
            alert_config_path, initialize=self._initialize_alert_monitoring
        )
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
        return self.alert_service.check_alerts(
            container_stats, container_name, trigger=self._trigger_alert
        )

    def _trigger_alert(self, rule: dict, container_name: str, details: str):
        """Trigger an alert notification."""
        self.alert_service.notification_channels = getattr(self, "notification_channels", [])
        return self.alert_service.trigger_alert(
            rule, container_name, details, send_notification=self._send_notification
        )

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
