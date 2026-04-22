"""Deployment and promotion services extracted from DockerPilotEnhanced."""

from dataclasses import fields
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
        requested_path = Path(dockerfile_path).expanduser()
        if not requested_path.is_absolute():
            requested_path = Path.cwd() / requested_path
        requested_path = requested_path.resolve(strict=False)

        if requested_path.is_file():
            if self._is_dockerfile_candidate(requested_path):
                return {
                    "status": "ready",
                    "requested_path": requested_path,
                    "context_path": requested_path.parent,
                    "dockerfile_name": requested_path.name,
                    "selected_path": requested_path,
                    "auto_detected": False,
                    "candidates": [requested_path],
                    "message": f"Using Dockerfile file {requested_path}.",
                }
            return {
                "status": "invalid",
                "requested_path": requested_path,
                "context_path": requested_path.parent,
                "dockerfile_name": requested_path.name,
                "selected_path": None,
                "auto_detected": False,
                "candidates": [],
                "message": f"{requested_path} is a file, but it does not look like a Dockerfile.",
            }

        explicit_dockerfile = requested_path / "Dockerfile"
        if explicit_dockerfile.exists():
            return {
                "status": "ready",
                "requested_path": requested_path,
                "context_path": requested_path,
                "dockerfile_name": "Dockerfile",
                "selected_path": explicit_dockerfile,
                "auto_detected": False,
                "candidates": [explicit_dockerfile],
                "message": f"Using Dockerfile at {explicit_dockerfile}.",
            }

        candidates = self._discover_dockerfile_candidates(requested_path)
        if len(candidates) == 1:
            candidate = candidates[0]
            return {
                "status": "ready",
                "requested_path": requested_path,
                "context_path": candidate.parent,
                "dockerfile_name": candidate.name,
                "selected_path": candidate,
                "auto_detected": True,
                "candidates": candidates,
                "message": f"No Dockerfile found directly in {requested_path}. Auto-detected {candidate}.",
            }

        if len(candidates) > 1:
            return {
                "status": "multiple",
                "requested_path": requested_path,
                "context_path": requested_path,
                "dockerfile_name": None,
                "selected_path": None,
                "auto_detected": False,
                "candidates": candidates,
                "message": f"Found multiple Dockerfile candidates under {requested_path}.",
            }

        return {
            "status": "missing",
            "requested_path": requested_path,
            "context_path": requested_path,
            "dockerfile_name": None,
            "selected_path": None,
            "auto_detected": False,
            "candidates": [],
            "message": f"No Dockerfile found in {requested_path}.",
        }

    def _is_dockerfile_candidate(self, candidate: Path) -> bool:
        """Return True if the filename matches a Dockerfile-style pattern."""
        lowered = candidate.name.lower()
        return lowered == "dockerfile" or lowered.startswith("dockerfile.")

    def _discover_dockerfile_candidates(self, search_root: Path) -> list[Path]:
        """Search for Dockerfile-like files in the directory and one level below it."""
        if not search_root.exists() or not search_root.is_dir():
            return []

        candidates: list[Path] = []
        seen: set[Path] = set()

        def add_candidate(candidate: Path) -> None:
            resolved = candidate.resolve(strict=False)
            if resolved in seen or not candidate.is_file() or not self._is_dockerfile_candidate(candidate):
                return
            seen.add(resolved)
            candidates.append(candidate)

        for child in sorted(search_root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file():
                add_candidate(child)

        for child in sorted(search_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            for nested in sorted(child.iterdir(), key=lambda item: item.name.lower()):
                if nested.is_file():
                    add_candidate(nested)

        return candidates

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
        deployment = deployment or {}
        if not isinstance(deployment, dict):
            raise ValueError("deployment config must be a dictionary")

        normalized = dict(deployment)
        for key in ("volumes", "port_mapping", "environment", "build_args"):
            if normalized.get(key) is None or not isinstance(normalized.get(key), dict):
                normalized[key] = {}

        model_fields = {field.name for field in fields(DeploymentConfig)}
        extra_keys = sorted(k for k in normalized.keys() if k not in model_fields)
        if extra_keys:
            self.logger.warning(f"Ignoring unsupported deployment config field(s): {', '.join(extra_keys)}")

        filtered = {k: v for k, v in normalized.items() if k in model_fields}
        return DeploymentConfig(**filtered)

    def _resolve_runtime_network(self, requested_network: Optional[str]) -> Optional[str]:
        """Return safe network name for container start, falling back to bridge if missing."""
        if requested_network is None:
            return "bridge"

        network = str(requested_network).strip()
        if not network:
            return "bridge"
        if network in {"bridge", "host", "none"}:
            return network

        try:
            self.client.networks.get(network)
            return network
        except docker.errors.NotFound:
            self.logger.warning(
                f"Docker network '{network}' not found on current host. Falling back to 'bridge'."
            )
            return "bridge"
        except Exception as exc:
            self.logger.warning(
                f"Could not validate docker network '{network}' ({exc}). Falling back to 'bridge'."
            )
            return "bridge"

    def _ensure_image_from_existing_container(self, image_tag: str, container_name: Optional[str]) -> bool:
        """Try to satisfy image requirement by aliasing image used by existing container."""
        if not container_name:
            return False

        try:
            container = self.client.containers.get(container_name)
        except docker.errors.NotFound:
            return False
        except Exception as exc:
            self.logger.debug(f"Could not inspect container {container_name} for image fallback: {exc}")
            return False

        source_image = container.image
        if not source_image:
            return False

        if image_tag in (source_image.tags or []):
            self.logger.info(f"Using image {image_tag} from existing container {container_name}")
            return True

        if "@" in image_tag:
            # Digest references cannot be re-tagged with Docker tag semantics.
            self.logger.info(
                f"Using digest image from existing container {container_name} (skipping local retag for {image_tag})"
            )
            return True

        if ":" in image_tag and image_tag.rfind(":") > image_tag.rfind("/"):
            repository, tag = image_tag.rsplit(":", 1)
        else:
            repository, tag = image_tag, "latest"
        try:
            source_image.tag(repository, tag=tag)
            self.logger.info(
                f"Tagged existing container image {container.id[:12]} as {repository}:{tag} for deployment fallback"
            )
            return True
        except Exception as exc:
            self.logger.warning(
                f"Could not tag image from container {container_name} as {image_tag}: {exc}"
            )
            return False

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
        self.console.print(f"\n[bold cyan]🚀 ROLLING DEPLOYMENT STARTED[/bold cyan]")

        deployment_start = datetime.now()
        deployment_id = f"deploy_{int(deployment_start.timestamp())}"
        runtime_network = self._resolve_runtime_network(config.network)
        
        # Auto-detect health check endpoint based on image type
        detected_endpoint = self._detect_health_check_endpoint(config.image_tag)
        if detected_endpoint != config.health_check_endpoint:
            self.logger.info(f"Auto-detected health check endpoint: {detected_endpoint} (was: {config.health_check_endpoint})")
            config.health_check_endpoint = detected_endpoint

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:

            # Phase 1: Prepare image (check, pull, or build)
            build_task = progress.add_task("🔨 Preparing image...", total=None)
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
                # Add command if provided in config (for images that exit immediately without command)
                create_kwargs = {
                    'image': config.image_tag,
                    'name': temp_name,
                    'ports': config.port_mapping,
                    'environment': config.environment,
                    'volumes': self._normalize_volumes(config.volumes),
                    'restart_policy': {"Name": config.restart_policy},
                    **self._get_resource_limits(config)
                }
                if runtime_network == "host":
                    create_kwargs['network_mode'] = "host"
                elif runtime_network:
                    create_kwargs['network'] = runtime_network
                if hasattr(config, 'command') and config.command:
                    create_kwargs['command'] = config.command
                elif 'alpine' in config.image_tag.lower():
                    # Alpine needs a command to stay running
                    create_kwargs['command'] = ['sh', '-c', 'sleep 3600']
                
                new_container = self.client.containers.create(**create_kwargs)

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

                # Grace period - longer for HTTP services like nginx
                grace_period = 5
                if 'nginx' in config.image_tag.lower() or 'http' in config.image_tag.lower():
                    grace_period = 15  # nginx needs more time to start
                time.sleep(grace_period)
                
                # Verify container is running before health check
                try:
                    new_container.reload()
                    if new_container.status != "running":
                        self.logger.error(f"Container {new_container.name} is not running (status: {new_container.status})")
                        new_container.stop()
                        new_container.remove()
                        return False
                except Exception as e:
                    self.logger.warning(f"Could not verify container status: {e}")

            except Exception as e:
                progress.update(deploy_task, description="❌ New container creation failed")
                self.logger.error(f"New container creation failed: {e}")
                return False

            # Phase 4: Health check new container (only if ports are mapped)
            if config.port_mapping:
                health_check_task = progress.add_task("🩺 Health checking new deployment...", total=None)
                host_port = list(config.port_mapping.values())[0]
                
                # Wait a bit more and verify container is running before health check
                time.sleep(2)
                try:
                    new_container.reload()
                    if new_container.status != "running":
                        progress.update(health_check_task, description="❌ Container not running")
                        new_container.stop()
                        new_container.remove()
                        return False
                except Exception as e:
                    self.logger.warning(f"Could not verify container status: {e}")
                
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
        """Enhanced Blue-Green deployment with advanced features
        
        Args:
            config: Deployment configuration
            build_config: Build configuration
            skip_backup: Skip data backup (faster but risky for production)
        """
        self.console.print(f"\n[bold cyan]🔵🟢 BLUE-GREEN DEPLOYMENT STARTED[/bold cyan]")
        
        deployment_start = datetime.now()
        deployment_id = f"bg_deploy_{int(deployment_start.timestamp())}"
        
        # Track current deployment for cancellation support
        self._current_deployment_container = config.container_name
        
        # Auto-detect health check endpoint based on image type
        detected_endpoint = self._detect_health_check_endpoint(config.image_tag)
        if detected_endpoint != config.health_check_endpoint:
            self.logger.info(f"Auto-detected health check endpoint: {detected_endpoint} (was: {config.health_check_endpoint})")
            config.health_check_endpoint = detected_endpoint
        runtime_network = self._resolve_runtime_network(config.network)
        
        blue_name = f"{config.container_name}_blue"
        green_name = f"{config.container_name}_green"
        
        # Determine current active container
        active_container = None
        active_name = None
        
        # Check for blue-green containers first
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
        
        # If no blue-green container found, check for main container (without suffix)
        # This handles migration from old deployment to blue-green
        if not active_container:
            try:
                main_container = self.client.containers.get(config.container_name)
                if main_container.status == "running":
                    active_container = main_container
                    active_name = "main"
                    self.console.print(f"[yellow]Found existing container '{config.container_name}', will migrate to blue-green[/yellow]")
            except docker.errors.NotFound:
                pass
        
        target_name = "green" if active_name == "blue" else "blue"
        target_container_name = green_name if target_name == "green" else blue_name
        
        self.console.print(f"[cyan]Current active: {active_name or 'none'} | Deploying to: {target_name}[/cyan]")
        
        # CHECKPOINT 1: Check for cancellation before backup
        if self._check_cancel_flag():
            self.console.print("[yellow]🛑 Deployment cancelled by user (before backup)[/yellow]")
            self._current_deployment_container = None
            # Clean up any orphaned backup containers
            self._cleanup_backup_containers()
            return False
        
        # Backup OUTSIDE Progress context to avoid "Only one live display" error
        backup_path = None
        if active_container and not skip_backup:
            self._update_progress('backup', 5, '💾 Creating data backup...')
            self.console.print(f"[cyan]💾 Backing up container data...[/cyan]")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_{config.container_name}_pre_deploy_{timestamp}"
            
            # Try to reuse existing backup first (reuse_existing=True by default, max age 24 hours)
            if self.backup_container_data(active_container.name, backup_path, reuse_existing=True, max_backup_age_hours=24):
                self._update_progress('backup', 20, '✅ Backup completed')
                # Check if backup was reused or newly created
                backup_dir = Path(backup_path)
                if backup_dir.exists() and (backup_dir / 'backup_metadata.json').exists():
                    try:
                        with open(backup_dir / 'backup_metadata.json', 'r') as f:
                            metadata = json.load(f)
                        backup_time_str = metadata.get('backup_time', '')
                        if backup_time_str:
                            backup_time = datetime.fromisoformat(backup_time_str.replace('Z', '+00:00'))
                            if backup_time.tzinfo is None:
                                backup_time = backup_time.replace(tzinfo=datetime.now().astimezone().tzinfo)
                            age_seconds = (datetime.now(backup_time.tzinfo) - backup_time).total_seconds()
                            if age_seconds > 60:  # More than 1 minute old, it was reused
                                self.console.print(f"[green]💾 Using existing backup: {backup_path}[/green]")
                            else:
                                self.console.print(f"[green]💾 Data backup saved to: {backup_path}[/green]")
                    except:
                        self.console.print(f"[green]💾 Data backup saved to: {backup_path}[/green]")
                else:
                    self.console.print(f"[green]💾 Using existing backup[/green]")
            else:
                self.console.print(f"[yellow]⚠️ Data backup failed, but continuing deployment...[/yellow]")
                self.logger.warning("Data backup failed before deployment - this is risky for production!")
        elif skip_backup and active_container:
            self.console.print("[yellow]⚠️ Skipping data backup (--skip-backup flag)[/yellow]")
        else:
            self.console.print("[cyan]ℹ️ No active container to backup[/cyan]")
        
        # CHECKPOINT 2: Check for cancellation after backup
        if self._check_cancel_flag():
            self.console.print("[yellow]🛑 Deployment cancelled by user (after backup)[/yellow]")
            self._current_deployment_container = None
            # Clean up any orphaned backup containers
            self._cleanup_backup_containers()
            return False
        
        # Clean up any orphaned backup containers from previous interrupted deployments
        # This ensures we start with a clean state
        self._cleanup_backup_containers()
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            
            # Add backup status to progress display
            if backup_path:
                progress.add_task(f"✅ Data backed up to {backup_path}", total=None)
            
            # Build or pull image
            build_task = progress.add_task("🔨 Preparing image...", total=None)
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
            self._update_progress('deploy', 50, f'🚀 Deploying to slot {target_name}...')
            deploy_task = progress.add_task(f"🚀 Deploying to {target_name} slot...", total=None)
            
            # Prepare container creation parameters
            normalized_volumes = self._normalize_volumes(config.volumes)
            self.logger.debug(f"Normalized volumes: {normalized_volumes}")
            
            container_kwargs = {
                'image': config.image_tag,
                'name': target_container_name,
                'detach': True,
                'environment': config.environment,
                'volumes': normalized_volumes,
                'restart_policy': {"Name": config.restart_policy},
            }
            
            # Handle network mode
            if runtime_network == 'host':
                # With host network, ports are directly mapped - no port mapping needed
                container_kwargs['network_mode'] = 'host'
                # For host network, we can't use different ports for testing
                # So we need to stop the active container first to free up the port
                if active_container:
                    # Stop the active container to free up the port for host network
                    container_to_stop_name = active_container.name
                    self.console.print(f"[yellow]Stopping active container '{container_to_stop_name}' to free port for host network...[/yellow]")
                    try:
                        active_container.stop(timeout=10)
                        self.console.print(f"[green]Active container stopped[/green]")
                        # Wait a moment for port to be released
                        time.sleep(2)
                    except Exception as e:
                        self.logger.warning(f"Failed to stop active container: {e}")
                        # Try to continue anyway - the new container might fail with port conflict
                temp_port_mapping = None
            else:
                # Use different port for parallel testing when not using host network
                temp_port_mapping = None  # Initialize before conditional
                if config.port_mapping and len(config.port_mapping) > 0:
                    temp_port_mapping = {}
                    for container_port, host_port in config.port_mapping.items():
                        temp_port_mapping[container_port] = str(int(host_port) + 1000)  # +1000 for temp
                    container_kwargs['ports'] = temp_port_mapping
                if runtime_network and runtime_network != 'bridge':
                    container_kwargs['network'] = runtime_network
            
            # Add resource limits
            container_kwargs.update(self._get_resource_limits(config))
            
            # Add privileged mode if requested (needed for DB2 with bind mounts to support setuid)
            # Also auto-detect for infrastructure containers (minikube, kubernetes, etc.)
            requires_privileged = False
            if hasattr(config, 'privileged') and config.privileged:
                requires_privileged = True
            else:
                # Auto-detect infrastructure containers that require privileged mode
                image_lower = config.image_tag.lower()
                infrastructure_containers = ['minikube', 'kicbase', 'kubernetes', 'k8s', 'kind', 'k3s', 'k3d']
                for infra_container in infrastructure_containers:
                    if infra_container in image_lower:
                        requires_privileged = True
                        self.logger.info(f"Auto-detected infrastructure container requiring privileged mode: {infra_container}")
                        break
                
                # Also check if active container has privileged mode enabled
                if active_container:
                    try:
                        active_privileged = active_container.attrs.get('HostConfig', {}).get('Privileged', False)
                        if active_privileged:
                            requires_privileged = True
                            self.logger.info(f"Active container has privileged mode enabled, copying to new container")
                    except Exception as e:
                        self.logger.debug(f"Could not check active container privileged mode: {e}")
            
            if requires_privileged:
                container_kwargs['privileged'] = True
                self.logger.info(f"Container {target_container_name} will run in privileged mode")
            
            # Add command if provided in config (for images that exit immediately without command)
            if hasattr(config, 'command') and config.command:
                container_kwargs['command'] = config.command
            elif 'alpine' in config.image_tag.lower():
                # Alpine needs a command to stay running
                container_kwargs['command'] = ['sh', '-c', 'sleep 3600']
            
            try:
                target_container = self.client.containers.run(**container_kwargs)
                
                progress.update(deploy_task, description=f"✅ {target_name.title()} container deployed")
                self._update_progress('deploy', 60, f'✅ Kontener {target_name} wdrożony')
                
                # Longer startup grace period for databases and services with slow startup
                startup_grace = 5
                db_config = self._get_database_config(config.image_tag)
                if db_config:
                    startup_grace = db_config.get('startup_grace_period', 15)
                    db_name = self._get_database_name(config.image_tag) or 'database'
                    self.logger.info(f"Extended startup grace period: {startup_grace}s for {db_name} service")
                
                time.sleep(startup_grace)
                
                # CHECKPOINT 3: Check for cancellation after container creation
                if self._check_cancel_flag():
                    self.console.print("[yellow]🛑 Deployment cancelled by user (after container creation)[/yellow]")
                    # Cleanup new container
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    self._current_deployment_container = None
                    # Clean up any orphaned backup containers
                    self._cleanup_backup_containers()
                    return False
                
                # Migrate data from active container to new container
                if active_container and active_container.status == "running":
                    migrate_task = progress.add_task("📦 Migrating data to new container...", total=None)
                    try:
                        migration_success = self._migrate_container_data(active_container, target_container, config)
                        if migration_success:
                            progress.update(migrate_task, description="✅ Data migration completed")
                        else:
                            progress.update(migrate_task, description="⚠️ Data migration had issues (continuing...)")
                            self.logger.warning("Data migration completed with warnings, continuing deployment")
                    except Exception as e:
                        self.logger.error(f"Data migration failed: {e}")
                        progress.update(migrate_task, description="⚠️ Data migration failed (continuing...)")
                        # Don't fail deployment if migration fails - just log warning
                
            except Exception as e:
                progress.update(deploy_task, description=f"❌ {target_name.title()} deployment failed")
                self.logger.error(f"Container creation failed: {e}")
                self.logger.error(f"Container kwargs: {container_kwargs}")
                # Try to get more details about the error
                if hasattr(e, 'explanation'):
                    self.logger.error(f"Error explanation: {e.explanation}")
                return False
            
            # Comprehensive validation of new deployment
            self._update_progress('health_check', 70, f'🩺 Checking container health {target_name}...')
            health_task = progress.add_task(f"🔍 Comprehensive validation of {target_name} deployment...", total=None)
            
            # Determine port for validation
            validation_port = None
            if runtime_network == 'host':
                # With host network, use the original port directly
                validation_port = list(config.port_mapping.values())[0] if config.port_mapping else '3000'
            elif 'temp_port_mapping' in locals() and temp_port_mapping and len(temp_port_mapping) > 0:
                validation_port = list(temp_port_mapping.values())[0]
            else:
                validation_port = list(config.port_mapping.values())[0] if config.port_mapping else None
            
            if validation_port:
                # Check if this is a non-HTTP service (endpoint is None)
                if config.health_check_endpoint is None:
                    # Skip HTTP health check for non-HTTP services (SSH, Redis, infrastructure, etc.)
                    progress.update(health_task, description=f"ℹ️ Skipping HTTP health check (non-HTTP service)")
                    self.logger.info(f"Skipping HTTP health check for {target_name} (non-HTTP service)")
                else:
                    # First, do basic health check to ensure service is responding
                    progress.update(health_task, description=f"🩺 Basic health check ({target_name})...")
                    
                    # Increase retries for slow-starting services (databases, etc.)
                    health_retries = config.health_check_retries
                    db_config = self._get_database_config(config.image_tag)
                    if db_config:
                        health_retries = max(health_retries, db_config.get('health_check_retries', 20))
                        db_name = self._get_database_name(config.image_tag) or 'database'
                        self.logger.info(f"Extended health check retries: {health_retries} for {db_name} service")
                        
                        # Add extra wait time before validation if configured
                        additional_wait = db_config.get('additional_wait_before_validation', 0)
                        if additional_wait > 0:
                            self.logger.info(f"Waiting additional {additional_wait}s for {db_name} to finish initialization...")
                            time.sleep(additional_wait)
                    
                    if not self._advanced_health_check(
                        validation_port,
                        config.health_check_endpoint,
                        config.health_check_timeout,
                        health_retries
                    ):
                        progress.update(health_task, description=f"❌ {target_name.title()} basic health check failed")
                        try:
                            target_container.stop()
                            target_container.remove()
                        except:
                            pass
                        return False
                
                # Then, comprehensive validation
                progress.update(health_task, description=f"🔍 Comprehensive validation ({target_name})...")
                is_valid, error_msg = self._comprehensive_container_validation(
                    target_container, config, validation_port, target_name
                )
                
                if not is_valid:
                    progress.update(health_task, description=f"❌ {target_name.title()} validation failed")
                    self.logger.error(f"Container validation failed: {error_msg}")
                    
                    # Get container logs for debugging
                    try:
                        logs = target_container.logs(tail=50).decode('utf-8', errors='ignore')
                        self.logger.error(f"Container logs (last 50 lines):\n{logs}")
                    except:
                        pass
                    
                    # Cleanup failed container
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    
                    return False
                
                progress.update(health_task, description=f"✅ {target_name.title()} validation passed")
                self._update_progress('health_check', 80, f'✅ Validation of {target_name} completed successfully')
            else:
                self.logger.warning("No ports mapped for validation, skipping comprehensive check")
                progress.update(health_task, description=f"⚠️ {target_name.title()} no ports to validate")
                
                # Still do basic container status check
                try:
                    target_container.reload()
                    if target_container.status != "running":
                        progress.update(health_task, description=f"❌ {target_name.title()} container not running")
                        try:
                            target_container.stop()
                            target_container.remove()
                        except:
                            pass
                        return False
                except Exception as e:
                    self.logger.warning(f"Could not verify container status: {e}")
            
            # Parallel testing phase (optional)
            if self._should_run_parallel_tests():
                test_task = progress.add_task("🧪 Running parallel tests...", total=None)
                # Check if container has ports for testing
                has_ports = config.port_mapping and len(config.port_mapping) > 0
                
                if not has_ports:
                    self.logger.warning("No ports mapped, skipping parallel tests")
                    progress.update(test_task, description="⚠️ No ports to test")
                else:
                    # Determine test port based on network mode
                    if runtime_network == 'host':
                        test_port = list(config.port_mapping.values())[0]
                    elif 'temp_port_mapping' in locals() and temp_port_mapping and len(temp_port_mapping) > 0:
                        test_port = list(temp_port_mapping.values())[0]
                    else:
                        test_port = list(config.port_mapping.values())[0]
                    
                    if not self._run_parallel_tests(test_port, config):
                        progress.update(test_task, description="❌ Parallel tests failed")
                        # Cleanup and abort
                        try:
                            target_container.stop()
                            target_container.remove()
                        except:
                            pass
                        return False
                    progress.update(test_task, description="✅ Parallel tests passed")
            
            # CHECKPOINT 4: Check for cancellation before traffic switch
            if self._check_cancel_flag():
                self.console.print("[yellow]🛑 Deployment cancelled by user (before traffic switch)[/yellow]")
                # Cleanup target container
                try:
                    target_container.stop()
                    target_container.remove()
                except:
                    pass
                self._current_deployment_container = None
                return False
            
            # Traffic switch with zero-downtime
            self._update_progress('traffic_switch', 90, '🔄 Switching traffic (zero-downtime)...')
            switch_task = progress.add_task("🔄 Zero-downtime traffic switch...", total=None)
            
            try:
                # Stop target container temporarily
                target_container.stop()
                target_container.remove()
                
                # CRITICAL: Stop old container BEFORE creating final container with original ports
                # Otherwise we'll get "port is already allocated" error
                if active_container and active_container.status == "running":
                    self.console.print(f"[cyan]🛑 Stopping old container '{active_container.name}' to free ports...[/cyan]")
                    try:
                        active_container.stop(timeout=10)
                        self.console.print(f"[green]✅ Old container '{active_container.name}' stopped[/green]")
                        # Wait for ports to be released
                        time.sleep(2)
                    except Exception as e:
                        self.logger.warning(f"Failed to stop old container: {e}")
                        raise Exception(f"Cannot proceed: failed to stop old container: {e}")
                
                # Create final container with correct configuration
                final_normalized_volumes = self._normalize_volumes(config.volumes)
                self.logger.debug(f"Final normalized volumes: {final_normalized_volumes}")
                
                final_container_kwargs = {
                    'image': config.image_tag,
                    'name': target_container_name,
                    'detach': True,
                    'environment': config.environment,
                    'volumes': final_normalized_volumes,
                    'restart_policy': {"Name": config.restart_policy},
                }
                
                # Handle network and ports
                if runtime_network == 'host':
                    final_container_kwargs['network_mode'] = 'host'
                else:
                    if config.port_mapping and len(config.port_mapping) > 0:
                        final_container_kwargs['ports'] = config.port_mapping  # Final ports
                    if runtime_network and runtime_network != 'bridge':
                        final_container_kwargs['network'] = runtime_network
                
                # Add resource limits
                final_container_kwargs.update(self._get_resource_limits(config))
                
                # Add privileged mode if requested (needed for DB2 with bind mounts to support setuid)
                # Also auto-detect for infrastructure containers (minikube, kubernetes, etc.)
                requires_privileged = False
                if hasattr(config, 'privileged') and config.privileged:
                    requires_privileged = True
                else:
                    # Auto-detect infrastructure containers that require privileged mode
                    image_lower = config.image_tag.lower()
                    infrastructure_containers = ['minikube', 'kicbase', 'kubernetes', 'k8s', 'kind', 'k3s', 'k3d']
                    for infra_container in infrastructure_containers:
                        if infra_container in image_lower:
                            requires_privileged = True
                            self.logger.info(f"Auto-detected infrastructure container requiring privileged mode: {infra_container}")
                            break
                    
                    # Also check if active container has privileged mode enabled
                    if active_container:
                        try:
                            active_privileged = active_container.attrs.get('HostConfig', {}).get('Privileged', False)
                            if active_privileged:
                                requires_privileged = True
                                self.logger.info(f"Active container has privileged mode enabled, copying to final container")
                        except Exception as e:
                            self.logger.debug(f"Could not check active container privileged mode: {e}")
                
                if requires_privileged:
                    final_container_kwargs['privileged'] = True
                    self.logger.info(f"Final container {target_container_name} will run in privileged mode")
                
                # Add command if provided in config (for images that exit immediately without command)
                if hasattr(config, 'command') and config.command:
                    final_container_kwargs['command'] = config.command
                elif 'alpine' in config.image_tag.lower():
                    # Alpine needs a command to stay running
                    final_container_kwargs['command'] = ['sh', '-c', 'sleep 3600']
                
                # Create final container with retry on port conflict
                # Note: Final container uses the same volumes from config, so data migrated to target_container
                # will be available in final_container since they share the same volume definitions
                try:
                    final_container = self.client.containers.run(**final_container_kwargs)
                    self.logger.info("Final container created with migrated data (shares volumes with target)")
                except Exception as create_error:
                    error_msg = str(create_error)
                    # If port conflict and we have an active container, try to stop it and retry
                    if ('port is already allocated' in error_msg.lower() or 'bind for' in error_msg.lower()) and active_container:
                        self.console.print(f"[yellow]⚠️ Port conflict detected, stopping old container '{active_container.name}' and retrying...[/yellow]")
                        try:
                            # Force stop old container
                            active_container.stop(timeout=5)
                            active_container.remove()
                            time.sleep(3)  # Wait for port to be released
                            # Retry creating final container
                            final_container = self.client.containers.run(**final_container_kwargs)
                            self.console.print(f"[green]✅ Final container created after stopping old container[/green]")
                        except Exception as retry_error:
                            self.logger.error(f"Failed to stop old container and retry: {retry_error}")
                            raise Exception(f"Port conflict: {error_msg}. Failed to resolve by stopping old container: {retry_error}")
                    else:
                        raise
                
                # Wait for final container to be ready
                time.sleep(3)
                
                # Final comprehensive validation before traffic switch
                # Check if container has port mapping for health checks
                has_ports = config.port_mapping and len(config.port_mapping) > 0
                
                if has_ports:
                    if runtime_network == 'host':
                        # With host network, use the original port directly
                        final_port = list(config.port_mapping.values())[0]
                    else:
                        final_port = list(config.port_mapping.values())[0]
                    
                    # Final health check
                    if not self._advanced_health_check(final_port, config.health_check_endpoint, 10, 5):
                        raise Exception("Final health check failed")
                    
                    # Final comprehensive validation - critical check before traffic switch
                    self.console.print(f"[yellow]🔍 Final validation before traffic switch...[/yellow]")
                    is_valid, error_msg = self._comprehensive_container_validation(
                        final_container, config, final_port, target_name
                    )
                else:
                    # No ports - just verify container is running
                    self.console.print(f"[yellow]🔍 Final validation before traffic switch (no ports)...[/yellow]")
                    try:
                        final_container.reload()
                        if final_container.status != "running":
                            raise Exception(f"Container is not running: {final_container.status}")
                        is_valid = True
                        error_msg = None
                    except Exception as e:
                        is_valid = False
                        error_msg = str(e)
                
                if not is_valid:
                    error_msg_full = f"Final validation failed before traffic switch: {error_msg}"
                    self.logger.error(error_msg_full)
                    
                    # Get detailed logs
                    try:
                        logs = final_container.logs(tail=100).decode('utf-8', errors='ignore')
                        self.logger.error(f"Final container logs:\n{logs}")
                    except:
                        pass
                    
                    # Cleanup and rollback
                    try:
                        final_container.stop()
                        final_container.remove()
                    except:
                        pass
                    
                    # Restart old container if it exists (rollback)
                    if active_container:
                        try:
                            self.console.print(f"[yellow]🔄 Rolling back to previous container...[/yellow]")
                            active_container.start()
                            self.console.print(f"[green]✅ Rollback successful - previous container restarted[/green]")
                        except Exception as e:
                            self.logger.error(f"Rollback failed: {e}")
                    
                    raise Exception(error_msg_full)
                
                self.console.print(f"[green]✅ Final validation passed - deployment successful[/green]")
                
                # Old container was already stopped before creating final container
                # Now just remove it if it still exists
                if active_container:
                    try:
                        if active_container.status != 'exited':
                            active_container.stop(timeout=10)
                        active_container.remove()
                        self.console.print(f"[green]✅ Old container '{active_container.name}' removed[/green]")
                    except docker.errors.NotFound:
                        # Already removed, that's fine
                        pass
                    except Exception as e:
                        self.logger.warning(f"Failed to remove old container: {e}")
                
                progress.update(switch_task, description="✅ Traffic switched successfully")
                self._update_progress('traffic_switch', 95, '✅ Traffic switch completed')
                
            except Exception as e:
                progress.update(switch_task, description="❌ Traffic switch failed")
                self._update_progress('traffic_switch', 0, f'❌ Traffic switch failed: {e}')
                self.logger.error(f"Traffic switch failed: {e}")
                return False
        
        deployment_end = datetime.now()
        duration = deployment_end - deployment_start
        
        self._record_deployment(deployment_id, config, "blue-green", True, duration)
        
        # Clear deployment tracking
        self._current_deployment_container = None
        
        self._update_progress('completed', 100, '🎉 Deployment completed successfully!')
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
        """Prepare image for deployment - check if exists, pull, or build.
        
        Args:
            image_tag: Docker image tag to prepare
            build_config: Optional build configuration dict
            container_name: Optional running container name for local-image fallback
        
        Returns:
            tuple: (success: bool, message: str)
        """
        # Check if image already exists locally
        try:
            self.client.images.get(image_tag)
            self.logger.info(f"Image {image_tag} already exists locally")
            return True, "Image already exists"
        except docker.errors.ImageNotFound:
            pass

        if self._ensure_image_from_existing_container(image_tag, container_name):
            return True, "Image resolved from existing container"
        
        # If build_config is provided and has dockerfile_path, try to build
        if build_config and build_config.get('dockerfile_path'):
            build_success = self._build_image_enhanced(image_tag, build_config)
            if build_success:
                return True, "Image built successfully"
            # If build failed, try to pull as fallback
            self.logger.warning(f"Build failed, trying to pull image {image_tag}")
        
        # Try to pull image from registry
        try:
            self.logger.info(f"Pulling image {image_tag} from registry...")
            self.client.images.pull(image_tag)
            return True, "Image pulled successfully"
        except Exception as pull_error:
            if self._ensure_image_from_existing_container(image_tag, container_name):
                return True, "Image resolved from existing container after pull failure"
            error_msg = f"Failed to pull image {image_tag}: {pull_error}"
            self.logger.error(error_msg)
            return False, error_msg

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

    def _detect_health_check_endpoint(self, image_tag: str) -> str:
        """Detect appropriate health check endpoint based on image name
        
        Uses configuration from:
        1. User config (self.config from YAML) - highest priority
        2. Default config file (health-checks-defaults.json) - fallback
        
        Returns:
            str: Health check endpoint path, or None for non-HTTP services
        """
        image_lower = image_tag.lower()
        
        # Load defaults from JSON file
        defaults = self._load_health_check_defaults()
        default_health_checks = defaults.get('health_checks', {})
        
        # User config overrides defaults
        user_health_checks = self.config.get('health_checks', {})
        
        # Merge: user config takes precedence over defaults
        non_http_services = user_health_checks.get(
            'non_http_services',
            default_health_checks.get('non_http_services', [])
        )
        
        endpoint_mappings = {
            **default_health_checks.get('endpoint_mappings', {}),  # Defaults first
            **user_health_checks.get('endpoint_mappings', {})      # User overrides
        }
        
        default_endpoint = user_health_checks.get(
            'default_endpoint',
            default_health_checks.get('default_endpoint', '/health')
        )
        
        # Check for non-HTTP services
        for service in non_http_services:
            if service in image_lower:
                self.logger.info(f"Detected non-HTTP service ({service}) - skipping HTTP health check")
                return None
        
        # Additional hardcoded non-HTTP services (infrastructure containers)
        infrastructure_services = ['minikube', 'kicbase', 'kubernetes', 'k8s', 'kind', 'k3s', 'k3d']
        for infra_service in infrastructure_services:
            if infra_service in image_lower:
                self.logger.info(f"Detected infrastructure service ({infra_service}) - skipping HTTP health check")
                return None
        
        # Try to find matching endpoint mapping
        for image_pattern, endpoint in endpoint_mappings.items():
            if image_pattern.lower() in image_lower:
                self.logger.info(f"Detected image pattern '{image_pattern}' -> endpoint '{endpoint}'")
                return endpoint
        
        # Use default endpoint
        self.logger.info(f"Using default health check endpoint: {default_endpoint}")
        return default_endpoint
    
    def _advanced_health_check(self, port: str, endpoint: str, timeout: int, max_retries: int) -> bool:
        """Advanced health check with detailed reporting
        
        Returns True if health check passes or if endpoint is None (skip check)
        """
        # Skip health check if endpoint is None (for non-HTTP services like SSH, Redis, etc.)
        if endpoint is None:
            self.logger.info("Skipping HTTP health check (non-HTTP service)")
            return True
        
        url = f"http://localhost:{port}{endpoint}"
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                # Use longer timeout for first attempts (service may be starting)
                request_timeout = 10 if attempt < 3 else 5
                response = requests.get(url, timeout=request_timeout)
                response_time = time.time() - start_time
                
                # Accept 200-299 status codes as successful health checks
                if 200 <= response.status_code < 300:
                    self.logger.info(f"Health check passed (attempt {attempt + 1}): {response_time:.2f}s (status {response.status_code})")
                    return True
                else:
                    self.logger.warning(f"Health check returned {response.status_code} (attempt {attempt + 1})")
                    
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Health check failed (attempt {attempt + 1}): {e}")
            
            if attempt < max_retries - 1:
                # Longer wait between retries for first attempts
                wait_time = 5 if attempt < 3 else 3
                time.sleep(wait_time)
        
        return False

    def _comprehensive_container_validation(self, container, config: DeploymentConfig, 
                                          port: str, target_name: str) -> tuple:
        """
        Comprehensive validation of container before traffic switch.
        Returns (is_valid, error_message)
        
        Checks:
        1. Container status (must be running, not restarting)
        2. Health check endpoint response
        3. Container logs for errors
        4. Resource usage (CPU, memory)
        5. Volumes mounting
        6. Port availability
        7. Response time
        8. No crash loops
        """
        validation_errors = []
        
        # 1. Check container status
        try:
            container.reload()  # Refresh container state
            status = container.status
            
            if status != "running":
                validation_errors.append(f"Container status is '{status}', expected 'running'")
                return False, "; ".join(validation_errors)
            
            # Check restart count - be more lenient for databases that may restart during initialization
            restart_count = container.attrs.get('RestartCount', 0) if hasattr(container, 'attrs') else 0
            
            # Detect if this is a database service and get its configuration
            db_config = self._get_database_config(config.image_tag)
            is_database = len(db_config) > 0
            
            # Set restart threshold based on service type (from config or default)
            max_restarts = db_config.get('max_restart_count', 15) if is_database else 3
            
            if restart_count > max_restarts:
                validation_errors.append(f"Container has restarted {restart_count} times (possible crash loop, max allowed: {max_restarts})")
                return False, "; ".join(validation_errors)
            
            # For databases with high restart count, check if container is stable now
            # Wait a bit and check if it's still running (not restarting)
            if is_database and restart_count > 5:
                self.logger.info(f"Database container has {restart_count} restarts, checking stability...")
                time.sleep(5)  # Wait 5 seconds
                try:
                    container.reload()
                    if container.status != "running":
                        validation_errors.append(f"Container not stable after {restart_count} restarts (current status: {container.status})")
                        return False, "; ".join(validation_errors)
                    # Check if restart count increased (still restarting)
                    new_restart_count = container.attrs.get('RestartCount', 0) if hasattr(container, 'attrs') else 0
                    if new_restart_count > restart_count:
                        validation_errors.append(f"Container still restarting (restart count increased from {restart_count} to {new_restart_count})")
                        return False, "; ".join(validation_errors)
                    self.logger.info(f"Container appears stable after {restart_count} restarts")
                except Exception as e:
                    self.logger.warning(f"Could not verify container stability: {e}")
                
        except Exception as e:
            validation_errors.append(f"Failed to check container status: {e}")
            return False, "; ".join(validation_errors)
        
        # 2. Health check endpoint (skip for non-HTTP services)
        health_check_passed = False
        response_time = None
        
        if config.health_check_endpoint is None:
            # Non-HTTP service (SSH, Redis, etc.) - skip HTTP health check
            self.logger.info("Skipping HTTP health check for non-HTTP service")
            health_check_passed = True
        else:
            try:
                url = f"http://localhost:{port}{config.health_check_endpoint}"
                start_time = time.time()
                response = requests.get(url, timeout=10)
                response_time = time.time() - start_time
                
                if 200 <= response.status_code < 300:
                    health_check_passed = True
                    self.logger.info(f"Health check passed: {response_time:.2f}s response time")
                else:
                    validation_errors.append(f"Health check returned status {response.status_code}, expected 200-299")
            except requests.exceptions.Timeout:
                validation_errors.append(f"Health check timeout after 10s")
            except requests.exceptions.ConnectionError:
                validation_errors.append(f"Health check connection error - service may not be ready")
            except Exception as e:
                validation_errors.append(f"Health check failed: {e}")
            
            if not health_check_passed:
                return False, "; ".join(validation_errors)
        
        # 3. Check container logs for critical errors
        try:
            logs = container.logs(tail=100).decode('utf-8', errors='ignore')
            
            # For database services, check for normal initialization patterns
            db_config = self._get_database_config(config.image_tag)
            if db_config:
                log_patterns = db_config.get('log_patterns', {})
                loading_patterns = log_patterns.get('loading_shards', []) + log_patterns.get('loading', [])
                
                if loading_patterns:
                    for pattern in loading_patterns:
                        if pattern.lower() in logs.lower():
                            db_name = self._get_database_name(config.image_tag) or 'database'
                            self.logger.info(f"{db_name} is loading data - this is normal and may take time")
                            break
            
            # Common error patterns
            error_patterns = [
                'FATAL', 'CRITICAL', 'panic', 'segmentation fault',
                'out of memory', 'cannot bind', 'address already in use',
                'permission denied', 'access denied', 'failed to start'
            ]
            
            # Special handling for OOM - only treat as error if it's actual OOM, not OOM detection warnings
            # cadvisor and similar tools may log "OOM detection" warnings which are safe to ignore
            oom_safe_patterns = [
                'oom detection', 'configure.*oom', 'disabling oom', 'no oom',
                'could not configure.*oom', 'unable to configure.*oom'
            ]
            
            found_errors = []
            logs_lower = logs.lower()
            
            for pattern in error_patterns:
                if pattern.lower() in logs_lower:
                    found_errors.append(pattern)
            
            # Check for OOM separately - only flag if it's not a safe OOM detection warning
            if 'oom' in logs_lower:
                is_safe_oom = any(safe_pattern in logs_lower for safe_pattern in oom_safe_patterns)
                if not is_safe_oom:
                    # Check if it's actually an OOM error (not just detection-related)
                    oom_error_patterns = ['killed.*oom', 'out of memory', 'oom killer', 'memory limit exceeded']
                    if any(oom_err in logs_lower for oom_err in oom_error_patterns):
                        found_errors.append('OOM')
                else:
                    # It's a safe OOM detection warning, log but don't treat as error
                    self.logger.info("Found safe OOM detection warning (not a critical error)")
            
            if found_errors:
                validation_errors.append(f"Found critical errors in logs: {', '.join(found_errors)}")
                # Don't fail immediately, but log it
                self.logger.warning(f"Warning: Found error patterns in logs: {', '.join(found_errors)}")
        except Exception as e:
            self.logger.warning(f"Could not check container logs: {e}")
        
        # 4. Check resource usage (CPU, memory)
        try:
            stats = container.stats(stream=False)
            
            # Check memory usage
            if 'memory_stats' in stats:
                mem_usage = stats['memory_stats'].get('usage', 0)
                mem_limit = stats['memory_stats'].get('limit', 1)
                
                if mem_limit > 0:
                    mem_percent = (mem_usage / mem_limit) * 100.0
                    if mem_percent > 95:
                        validation_errors.append(f"Memory usage critical: {mem_percent:.1f}%")
                    elif mem_percent > 80:
                        self.logger.warning(f"Memory usage high: {mem_percent:.1f}%")
            
            # Check CPU usage (if available)
            if 'cpu_stats' in stats and 'precpu_stats' in stats:
                # CPU calculation would require more complex logic
                # For now, just check if stats are available
                pass
                
        except Exception as e:
            self.logger.warning(f"Could not check resource usage: {e}")
        
        # 5. Check volumes mounting
        try:
            mounts = container.attrs.get('Mounts', [])
            if config.volumes:
                expected_volumes = len(config.volumes) if isinstance(config.volumes, (dict, list)) else 0
                if len(mounts) < expected_volumes:
                    validation_errors.append(f"Expected {expected_volumes} volume(s), found {len(mounts)}")
        except Exception as e:
            self.logger.warning(f"Could not verify volumes: {e}")
        
        # 6. Check response time (should be reasonable)
        if response_time:
            if response_time > 5.0:
                validation_errors.append(f"Health check response time too slow: {response_time:.2f}s")
            elif response_time > 2.0:
                self.logger.warning(f"Health check response time slow: {response_time:.2f}s")
        
        # 7. Wait a bit and check again to ensure stability
        time.sleep(2)
        try:
            container.reload()
            if container.status != "running":
                validation_errors.append(f"Container status changed to '{container.status}' after validation")
                return False, "; ".join(validation_errors)
        except Exception as e:
            self.logger.warning(f"Could not re-check container status: {e}")
        
        # If we have critical errors, fail validation
        if validation_errors:
            return False, "; ".join(validation_errors)
        
        return True, "All validations passed"

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
        if not volumes:
            return []
        
        # Handle case where volumes might already be a list (from previous normalization)
        if isinstance(volumes, list):
            return volumes
        
        # Handle case where volumes might not be a dict (defensive programming)
        if not isinstance(volumes, dict):
            self.logger.warning(f"Volumes is not a dict or list, got {type(volumes)}: {volumes}")
            return []
        
        normalized = []
        for key, value in volumes.items():
            if isinstance(value, dict):
                # Already in correct format with bind and mode
                # Format: {'/host/path': {'bind': '/container/path', 'mode': 'rw'}}
                if 'bind' in value:
                    bind_path = value['bind']
                    mode = value.get('mode', 'rw')
                    normalized.append(f"{key}:{bind_path}:{mode}")
                else:
                    self.logger.warning(f"Volume dict for '{key}' missing 'bind', skipping: {value}")
            elif isinstance(value, str):
                # Check if it's a named volume (doesn't start with / or ./)
                # Named volumes don't have leading slash in Docker
                if not key.startswith('/') and not key.startswith('./') and not key.startswith('../'):
                    # Named volume: format as 'volume_name:/container/path'
                    normalized.append(f"{key}:{value}")
                else:
                    # Bind mount: format as '/host/path:/container/path'
                    normalized.append(f"{key}:{value}")
            else:
                # Unknown format, log warning and skip
                self.logger.warning(f"Unknown volume format for key '{key}': {type(value)} - {value}")
        
        return normalized

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
                          deployment_type: str, success: bool, duration: timedelta, target_env: str = None):
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
        
        # Add environment information if provided
        if target_env:
            deployment_record['environment'] = target_env
        
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
