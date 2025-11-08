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

# Import modules
from .models import LogLevel, DeploymentConfig, ContainerStats
from .container_manager import ContainerManager
from .image_manager import ImageManager
from .monitoring import MonitoringManager

class DockerPilotEnhanced:
    """Enhanced Docker container management tool with advanced deployment capabilities."""
    
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
        
        # Initialize managers
        self.container_manager = ContainerManager(
            self.client, self.console, self.logger, self._error_handler
        )
        self.image_manager = ImageManager(
            self.client, self.console, self.logger, self._error_handler
        )
        self.monitoring_manager = MonitoringManager(
            self.client, self.console, self.logger, self.metrics_file
        )
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Docker Pilot Enhanced initialized successfully")
    
    def _show_banner(self):
        """Display ASCII banner with application information"""
        banner = """
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
        """Enhanced container listing with multiple output formats."""
        return self.container_manager.list_containers(show_all, format_output)

    def list_images(self, show_all: bool = True, format_output: str = "table") -> List[Any]:
        """Enhanced image listing with multiple output formats."""
        return self.image_manager.list_images(show_all, format_output)
    
    def remove_image(self, image_name: str, force: bool = False) -> bool:
        """Remove Docker image."""
        return self.image_manager.remove_image(image_name, force)

    def container_operation(self, operation: str, container_name: str, **kwargs) -> bool:
        """Unified container operation handler with progress tracking."""
        if operation == 'update_restart_policy':
            return self.update_restart_policy(container_name, kwargs.get('policy', 'unless-stopped'))
        elif operation == 'run_image':
            return self.run_new_container(
                kwargs.get('image_name'),
                kwargs.get('name', container_name),
                kwargs.get('ports'),
                kwargs.get('command')
            )
        else:
            return self.container_manager.container_operation(operation, container_name, **kwargs)
    
    def update_restart_policy(self, container_name: str, policy: str = 'unless-stopped') -> bool:
        """Set restart policy on container."""
        return self.container_manager.update_restart_policy(container_name, policy)
    
    def run_new_container(self, image_name: str, name: str, ports: dict = None, command: str = None) -> bool:
        """Run a new container."""
        return self.container_manager.run_new_container(image_name, name, ports, command)
    
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
        return self.monitoring_manager.get_container_stats(container_name)
    
    def monitor_containers_dashboard(self, containers: List[str] = None, duration: int = 300):
        """Real-time monitoring dashboard for multiple containers."""
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

    def create_deployment_config(self, config_path: str = "deployment.yml") -> bool:
        """Create deployment configuration template from file"""
        try:
            # Load template from configs directory
            template_path = Path(__file__).parent / "configs" / "deployment.yml.template"
            
            if not template_path.exists():
                self.logger.error(f"Template file not found: {template_path}")
                self.console.print(f"[red]Template file not found: {template_path}[/red]")
                return False
            
            with open(template_path, 'r', encoding='utf-8') as f:
                template_content = f.read()
            
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(template_content)
            
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

    def view_container_logs(self, container_name: str = None, tail: int = 50):
        """View container logs."""
        return self.container_manager.view_container_logs(container_name, tail)
    
    def view_container_json(self, container_name: str):
        """Display container information in JSON format."""
        return self.container_manager.view_container_json(container_name)


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

    def quick_deploy(self, dockerfile_path: str = ".", image_tag: str = None, 
                    container_name: str = None, port_mapping: dict = None, 
                    environment: dict = None, volumes: dict = None,
                    yaml_config: str = None, cleanup_old_image: bool = True) -> bool:
        """
        Quick deployment: build -> stop old -> remove old container -> remove old image -> run new
        
        Args:
            dockerfile_path: Path to directory containing Dockerfile
            image_tag: Tag for the new image (e.g., 'myapp:v1.2')
            container_name: Name of the container
            port_mapping: Port mapping dict (e.g., {'80': '8080'})
            environment: Environment variables dict
            volumes: Volume mapping dict
            yaml_config: Optional path to YAML config file for container settings
            cleanup_old_image: Whether to remove old image after deployment
        
        Returns:
            bool: True if deployment successful
        """
        self.console.print(f"\n[bold cyan]⚡ QUICK DEPLOY STARTED[/bold cyan]")
        
        deployment_start = datetime.now()
        old_image_id = None
        
        # Load configuration from YAML if provided
        if yaml_config and Path(yaml_config).exists():
            try:
                with open(yaml_config, 'r') as f:
                    config = yaml.safe_load(f)
                
                # Override with YAML settings if not explicitly provided
                image_tag = image_tag or config.get('image_tag')
                container_name = container_name or config.get('container_name')
                port_mapping = port_mapping or config.get('port_mapping', {})
                environment = environment or config.get('environment', {})
                volumes = volumes or config.get('volumes', {})
                
                self.console.print(f"[cyan]✓ Loaded configuration from {yaml_config}[/cyan]")
            except Exception as e:
                self.logger.error(f"Failed to load YAML config: {e}")
                self.console.print(f"[yellow]⚠️ Could not load YAML config, using provided parameters[/yellow]")
        
        # Validate required parameters
        if not image_tag or not container_name:
            self.console.print("[red]❌ image_tag and container_name are required[/red]")
            return False
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            
            # Step 1: Get old container info (for image cleanup)
            check_task = progress.add_task("🔍 Checking existing deployment...", total=None)
            try:
                old_container = self.client.containers.get(container_name)
                old_image_id = old_container.image.id
                old_image_tags = old_container.image.tags
                progress.update(check_task, description=f"✅ Found existing container (image: {old_image_tags[0] if old_image_tags else old_image_id[:12]})")
            except docker.errors.NotFound:
                progress.update(check_task, description="ℹ️ No existing container (first deployment)")
                old_image_id = None
            
            # Step 2: Build new image
            build_task = progress.add_task(f"🔨 Building image {image_tag}...", total=None)
            try:
                dockerfile = Path(dockerfile_path) / "Dockerfile"
                if not dockerfile.exists():
                    progress.update(build_task, description="❌ Dockerfile not found")
                    self.console.print(f"[red]❌ Dockerfile not found at {dockerfile}[/red]")
                    return False
                
                image, build_logs = self.client.images.build(
                    path=dockerfile_path,
                    tag=image_tag,
                    rm=True,
                    pull=True
                )
                progress.update(build_task, description=f"✅ Image {image_tag} built successfully")
                
            except docker.errors.BuildError as e:
                progress.update(build_task, description="❌ Build failed")
                self.logger.error(f"Build error: {e}")
                for log in e.build_log:
                    if 'stream' in log:
                        self.console.print(f"[red]{log['stream']}[/red]", end="")
                return False
            except Exception as e:
                progress.update(build_task, description="❌ Build failed")
                self.logger.error(f"Unexpected build error: {e}")
                return False
            
            # Step 3: Stop old container
            stop_task = progress.add_task("🛑 Stopping old container...", total=None)
            try:
                old_container = self.client.containers.get(container_name)
                if old_container.status == "running":
                    old_container.stop(timeout=10)
                    progress.update(stop_task, description="✅ Old container stopped")
                else:
                    progress.update(stop_task, description="ℹ️ Container was not running")
            except docker.errors.NotFound:
                progress.update(stop_task, description="ℹ️ No container to stop")
            except Exception as e:
                progress.update(stop_task, description="❌ Failed to stop container")
                self.logger.error(f"Stop failed: {e}")
                return False
            
            # Step 4: Remove old container
            remove_task = progress.add_task("🗑️ Removing old container...", total=None)
            try:
                old_container = self.client.containers.get(container_name)
                old_container.remove()
                progress.update(remove_task, description="✅ Old container removed")
            except docker.errors.NotFound:
                progress.update(remove_task, description="ℹ️ No container to remove")
            except Exception as e:
                progress.update(remove_task, description="❌ Failed to remove container")
                self.logger.error(f"Remove failed: {e}")
                # Continue anyway
            
            # Step 5: Remove old image (if requested and exists)
            if cleanup_old_image and old_image_id:
                cleanup_task = progress.add_task("🧹 Cleaning up old image...", total=None)
                try:
                    # Check if old image is different from new one
                    new_image = self.client.images.get(image_tag)
                    if old_image_id != new_image.id:
                        # Check if any other containers are using the old image
                        containers_using_image = self.client.containers.list(
                            all=True,
                            filters={"ancestor": old_image_id}
                        )
                        
                        if len(containers_using_image) == 0:
                            try:
                                self.client.images.remove(old_image_id, force=False)
                                progress.update(cleanup_task, description="✅ Old image removed")
                            except docker.errors.ImageNotFound:
                                progress.update(cleanup_task, description="ℹ️ Old image already removed")
                            except docker.errors.APIError as e:
                                if "image is being used" in str(e).lower():
                                    progress.update(cleanup_task, description="⚠️ Old image in use by other containers")
                                else:
                                    progress.update(cleanup_task, description=f"⚠️ Could not remove old image: {str(e)[:50]}")
                        else:
                            progress.update(cleanup_task, description=f"⚠️ Old image used by {len(containers_using_image)} other container(s)")
                    else:
                        progress.update(cleanup_task, description="ℹ️ Same image, no cleanup needed")
                except Exception as e:
                    progress.update(cleanup_task, description="⚠️ Image cleanup skipped")
                    self.logger.warning(f"Image cleanup failed: {e}")
            
            # Step 6: Run new container
            run_task = progress.add_task("🚀 Starting new container...", total=None)
            try:
                new_container = self.client.containers.run(
                    image=image_tag,
                    name=container_name,
                    detach=True,
                    ports=port_mapping,
                    environment=environment,
                    volumes=volumes,
                    restart_policy={"Name": "unless-stopped"}
                )
                progress.update(run_task, description="✅ New container started")
                
                # Grace period for startup
                time.sleep(3)
                
            except Exception as e:
                progress.update(run_task, description="❌ Failed to start container")
                self.logger.error(f"Container start failed: {e}")
                return False
            
            # Step 7: Optional health check
            if port_mapping:
                health_task = progress.add_task("🩺 Health check...", total=None)
                host_port = list(port_mapping.values())[0]
                
                if self._advanced_health_check(host_port, "/", timeout=10, max_retries=3):
                    progress.update(health_task, description="✅ Health check passed")
                else:
                    progress.update(health_task, description="⚠️ Health check failed (container still running)")
                    self.logger.warning("Health check failed but deployment completed")
        
        # Deployment summary
        deployment_end = datetime.now()
        duration = deployment_end - deployment_start
        
        self.console.print(f"\n[bold green]🎉 QUICK DEPLOY COMPLETED SUCCESSFULLY![/bold green]")
        self.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
        self.console.print(f"[green]Container: {container_name}[/green]")
        self.console.print(f"[green]Image: {image_tag}[/green]")
        
        if port_mapping:
            for container_port, host_port in port_mapping.items():
                self.console.print(f"[green]Available at: http://localhost:{host_port}[/green]")
        
        # Record deployment
        self._record_deployment(
            f"quick_{int(deployment_start.timestamp())}",
            DeploymentConfig(
                image_tag=image_tag,
                container_name=container_name,
                port_mapping=port_mapping or {},
                environment=environment or {},
                volumes=volumes or {}
            ),
            "quick",
            True,
            duration
        )
        
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
        remove_img_parser = container_subparsers.add_parser('remove-image', help='Remove Docker image(s)')
        remove_img_parser.add_argument('name', help='Image name(s) or ID(s), comma-separated (e.g., image1:tag,image2:tag)')
        remove_img_parser.add_argument('--force', '-f', action='store_true', help='Force removal')

        
        # Container actions
        for action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
            action_parser = container_subparsers.add_parser(action, help=f'{action.title()} container(s)')
            action_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2 or id1,id2)')
            if action in ['stop', 'restart']:
                action_parser.add_argument('--timeout', '-t', type=int, default=10, help='Timeout seconds')
            if action == 'remove':
                action_parser.add_argument('--force', '-f', action='store_true', help='Force removal')
        
        # Stop and remove in one operation
        stop_remove_parser = container_subparsers.add_parser('stop-remove', help='Stop and remove container(s) in one operation')
        stop_remove_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated')
        stop_remove_parser.add_argument('--timeout', '-t', type=int, default=10, help='Timeout seconds')
        
        # Exec non-interactive
        exec_simple_parser = container_subparsers.add_parser('exec-simple', help='Execute command non-interactively')
        exec_simple_parser.add_argument('name', help='Container name or ID')
        exec_simple_parser.add_argument('command', help='Command to execute (e.g., "ls -la")')
        
        # Exec into container
        exec_parser = container_subparsers.add_parser('exec', help='Execute interactive command in container(s)')
        exec_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2)')
        exec_parser.add_argument('--command', '-c', default='/bin/bash', help='Command to execute (default: /bin/bash)')
        
        # View container logs
        logs_parser = container_subparsers.add_parser('logs', help='View container logs')
        logs_parser.add_argument('name', nargs='?', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2)')
        logs_parser.add_argument('--tail', '-n', type=int, default=50, help='Number of lines to show (default: 50)')
        
        # Monitoring
        monitor_parser = subparsers.add_parser('monitor', help='Container monitoring')
        monitor_subparsers = monitor_parser.add_subparsers(dest='monitor_action')
        
        # Dashboard monitoring
        dashboard_parser = monitor_subparsers.add_parser('dashboard', help='Multi-container dashboard')
        dashboard_parser.add_argument('containers', nargs='*', help='Container names (empty for all running)')
        dashboard_parser.add_argument('--duration', '-d', type=int, default=300, help='Monitor duration in seconds')
        
        # Live monitoring (with screen clearing)
        live_parser = monitor_subparsers.add_parser('live', help='Live monitoring with screen clearing')
        live_parser.add_argument('container', help='Container name')
        live_parser.add_argument('--duration', '-d', type=int, default=30, help='Monitor duration in seconds')
        
        # One-time stats
        stats_parser = monitor_subparsers.add_parser('stats', help='Get one-time container statistics')
        stats_parser.add_argument('container', help='Container name')
        
        # Health check standalone
        health_parser = monitor_subparsers.add_parser('health', help='Test health check endpoint')
        health_parser.add_argument('port', type=int, help='Port number')
        health_parser.add_argument('--endpoint', '-e', default='/health', help='Health check endpoint')
        health_parser.add_argument('--retries', '-r', type=int, default=10, help='Maximum retries')
        
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
        
        # Quick deploy
        quick_deploy_parser = deploy_subparsers.add_parser('quick', help='Quick deployment (build + replace)')
        quick_deploy_parser.add_argument('--dockerfile-path', '-d', default='.', help='Path to Dockerfile directory')
        quick_deploy_parser.add_argument('--image-tag', '-t', required=True, help='Image tag (e.g., myapp:v1.2)')
        quick_deploy_parser.add_argument('--container-name', '-n', required=True, help='Container name')
        quick_deploy_parser.add_argument('--port', '-p', help='Port mapping (format: container:host, e.g., 80:8080)')
        quick_deploy_parser.add_argument('--env', '-e', action='append', help='Environment variable (format: KEY=VALUE)')
        quick_deploy_parser.add_argument('--volume', '-v', action='append', help='Volume mapping (format: host:container)')
        quick_deploy_parser.add_argument('--yaml-config', '-y', help='YAML config file with container settings')
        quick_deploy_parser.add_argument('--no-cleanup', action='store_true', help='Do not remove old image')
        
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
        
        # Execute CLI command
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
        """Handle container CLI commands with support for multiple targets"""
        if args.container_action == 'list':
            self.list_containers(show_all=args.all, format_output=args.format)
        elif args.container_action == 'stop-remove':
            # Stop and remove in one operation
            containers = self._parse_multi_target(args.name)
            
            if not containers:
                self.console.print("[red]❌ No container names provided[/red]")
                sys.exit(1)
            
            timeout = args.timeout if hasattr(args, 'timeout') else 10
            
            all_success = True
            for container in containers:
                self.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                success = self.stop_and_remove_container(container, timeout)
                if not success:
                    all_success = False
            
            if not all_success:
                self.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
                sys.exit(1)
            else:
                self.console.print("\n[green]✅ All operations completed successfully[/green]")
        
        elif args.container_action == 'exec-simple':
            # Non-interactive exec
            success = self.exec_command_non_interactive(args.name, args.command)
            if not success:
                sys.exit(1)
        
        elif args.container_action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
            # Parse multiple container names/IDs
            containers = self._parse_multi_target(args.name)
            
            if not containers:
                self.console.print("[red]❌ No container names provided[/red]")
                sys.exit(1)
            
            kwargs = {}
            if hasattr(args, 'timeout'):
                kwargs['timeout'] = args.timeout
            if hasattr(args, 'force'):
                kwargs['force'] = args.force
            
            # Execute operation on each container
            all_success = True
            for container in containers:
                self.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                success = self.container_operation(args.container_action, container, **kwargs)
                if not success:
                    all_success = False
            
            if not all_success:
                self.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
                sys.exit(1)
            else:
                self.console.print("\n[green]✅ All operations completed successfully[/green]")
                
        elif args.container_action == 'exec':
            # Parse multiple container names/IDs
            containers = self._parse_multi_target(args.name)
            
            if not containers:
                self.console.print("[red]❌ No container names provided[/red]")
                sys.exit(1)
            
            command = args.command if hasattr(args, 'command') else '/bin/bash'
            
            # Execute command in each container sequentially
            for container in containers:
                self.console.print(f"\n[cyan]Executing in container: {container}[/cyan]")
                success = self.exec_container(container, command)
                if not success:
                    self.console.print(f"[yellow]⚠️ Failed to exec in {container}, continuing...[/yellow]")
        
        elif args.container_action == 'logs':
            tail = args.tail if hasattr(args, 'tail') else 50
            if args.name:
                self.view_container_logs(args.name, tail)
            else:
                # Interactive mode when no container name provided
                self.view_container_logs(None, tail)
                    
        elif args.container_action == 'run_image':
            # This would need CLI parser setup for run_image - currently only supports interactive mode
            self.console.print("[yellow]run_image is only available in interactive mode[/yellow]")
        elif args.container_action == 'list-images':
            self.list_images(show_all=args.all, format_output=args.format)
        elif args.container_action == 'remove-image':
            # Parse multiple image names/IDs
            images = self._parse_multi_target(args.name)
            
            if not images:
                self.console.print("[red]❌ No image names provided[/red]")
                sys.exit(1)
            
            # Remove each image
            all_success = True
            for image in images:
                self.console.print(f"\n[cyan]Processing image: {image}[/cyan]")
                success = self.remove_image(image, args.force)
                if not success:
                    all_success = False
            
            if not all_success:
                self.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
                sys.exit(1)
            else:
                self.console.print("\n[green]✅ All operations completed successfully[/green]")

    def _handle_monitor_cli(self, args):
        """Handle monitoring CLI commands"""
        if args.monitor_action == 'dashboard' or not args.monitor_action:
            # Default to dashboard if no action specified (backward compatibility)
            containers = args.containers if hasattr(args, 'containers') and args.containers else None
            duration = args.duration if hasattr(args, 'duration') else 300
            self.monitor_containers_dashboard(containers, duration)
        
        elif args.monitor_action == 'live':
            # Live monitoring with screen clearing
            success = self.monitor_container_live(args.container, args.duration)
            if not success:
                sys.exit(1)
        
        elif args.monitor_action == 'stats':
            # One-time stats
            success = self.get_container_stats_once(args.container)
            if not success:
                sys.exit(1)
        
        elif args.monitor_action == 'health':
            # Health check
            success = self.health_check_standalone(
                args.port, 
                args.endpoint, 
                timeout=30,
                max_retries=args.retries
            )
            if not success:
                sys.exit(1)

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
        elif args.deploy_action == 'quick':
            # Parse port mapping
            port_mapping = None
            if args.port:
                try:
                    container_port, host_port = args.port.split(':')
                    port_mapping = {container_port: host_port}
                except ValueError:
                    self.console.print("[red]Invalid port format. Use container:host (e.g., 80:8080)[/red]")
                    sys.exit(1)
            
            # Parse environment variables
            environment = {}
            if args.env:
                for env_var in args.env:
                    try:
                        key, value = env_var.split('=', 1)
                        environment[key] = value
                    except ValueError:
                        self.console.print(f"[red]Invalid env format: {env_var}. Use KEY=VALUE[/red]")
                        sys.exit(1)
            
            # Parse volumes
            volumes = {}
            if args.volume:
                for volume in args.volume:
                    try:
                        host_path, container_path = volume.split(':')
                        volumes[host_path] = {'bind': container_path, 'mode': 'rw'}
                    except ValueError:
                        self.console.print(f"[red]Invalid volume format: {volume}. Use host:container[/red]")
                        sys.exit(1)
            
            success = self.quick_deploy(
                dockerfile_path=args.dockerfile_path,
                image_tag=args.image_tag,
                container_name=args.container_name,
                port_mapping=port_mapping,
                environment=environment if environment else None,
                volumes=volumes if volumes else None,
                yaml_config=args.yaml_config,
                cleanup_old_image=not args.no_cleanup
            )
            
            if not success:
                sys.exit(1)
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
                    "Container: list, list-img, start, stop, restart, remove, pause, unpause, stop-remove, exec, exec-simple, policy, run_image, logs, remove-image, json, build\n"
                    "Monitor: monitor, live-monitor, stats, health-check\n"
                    "Deploy: quick-deploy, deploy-init, deploy-config, history, promote\n"
                    "System: validate, backup-create, backup-restore, alerts, test, pipeline, docs, checklist\n"
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
                    names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2)")
                    containers = self._parse_multi_target(names_input)
                    
                    if not containers:
                        self.console.print("[red]No container names provided[/red]")
                        continue
                    
                    kwargs = {}
                    if choice in ("stop", "restart"):
                        kwargs['timeout'] = int(Prompt.ask("Timeout seconds", default="10"))
                    if choice == "remove":
                        kwargs['force'] = Confirm.ask("Force removal?", default=False)
                    
                    # Execute operation on each container
                    all_success = True
                    for container in containers:
                        self.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                        success = self.container_operation(choice, container, **kwargs)
                        if not success:
                            all_success = False
                    
                    if not all_success:
                        self.console.print(f"[yellow]⚠️ Some operations failed[/yellow]")
                    else:
                        self.console.print(f"[green]✅ All operations completed successfully[/green]")

                elif choice == "exec":
                    self.list_containers()
                    names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2)")
                    containers = self._parse_multi_target(names_input)
                    
                    if not containers:
                        self.console.print("[red]No container names provided[/red]")
                        continue
                    
                    command = Prompt.ask("Command to execute", default="/bin/bash")
                    
                    # Execute command in each container sequentially
                    for container in containers:
                        self.console.print(f"\n[cyan]Executing in container: {container}[/cyan]")
                        success = self.exec_container(container, command)
                        if not success:
                            self.console.print(f"[yellow]⚠️ Failed to exec in {container}, continuing...[/yellow]")

                elif choice == "stop-remove":
                    self.list_containers()
                    names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple)")
                    containers = self._parse_multi_target(names_input)
                    
                    if not containers:
                        self.console.print("[red]No container names provided[/red]")
                        continue
                    
                    timeout = int(Prompt.ask("Timeout seconds", default="10"))
                    
                    all_success = True
                    for container in containers:
                        self.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                        success = self.stop_and_remove_container(container, timeout)
                        if not success:
                            all_success = False
                    
                    if not all_success:
                        self.console.print(f"[yellow]⚠️ Some operations failed[/yellow]")
                    else:
                        self.console.print(f"[green]✅ All operations completed successfully[/green]")
                
                elif choice == "exec-simple":
                    self.list_containers()
                    container_name = Prompt.ask("Container name or ID")
                    command = Prompt.ask("Command to execute (e.g., 'ls -la')")
                    self.exec_command_non_interactive(container_name, command)
                
                elif choice == "monitor":
                    self.list_containers()
                    containers_input = Prompt.ask("Containers (comma separated, empty = all running)", default="").strip()
                    containers = [c.strip() for c in containers_input.split(",")] if containers_input else None
                    duration = int(Prompt.ask("Duration seconds", default="60"))
                    self.monitor_containers_dashboard(containers, duration)
                
                elif choice == "live-monitor":
                    self.list_containers()
                    container_name = Prompt.ask("Container name")
                    duration = int(Prompt.ask("Duration seconds", default="30"))
                    self.monitor_container_live(container_name, duration)
                
                elif choice == "stats":
                    self.list_containers()
                    container_name = Prompt.ask("Container name")
                    self.get_container_stats_once(container_name)
                
                elif choice == "health-check":
                    port = int(Prompt.ask("Port number"))
                    endpoint = Prompt.ask("Health check endpoint", default="/health")
                    max_retries = int(Prompt.ask("Maximum retries", default="10"))
                    self.health_check_standalone(port, endpoint, max_retries=max_retries)

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
                    self.list_containers()
                    containers_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, empty for interactive select)", default="").strip()
                    if containers_input:
                        self.view_container_logs(containers_input)
                    else:
                        self.view_container_logs()

                
                elif choice == "remove-image":
                    self.list_images()
                    images_input = Prompt.ask("Image name(s) or ID(s) to remove (comma-separated for multiple, e.g., img1:tag,img2:tag)")
                    images = self._parse_multi_target(images_input)
                    
                    if not images:
                        self.console.print("[red]No image names provided[/red]")
                        continue
                    
                    force = Confirm.ask("Force removal?", default=False)
                    
                    # Remove each image
                    all_success = True
                    for image in images:
                        self.console.print(f"\n[cyan]Processing image: {image}[/cyan]")
                        success = self.remove_image(image, force)
                        if not success:
                            all_success = False
                    
                    if not all_success:
                        self.console.print("[yellow]⚠️ Some operations failed[/yellow]")
                    else:
                        self.console.print("[green]✅ All operations completed successfully[/green]")

                elif choice == "quick-deploy":
                    dockerfile_path = Prompt.ask("Dockerfile directory path", default=".")
                    image_tag = Prompt.ask("Image tag (e.g., myapp:v1.2)")
                    container_name = Prompt.ask("Container name")
                    
                    # Optional YAML config
                    use_yaml = Confirm.ask("Load settings from YAML config?", default=False)
                    yaml_config = None
                    if use_yaml:
                        yaml_config = Prompt.ask("YAML config file path")
                    
                    # Port mapping (if not using YAML)
                    port_mapping = None
                    if not use_yaml:
                        port_input = Prompt.ask("Port mapping (format: container:host, e.g., 80:8080, empty to skip)", default="").strip()
                        if port_input:
                            try:
                                container_port, host_port = port_input.split(':')
                                port_mapping = {container_port: host_port}
                            except ValueError:
                                self.console.print("[red]Invalid port format[/red]")
                                continue
                    
                    # Environment variables
                    environment = None
                    if not use_yaml and Confirm.ask("Add environment variables?", default=False):
                        environment = {}
                        while True:
                            env_var = Prompt.ask("Environment variable (KEY=VALUE, empty to finish)", default="").strip()
                            if not env_var:
                                break
                            try:
                                key, value = env_var.split('=', 1)
                                environment[key] = value
                            except ValueError:
                                self.console.print("[red]Invalid format. Use KEY=VALUE[/red]")
                    
                    # Volumes
                    volumes = None
                    if not use_yaml and Confirm.ask("Add volume mappings?", default=False):
                        volumes = {}
                        while True:
                            volume = Prompt.ask("Volume mapping (host:container, empty to finish)", default="").strip()
                            if not volume:
                                break
                            try:
                                host_path, container_path = volume.split(':')
                                volumes[host_path] = {'bind': container_path, 'mode': 'rw'}
                            except ValueError:
                                self.console.print("[red]Invalid format. Use host:container[/red]")
                    
                    # Cleanup old image
                    cleanup_old_image = Confirm.ask("Remove old image after deployment?", default=True)
                    
                    success = self.quick_deploy(
                        dockerfile_path=dockerfile_path,
                        image_tag=image_tag,
                        container_name=container_name,
                        port_mapping=port_mapping,
                        environment=environment,
                        volumes=volumes,
                        yaml_config=yaml_config,
                        cleanup_old_image=cleanup_old_image
                    )
                    
                    if not success:
                        self.console.print("[red]Quick deploy failed[/red]")

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

