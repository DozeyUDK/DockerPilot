"""Quick deployment strategy extracted from DeploymentServiceMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any
import time

import docker
import yaml
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..models import DeploymentConfig


def quick_deploy(
    host: Any,
    dockerfile_path: str = ".",
    image_tag: str = None,
    container_name: str = None,
    port_mapping: dict = None,
    environment: dict = None,
    volumes: dict = None,
    yaml_config: str = None,
    cleanup_old_image: bool = True,
) -> bool:
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
    host.console.print(f"\n[bold cyan]⚡ QUICK DEPLOY STARTED[/bold cyan]")

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

            host.console.print(f"[cyan]✓ Loaded configuration from {yaml_config}[/cyan]")
        except Exception as e:
            host.logger.error(f"Failed to load YAML config: {e}")
            host.console.print(f"[yellow]⚠️ Could not load YAML config, using provided parameters[/yellow]")

    # Validate required parameters
    if not image_tag or not container_name:
        host.console.print("[red]❌ image_tag and container_name are required[/red]")
        return False

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=host.console
    ) as progress:

        # Step 1: Get old container info (for image cleanup)
        check_task = progress.add_task("🔍 Checking existing deployment...", total=None)
        try:
            old_container = host.client.containers.get(container_name)
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
                host.console.print(f"[red]❌ Dockerfile not found at {dockerfile}[/red]")
                return False

            image, build_logs = host.client.images.build(
                path=dockerfile_path,
                tag=image_tag,
                rm=True,
                pull=True
            )
            progress.update(build_task, description=f"✅ Image {image_tag} built successfully")

        except docker.errors.BuildError as e:
            progress.update(build_task, description="❌ Build failed")
            host.logger.error(f"Build error: {e}")
            for log in e.build_log:
                if 'stream' in log:
                    host.console.print(f"[red]{log['stream']}[/red]", end="")
            return False
        except Exception as e:
            progress.update(build_task, description="❌ Build failed")
            host.logger.error(f"Unexpected build error: {e}")
            return False

        # Step 3: Stop old container
        stop_task = progress.add_task("🛑 Stopping old container...", total=None)
        try:
            old_container = host.client.containers.get(container_name)
            if old_container.status == "running":
                old_container.stop(timeout=10)
                progress.update(stop_task, description="✅ Old container stopped")
            else:
                progress.update(stop_task, description="ℹ️ Container was not running")
        except docker.errors.NotFound:
            progress.update(stop_task, description="ℹ️ No container to stop")
        except Exception as e:
            progress.update(stop_task, description="❌ Failed to stop container")
            host.logger.error(f"Stop failed: {e}")
            return False

        # Step 4: Remove old container
        remove_task = progress.add_task("🗑️ Removing old container...", total=None)
        try:
            old_container = host.client.containers.get(container_name)
            old_container.remove()
            progress.update(remove_task, description="✅ Old container removed")
        except docker.errors.NotFound:
            progress.update(remove_task, description="ℹ️ No container to remove")
        except Exception as e:
            progress.update(remove_task, description="❌ Failed to remove container")
            host.logger.error(f"Remove failed: {e}")
            # Continue anyway

        # Step 5: Remove old image (if requested and exists)
        if cleanup_old_image and old_image_id:
            cleanup_task = progress.add_task("🧹 Cleaning up old image...", total=None)
            try:
                # Check if old image is different from new one
                new_image = host.client.images.get(image_tag)
                if old_image_id != new_image.id:
                    # Check if any other containers are using the old image
                    containers_using_image = host.client.containers.list(
                        all=True,
                        filters={"ancestor": old_image_id}
                    )

                    if len(containers_using_image) == 0:
                        try:
                            host.client.images.remove(old_image_id, force=False)
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
                host.logger.warning(f"Image cleanup failed: {e}")

        # Step 6: Run new container
        run_task = progress.add_task("🚀 Starting new container...", total=None)
        try:
            new_container = host.client.containers.run(
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
            host.logger.error(f"Container start failed: {e}")
            return False

        # Step 7: Optional health check
        if port_mapping:
            health_task = progress.add_task("🩺 Health check...", total=None)
            host_port = list(port_mapping.values())[0]

            if host._advanced_health_check(host_port, "/", timeout=10, max_retries=3):
                progress.update(health_task, description="✅ Health check passed")
            else:
                progress.update(health_task, description="⚠️ Health check failed (container still running)")
                host.logger.warning("Health check failed but deployment completed")

    # Deployment summary
    deployment_end = datetime.now()
    duration = deployment_end - deployment_start

    host.console.print(f"\n[bold green]🎉 QUICK DEPLOY COMPLETED SUCCESSFULLY![/bold green]")
    host.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")
    host.console.print(f"[green]Container: {container_name}[/green]")
    host.console.print(f"[green]Image: {image_tag}[/green]")

    if port_mapping:
        for container_port, host_port in port_mapping.items():
            host.console.print(f"[green]Available at: http://localhost:{host_port}[/green]")

    # Record deployment
    host._record_deployment(
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
