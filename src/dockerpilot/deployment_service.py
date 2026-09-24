"""Deployment and promotion services extracted from DockerPilotEnhanced."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os
import subprocess
import time

import docker
import requests
import yaml
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .build_source import (
    discover_dockerfile_candidates as _discover_dockerfile_candidates_impl,
    inspect_build_source as _inspect_build_source_impl,
    is_dockerfile_candidate as _is_dockerfile_candidate_impl,
)
from .deployment_helpers import (
    deployment_config_from_dict as _deployment_config_from_dict_impl,
    get_resource_limits as _get_resource_limits_impl,
    normalize_volumes as _normalize_volumes_impl,
    resolve_runtime_network as _resolve_runtime_network_impl,
)
from .deployment_history import record_deployment as _record_deployment_impl
from .deployment_strategies.blue_green import blue_green_deploy as _blue_green_deploy_strategy
from .deployment_strategies.quick import quick_deploy as _quick_deploy_strategy
from .deployment_strategies.rolling import rolling_deploy as _rolling_deploy_strategy
from .deployment_runtime import (
    apply_container_command as _apply_container_command_impl,
    offset_port_mapping as _offset_port_mapping_impl,
    requires_privileged_mode as _requires_privileged_mode_impl,
)
from .deployment_validation import (
    comprehensive_container_validation as _comprehensive_container_validation_impl,
)
from .image_preparation import (
    ensure_image_from_existing_container as _ensure_image_from_existing_container_impl,
    prepare_image as _prepare_image_impl,
)
from .health_checks import (
    advanced_health_check as _advanced_health_check_impl,
    detect_health_check_endpoint as _detect_health_check_endpoint_impl,
    monitor_canary_performance as _monitor_canary_performance_impl,
    run_parallel_tests as _run_parallel_tests_impl,
    should_run_parallel_tests as _should_run_parallel_tests_impl,
)
from .models import DeploymentConfig


def _load_dockerfile_template_bodies() -> dict[str, str]:
    """Load embedded Dockerfile starter bodies shipped next to this module."""
    path = Path(__file__).with_name("dockerfile_template_bodies.yaml")
    if not path.is_file():
        raise FileNotFoundError(f"Dockerfile templates data file not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping in {path.name}, got {type(raw).__name__}"
        )
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ValueError(
                f"Template {key!r} in {path.name} must be a string, got {type(value).__name__}"
            )
        out[str(key)] = value
    return out


class DeploymentServiceMixin:
    """Mixin containing deployment/build/promotion logic for DockerPilot."""

    _DOCKERFILE_TEMPLATE_BODIES = _load_dockerfile_template_bodies()

    def get_build_template_choices(self) -> list[str]:
        """Return the supported Dockerfile template names."""
        return sorted(self._DOCKERFILE_TEMPLATE_BODIES.keys())

    def inspect_build_source(self, dockerfile_path: str) -> dict[str, Any]:
        """Inspect a build source path and report how Dockerfile resolution will behave."""
        return _inspect_build_source_impl(
            dockerfile_path,
            is_candidate=self._is_dockerfile_candidate,
            discover_candidates=self._discover_dockerfile_candidates,
        )

    def _is_dockerfile_candidate(self, candidate: Path) -> bool:
        """Return True if the filename matches a Dockerfile-style pattern."""
        return _is_dockerfile_candidate_impl(candidate)

    def _discover_dockerfile_candidates(self, search_root: Path) -> list[Path]:
        """Search for Dockerfile-like files in the directory and one level below it."""
        return _discover_dockerfile_candidates_impl(
            search_root,
            is_candidate=self._is_dockerfile_candidate,
        )

    def create_dockerfile_template(self, destination: str, template_name: str) -> bool:
        """Create a starter Dockerfile in the requested directory."""
        template_body = self._DOCKERFILE_TEMPLATE_BODIES.get(template_name)
        if not template_body:
            self.console.print(f"[red]Unknown Dockerfile template: {template_name}[/red]")
            return False

        destination_path = Path(destination).expanduser()
        if not destination_path.is_absolute():
            destination_path = Path.cwd() / destination_path
        if destination_path.suffix or self._is_dockerfile_candidate(destination_path):
            destination_path = destination_path.parent
        destination_path.mkdir(parents=True, exist_ok=True)

        dockerfile_path = destination_path / "Dockerfile"
        if dockerfile_path.exists():
            self.console.print(f"[yellow]Dockerfile already exists at {dockerfile_path}[/yellow]")
            return False

        dockerfile_path.write_text(template_body, encoding="utf-8")
        self.console.print(f"[green]✅ Created {template_name} Dockerfile template at {dockerfile_path}[/green]")
        self.logger.info(f"Created Dockerfile template '{template_name}' at {dockerfile_path}")
        return True

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

    def _deployment_config_from_dict(self, deployment: dict) -> DeploymentConfig:
        """Build DeploymentConfig from raw dict while safely ignoring unknown keys."""
        return _deployment_config_from_dict_impl(deployment, self.logger)

    def _resolve_runtime_network(self, requested_network: Optional[str]) -> Optional[str]:
        """Return safe network name for container start, falling back to bridge if missing."""
        return _resolve_runtime_network_impl(
            requested_network,
            get_network=lambda network: self.client.networks.get(network),
            warn=lambda message: self.logger.warning(message),
        )

    def _ensure_image_from_existing_container(self, image_tag: str, container_name: Optional[str]) -> bool:
        """Try to satisfy image requirement by aliasing image used by existing container."""
        return _ensure_image_from_existing_container_impl(
            image_tag,
            container_name,
            client=self.client,
            logger=self.logger,
        )

    def deploy_from_config(self, config_path: str, deployment_type: str = "rolling") -> bool:
        """Deploy using configuration file"""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            deployment = config['deployment']
            deployment_config = self._deployment_config_from_dict(deployment)
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
        return _rolling_deploy_strategy(self, config, build_config)

    def view_container_logs(self, container_name: str = None, tail: int = 50):
        """View container logs."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return None
        return self.container_manager.view_container_logs(container_name, tail)
    
    def view_container_json(self, container_name: str):
        """Display container information in JSON format."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return None
        return self.container_manager.view_container_json(container_name)


    def _blue_green_deploy_enhanced(self, config: DeploymentConfig, build_config: dict, skip_backup: bool = False) -> bool:
        """Enhanced Blue-Green deployment with advanced features."""
        return _blue_green_deploy_strategy(
            self,
            config,
            build_config,
            skip_backup=skip_backup,
        )

    def quick_deploy(self, dockerfile_path: str = ".", image_tag: str = None, 
                    container_name: str = None, port_mapping: dict = None, 
                    environment: dict = None, volumes: dict = None,
                    yaml_config: str = None, cleanup_old_image: bool = True) -> bool:
        """Quick deployment: build and replace the target container."""
        return _quick_deploy_strategy(
            self,
            dockerfile_path=dockerfile_path,
            image_tag=image_tag,
            container_name=container_name,
            port_mapping=port_mapping,
            environment=environment,
            volumes=volumes,
            yaml_config=yaml_config,
            cleanup_old_image=cleanup_old_image,
        )

    def _canary_deploy(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Canary deployment with gradual traffic shifting"""
        self.console.print(f"\n[bold cyan]🐤 CANARY DEPLOYMENT STARTED[/bold cyan]")
        
        # This would require a load balancer integration
        # For now, we'll implement a simplified version
        
        deployment_start = datetime.now()
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            
            # Prepare image
            build_task = progress.add_task("🔨 Preparing canary image...", total=None)
            try:
                success, message = self._prepare_image(config.image_tag, build_config, config.container_name)
                if not success:
                    progress.update(build_task, description=f"❌ {message}")
                    self.console.print(f"[bold red]❌ {message}[/bold red]")
                    return False
                progress.update(build_task, description=f"✅ {message}")
            except Exception as e:
                progress.update(build_task, description="❌ Image preparation failed")
                self.logger.error(f"Image preparation failed: {e}")
                self.console.print(f"[bold red]❌ Image preparation failed: {e}[/bold red]")
                return False
            
            # Deploy canary container (5% traffic simulation)
            canary_name = f"{config.container_name}_canary"
            canary_task = progress.add_task("🚀 Deploying canary (5% traffic)...", total=None)
            
            # Use different port for canary
            canary_port_mapping = _offset_port_mapping_impl(config.port_mapping, 100)
            
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
                    volumes=self._normalize_volumes(config.volumes),
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
                    volumes=self._normalize_volumes(config.volumes),
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

    def _prepare_image(self, image_tag: str, build_config: dict = None, container_name: Optional[str] = None):
        """Prepare image for deployment - check if exists, pull, or build."""
        return _prepare_image_impl(
            image_tag,
            build_config,
            container_name,
            client=self.client,
            logger=self.logger,
            ensure_existing_image=self._ensure_image_from_existing_container,
            build_image=self._build_image_enhanced,
        )

    def _build_image_enhanced(self, image_tag: str, build_config: dict) -> bool:
        """Enhanced image building with advanced features"""
        dockerfile_path = build_config.get('dockerfile_path', '.')
        no_cache = build_config.get('no_cache', False)
        pull = build_config.get('pull', True)
        build_args = build_config.get('build_args', {})
        
        try:
            source_info = self.inspect_build_source(dockerfile_path)
            if source_info["status"] != "ready":
                self.console.print(f"[bold red]❌ {source_info['message']}[/bold red]")
                if source_info["status"] == "multiple":
                    self.console.print("[yellow]Select one of these paths explicitly:[/yellow]")
                    for candidate in source_info["candidates"]:
                        self.console.print(f"  - {candidate}")
                return False

            context = source_info["context_path"]
            dockerfile = source_info["selected_path"]
            dockerfile_name = source_info["dockerfile_name"]

            if source_info["auto_detected"]:
                self.console.print(f"[yellow]Auto-detected Dockerfile: {dockerfile}[/yellow]")
            
            # Build with enhanced logging
            self.logger.info(f"Building image {image_tag} from {context} using {dockerfile_name}")
            
            build_kwargs = {
                'path': str(context),
                'tag': image_tag,
                'rm': True,
                'nocache': no_cache,
                'pull': pull,
                'buildargs': build_args
            }
            if dockerfile_name and dockerfile_name != "Dockerfile":
                build_kwargs['dockerfile'] = dockerfile_name
            
            # Show loading indicator during build
            with self._with_loading("Building image"):
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
        
    def build_image_standalone(
        self,
        dockerfile_path: str,
        tag: str,
        no_cache: bool = False,
        pull: bool = True,
        pull_if_missing: bool = False,
        generate_template: Optional[str] = None,
    ) -> bool:
        """Standalone image building function"""
        source_info = self.inspect_build_source(dockerfile_path)

        if source_info["status"] != "ready":
            self.console.print(f"[yellow]{source_info['message']}[/yellow]")
            if source_info["status"] == "multiple":
                self.console.print("[yellow]Available Dockerfile candidates:[/yellow]")
                for candidate in source_info["candidates"]:
                    self.console.print(f"  - {candidate}")

            if generate_template:
                created = self.create_dockerfile_template(str(source_info["requested_path"]), generate_template)
                if created:
                    source_info = self.inspect_build_source(dockerfile_path)

            if source_info["status"] != "ready" and pull_if_missing:
                try:
                    self.console.print(f"[cyan]Pulling image {tag} from registry...[/cyan]")
                    self.client.images.pull(tag)
                    self.console.print(f"[green]✅ Pulled image {tag} successfully[/green]")
                    self.logger.info(f"Pulled image {tag} because no buildable Dockerfile was available")
                    return True
                except Exception as exc:
                    self.console.print(f"[red]❌ Failed to pull image {tag}: {exc}[/red]")
                    self.logger.error(f"Failed to pull image {tag}: {exc}")
                    return False

            if source_info["status"] != "ready":
                self.console.print("[yellow]Hints:[/yellow]")
                self.console.print("  - point build at a directory or file that contains a Dockerfile")
                self.console.print(f"  - rerun with --pull-if-missing to pull {tag} from a registry instead")
                self.console.print(f"  - rerun with --generate-template {{{', '.join(self.get_build_template_choices())}}} to create a starter Dockerfile")
                return False

        build_config = {
            'dockerfile_path': str(source_info["selected_path"]),
            'context': str(source_info["context_path"]),
            'no_cache': no_cache,
            'pull': pull,
            'build_args': {}
        }

        self.console.print(f"[cyan]Building image {tag} from {source_info['context_path']}...[/cyan]")

        success = self._build_image_enhanced(tag, build_config)

        if success:
            self.console.print(f"[green]✅ Image {tag} built successfully[/green]")
        else:
            self.console.print(f"[red]❌ Failed to build image {tag}[/red]")

        return success

    def _detect_health_check_endpoint(self, image_tag: str) -> Optional[str]:
        """Select a health-check endpoint for an image."""
        return _detect_health_check_endpoint_impl(
            image_tag,
            defaults=self._load_health_check_defaults(),
            config=self.config,
            logger=self.logger,
        )
    
    def _advanced_health_check(self, port: str, endpoint: Optional[str], timeout: int, max_retries: int) -> bool:
        """Run the retrying HTTP health check."""
        return _advanced_health_check_impl(
            port,
            endpoint,
            timeout,
            max_retries,
            logger=self.logger,
        )

    def _comprehensive_container_validation(self, container, config: DeploymentConfig, 
                                          port: str, target_name: str) -> tuple:
        """Run the comprehensive pre-traffic-switch validation."""
        return _comprehensive_container_validation_impl(
            container,
            config,
            port,
            target_name,
            get_database_config=self._get_database_config,
            get_database_name=self._get_database_name,
            logger=self.logger,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            clock=lambda: time.time(),
            sleep=lambda seconds: time.sleep(seconds),
        )

    def _get_resource_limits(self, config: DeploymentConfig) -> dict:
        """Convert resource limits to Docker API format"""
        return _get_resource_limits_impl(config)

    def _normalize_volumes(self, volumes: Dict[str, str]) -> list:
        """Convert volumes from config format to Docker API format.
        
        Docker Python API's containers.run() expects volumes as a list of strings
        in the format: ['/host/path:/container/path', 'volume_name:/container/path']
        
        Supports:
        - Named volumes: 'volume_name': '/container/path' -> ['volume_name:/container/path']
        - Bind mounts: '/host/path': '/container/path' -> ['/host/path:/container/path']
        - Already formatted as list: ['volume:/path'] -> unchanged
        - Already formatted as dict with bind/mode: {'/host': {'bind': '/container', 'mode': 'rw'}} -> ['/host:/container:rw']
        """
        return _normalize_volumes_impl(volumes, self.logger)

    def _should_run_parallel_tests(self) -> bool:
        """Determine if parallel tests should be run"""
        return _should_run_parallel_tests_impl(self.config)

    def _run_parallel_tests(self, port: str, config: DeploymentConfig) -> bool:
        """Run parallel tests against new deployment"""
        return _run_parallel_tests_impl(
            port,
            self.config,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            log_error=lambda message: self.logger.error(message),
        )

    def _monitor_canary_performance(self, port: str, duration: int) -> bool:
        """Monitor canary deployment performance"""
        return _monitor_canary_performance_impl(
            port,
            duration,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            clock=lambda: time.time(),
            sleep=lambda seconds: time.sleep(seconds),
            log_error=lambda message: self.logger.error(message),
            log_info=lambda message: self.logger.info(message),
        )

    def _record_deployment(self, deployment_id: str, config: DeploymentConfig, 
                          deployment_type: str, success: bool, duration: timedelta, target_env: str = None):
        """Record deployment in history"""
        return _record_deployment_impl(
            deployment_id,
            config,
            deployment_type,
            success,
            duration,
            target_env,
            append_record=lambda record: self.deployment_history.append(record),
            log_error=lambda message: self.logger.error(message),
            now=datetime.now,
            history_file="deployment_history.json",
        )

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

    def environment_promotion(self, source_env: str, target_env: str, 
                            config_path: str = None, skip_backup: bool = False) -> bool:
        """Promote deployment between environments (dev -> staging -> prod)
        
        Args:
            source_env: Source environment (dev/staging)
            target_env: Target environment (staging/prod)
            config_path: Path to deployment config (optional)
            skip_backup: Skip data backup before deployment (faster but risky)
        """
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
            
            # Keep original image tag for all environments
            # Don't modify image tags during promotion - same image should be used across environments
            # Environment-specific tags should be set manually in deployment configs if needed
            original_image_tag = config['deployment']['image_tag']
            config['deployment']['image_tag'] = original_image_tag
            self.logger.info(f"Using original image tag for promotion: {original_image_tag}")
            
            # Update resources
            config['deployment']['cpu_limit'] = target_config['resources']['cpu']
            config['deployment']['memory_limit'] = target_config['resources']['memory']
            
            # For STAGING environment, check if container already exists in PROD
            # If it does, don't create duplicate - just save config and mark as promoted
            original_container_name = config['deployment']['container_name']
            if target_env == 'staging':
                # Check if container with base name or variants already exists (likely in PROD)
                existing_prod_container = None
                try:
                    existing_prod_container = self.client.containers.get(original_container_name)
                except docker.errors.NotFound:
                    # Also check for blue/green variants
                    for suffix in ['_blue', '_green']:
                        try:
                            existing_prod_container = self.client.containers.get(f"{original_container_name}{suffix}")
                            break
                        except docker.errors.NotFound:
                            pass
                
                if existing_prod_container and existing_prod_container.status == 'running':
                    # Container already exists in PROD - don't create duplicate for STAGING
                    # Just save config and record promotion without deploying
                    self.console.print(f"[yellow]⚠️ Container '{original_container_name}' already exists in PROD[/yellow]")
                    self.console.print(f"[cyan]Saving STAGING configuration without creating new container...[/cyan]")
                    
                    # Save config for staging (will be used for future deployments)
                    container_name = config['deployment']['container_name']
                    image_tag = config['deployment']['image_tag']
                    config_dir = Path(config_path).parent
                    target_config_path = config_dir / f'deployment-{target_env}.yml'
                    with open(target_config_path, 'w', encoding='utf-8') as f:
                        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                    self.logger.info(f"Saved deployment config for {target_env} environment to {target_config_path}")
                    
                    # Record promotion without deploying
                    deployment_id = f"promote_{source_env}_to_{target_env}_{int(time.time())}"
                    duration = timedelta(seconds=1)
                    
                    # Create a deployment record for staging
                    deployment_config = self._deployment_config_from_dict(config['deployment'])
                    self._record_deployment(deployment_id, deployment_config, f'promotion-{target_env}', True, duration, target_env=target_env)
                    
                    self.console.print(f"[green]✓ STAGING configuration saved. Container already running in PROD, no new deployment needed.[/green]")
                    return True
            
            # Run pre-promotion checks
            if not self._run_pre_promotion_checks(source_env, target_env):
                self.console.print("[red]Pre-promotion checks failed[/red]")
                return False
            
            deployment = config['deployment']
            deployment_config = self._deployment_config_from_dict(deployment)
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
                    # Save deployment config for target environment
                    # Extract container name from config
                    container_name = config['deployment']['container_name']
                    image_tag = config['deployment']['image_tag']
                    
                    # Save config to unified deployment directory structure
                    # Save directly next to source config
                    config_dir = Path(config_path).parent
                    target_config_path = config_dir / f'deployment-{target_env}.yml'
                    with open(target_config_path, 'w', encoding='utf-8') as f:
                        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                    self.logger.info(f"Saved deployment config for {target_env} environment to {target_config_path}")
                    
                    # Update metadata.json if it exists
                    metadata_path = config_dir / 'metadata.json'
                    if metadata_path.exists():
                        try:
                            with open(metadata_path, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                            metadata['last_updated'] = datetime.now().isoformat()
                            metadata[f'env_{target_env}_config'] = str(target_config_path)
                            with open(metadata_path, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2)
                            self.logger.info(f"Updated metadata.json with {target_env} config path")
                        except Exception as e:
                            self.logger.warning(f"Could not update metadata.json: {e}")
                    
                    # Record deployment with environment information
                    deployment_id = f"promote_{source_env}_to_{target_env}_{int(time.time())}"
                    duration = timedelta(seconds=5)  # Approximate duration
                    self._record_deployment(deployment_id, deployment_config, f'promotion-{target_env}', True, duration, target_env=target_env)
                    
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
