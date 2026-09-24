"""Canary deployment strategy extracted from DeploymentServiceMixin."""

from datetime import datetime
from typing import Any
import time

import docker
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..deployment_runtime import offset_port_mapping as _offset_port_mapping_impl
from ..models import DeploymentConfig


def canary_deploy(host: Any, config: DeploymentConfig, build_config: dict) -> bool:
    """Canary deployment with gradual traffic shifting"""
    host.console.print(f"\n[bold cyan]🐤 CANARY DEPLOYMENT STARTED[/bold cyan]")

    # This would require a load balancer integration
    # For now, we'll implement a simplified version

    deployment_start = datetime.now()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:

        # Prepare image
        build_task = progress.add_task("🔨 Preparing canary image...", total=None)
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

        # Deploy canary container (5% traffic simulation)
        canary_name = f"{config.container_name}_canary"
        canary_task = progress.add_task("🚀 Deploying canary (5% traffic)...", total=None)

        # Use different port for canary
        canary_port_mapping = _offset_port_mapping_impl(config.port_mapping, 100)

        try:
            # Clean existing canary
            try:
                old_canary = host.client.containers.get(canary_name)
                old_canary.stop()
                old_canary.remove()
            except docker.errors.NotFound:
                pass

            canary_container = host.client.containers.run(
                image=config.image_tag,
                name=canary_name,
                detach=True,
                ports=canary_port_mapping,
                environment={**config.environment, "CANARY": "true"},
                volumes=host._normalize_volumes(config.volumes),
                restart_policy={"Name": config.restart_policy},
                **host._get_resource_limits(config)
            )

            progress.update(canary_task, description="✅ Canary deployed")
            time.sleep(5)

        except Exception as e:
            progress.update(canary_task, description="❌ Canary deployment failed")
            return False

        # Monitor canary
        monitor_task = progress.add_task("📊 Monitoring canary performance...", total=None)

        canary_port = list(canary_port_mapping.values())[0]
        if not host._monitor_canary_performance(canary_port, duration=30):
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
                main_container = host.client.containers.get(config.container_name)
                main_container.stop()
                main_container.remove()
            except docker.errors.NotFound:
                pass

            # Stop canary and redeploy as main
            canary_container.stop()
            canary_container.remove()

            # Deploy as main container
            main_container = host.client.containers.run(
                image=config.image_tag,
                name=config.container_name,
                detach=True,
                ports=config.port_mapping,
                environment=config.environment,
                volumes=host._normalize_volumes(config.volumes),
                restart_policy={"Name": config.restart_policy},
                **host._get_resource_limits(config)
            )

            progress.update(promote_task, description="✅ Canary promoted successfully")

        except Exception as e:
            progress.update(promote_task, description="❌ Canary promotion failed")
            return False

    deployment_end = datetime.now()
    duration = deployment_end - deployment_start

    host._record_deployment(f"canary_{int(deployment_start.timestamp())}", config, "canary", True, duration)

    host.console.print(f"\n[bold green]🎉 CANARY DEPLOYMENT COMPLETED![/bold green]")
    host.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")

    return True
