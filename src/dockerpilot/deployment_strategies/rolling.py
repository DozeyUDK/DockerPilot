"""Rolling deployment strategy extracted from DeploymentServiceMixin."""

from datetime import datetime
from typing import Any
import time

import docker
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..deployment_runtime import apply_container_command as _apply_container_command_impl
from ..models import DeploymentConfig


def rolling_deploy(host: Any, config: DeploymentConfig, build_config: dict) -> bool:
    """Enhanced rolling deployment with zero-downtime and full logging"""
    host.console.print(f"\n[bold cyan]🚀 ROLLING DEPLOYMENT STARTED[/bold cyan]")

    deployment_start = datetime.now()
    deployment_id = f"deploy_{int(deployment_start.timestamp())}"
    runtime_network = host._resolve_runtime_network(config.network)

    # Auto-detect health check endpoint based on image type
    detected_endpoint = host._detect_health_check_endpoint(config.image_tag)
    if detected_endpoint != config.health_check_endpoint:
        host.logger.info(f"Auto-detected health check endpoint: {detected_endpoint} (was: {config.health_check_endpoint})")
        config.health_check_endpoint = detected_endpoint

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=host.console
    ) as progress:

        # Phase 1: Prepare image (check, pull, or build)
        build_task = progress.add_task("🔨 Preparing image...", total=None)
        try:
            success, message = host._prepare_image(config.image_tag, build_config, config.container_name)
            if not success:
                progress.update(build_task, description=f"❌ {message}")
                host.console.print(f"[bold red]❌ {message}[/bold red]")
                return False
            progress.update(build_task, description=f"✅ {message}")
        except Exception as e:
            progress.update(build_task, description="❌ Image preparation failed")
            host.logger.error(f"Image preparation failed: {e}")
            host.console.print(f"[bold red]❌ Image preparation failed: {e}[/bold red]")
            return False

        # Phase 2: Check existing container
        health_task = progress.add_task("🔍 Checking existing deployment...", total=None)
        existing_container = None
        try:
            existing_container = host.client.containers.get(config.container_name)
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
                'volumes': host._normalize_volumes(config.volumes),
                'restart_policy': {"Name": config.restart_policy},
                **host._get_resource_limits(config)
            }
            if runtime_network == "host":
                create_kwargs['network_mode'] = "host"
            elif runtime_network:
                create_kwargs['network'] = runtime_network
            _apply_container_command_impl(create_kwargs, config)

            new_container = host.client.containers.create(**create_kwargs)

            # Start container
            try:
                new_container.start()
                progress.update(deploy_task, description="✅ New container started")
            except Exception as e:
                progress.update(deploy_task, description="❌ New container deployment failed")
                host.logger.error(f"Container start failed: {e}")
                try:
                    logs = new_container.logs().decode()
                    host.logger.error(f"Container logs:\n{logs}")
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
                    host.logger.error(f"Container {new_container.name} is not running (status: {new_container.status})")
                    new_container.stop()
                    new_container.remove()
                    return False
            except Exception as e:
                host.logger.warning(f"Could not verify container status: {e}")

        except Exception as e:
            progress.update(deploy_task, description="❌ New container creation failed")
            host.logger.error(f"New container creation failed: {e}")
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
                host.logger.warning(f"Could not verify container status: {e}")

            if not host._advanced_health_check(
                host_port,
                config.health_check_endpoint,
                config.health_check_timeout,
                config.health_check_retries
            ):
                progress.update(health_check_task, description="❌ Health check failed - rolling back")
                try:
                    logs = new_container.logs().decode()
                    host.logger.error(f"Health check failed. Container logs:\n{logs}")
                except Exception as e:
                    host.logger.error(f"Could not fetch logs: {e}")

                # Rollback
                try:
                    new_container.stop()
                    new_container.remove()
                except Exception as e:
                    host.logger.error(f"Rollback failed: {e}")
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
            host.logger.error(f"Traffic switch failed: {e}")
            return False

    # Deployment summary
    deployment_end = datetime.now()
    duration = deployment_end - deployment_start
    host._record_deployment(deployment_id, config, "rolling", True, duration)

    host.console.print(f"\n[bold green]🎉 ROLLING DEPLOYMENT COMPLETED SUCCESSFULLY![/bold green]")
    host.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
    if config.port_mapping:
        port = list(config.port_mapping.values())[0]
        host.console.print(f"[green]Application available at: http://localhost:{port}[/green]")
    else:
        host.console.print(f"[green]Application deployed (no port mapping set)[/green]")

    return True
