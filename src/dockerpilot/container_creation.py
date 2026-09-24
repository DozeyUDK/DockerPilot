"""Container creation and configuration helpers."""

from typing import Any

import docker


def normalize_volumes(host: Any, volumes):
    """Normalize supported volume formats for the Docker API."""
    if not volumes:
        return []

    if isinstance(volumes, list):
        return volumes

    if not isinstance(volumes, dict):
        host.logger.warning(f"Volumes is not a dict or list, got {type(volumes)}")
        return []

    normalized = []
    for key, value in volumes.items():
        if isinstance(value, dict):
            if 'bind' in value:
                bind_path = value['bind']
                mode = value.get('mode', 'rw')
                normalized.append(f"{key}:{bind_path}:{mode}")
            else:
                host.logger.warning(f"Volume dict for '{key}' missing 'bind', skipping")
        elif isinstance(value, str):
            normalized.append(f"{key}:{value}")
        else:
            host.logger.warning(f"Unknown volume format for key '{key}': {type(value)}")

    return normalized


def run_new_container(
    host: Any,
    image_name: str,
    name: str,
    ports: dict = None,
    command: str = None,
    environment: dict = None,
    volumes: dict = None,
    restart_policy: str = 'unless-stopped',
    network: str = None,
    privileged: bool = False,
    cpu_limit: str = None,
    memory_limit: str = None,
    **kwargs,
) -> bool:
    """Run a new container with the existing configuration semantics."""
    try:
        host.console.print(f"[cyan]🚀 Starting new container '{name}' from image '{image_name}'...[/cyan]")

        container_kwargs = {
            'image': image_name,
            'name': name,
            'detach': True,
        }

        if ports:
            container_kwargs['ports'] = ports
            host.console.print(f"[dim]  Ports: {ports}[/dim]")

        if command:
            container_kwargs['command'] = command
            host.console.print(f"[dim]  Command: {command}[/dim]")

        if environment:
            container_kwargs['environment'] = environment
            env_count = len(environment)
            host.console.print(f"[dim]  Environment variables: {env_count} set[/dim]")

        if volumes:
            normalized_volumes = host._normalize_volumes(volumes)
            container_kwargs['volumes'] = normalized_volumes
            vol_count = len(normalized_volumes)
            host.console.print(f"[dim]  Volumes: {vol_count} mounted[/dim]")

        container_kwargs['restart_policy'] = {"Name": restart_policy}
        host.console.print(f"[dim]  Restart policy: {restart_policy}[/dim]")

        if network:
            if network == 'host':
                container_kwargs['network_mode'] = 'host'
            else:
                container_kwargs['network'] = network
            host.console.print(f"[dim]  Network: {network}[/dim]")

        if privileged:
            container_kwargs['privileged'] = True
            host.console.print("[dim]  Privileged mode: enabled[/dim]")

        resource_limits = {}
        if cpu_limit:
            try:
                cpu_limit_nano = float(cpu_limit) * 1000000000
                resource_limits['nano_cpus'] = int(cpu_limit_nano)
                host.console.print(f"[dim]  CPU limit: {cpu_limit}[/dim]")
            except ValueError:
                host.logger.warning(f"Invalid CPU limit format: {cpu_limit}")

        if memory_limit:
            try:
                memory_str = memory_limit.lower()
                if memory_str.endswith('g'):
                    memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024 * 1024)
                elif memory_str.endswith('m'):
                    memory_bytes = int(float(memory_str[:-1]) * 1024 * 1024)
                else:
                    memory_bytes = int(memory_str)
                resource_limits['mem_limit'] = memory_bytes
                host.console.print(f"[dim]  Memory limit: {memory_limit}[/dim]")
            except ValueError:
                host.logger.warning(f"Invalid memory limit format: {memory_limit}")

        if resource_limits:
            container_kwargs.update(resource_limits)

        container = host.client.containers.run(**container_kwargs)

        host.console.print(f"[green]✅ Container '{name}' started successfully (ID: {container.short_id})[/green]")
        host.logger.info(f"Container {name} started from image {image_name}")
        return True

    except docker.errors.ImageNotFound:
        host.console.print(f"[bold red]❌ Image not found: {image_name}[/bold red]")
        host.logger.error(f"Image not found: {image_name}")
        return False
    except docker.errors.APIError as exc:
        error_msg = str(exc)
        host.console.print(f"[bold red]❌ Docker API error:[/bold red] {error_msg}")
        host.logger.error(f"Docker API error: {exc}")
        return False
    except Exception as exc:
        host.console.print(f"[bold red]❌ Failed to start container: {exc}[/bold red]")
        host.logger.error(f"Container start failed: {exc}")
        return False
