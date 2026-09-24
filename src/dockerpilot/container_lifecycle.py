"""Container lifecycle operations extracted from ContainerManager."""

from typing import Any
import time

import docker
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm


def container_operation(host: Any, operation: str, container_name: str, **kwargs) -> bool:
    """Dispatch a lifecycle operation with the existing progress presentation."""
    operations = {
        'start': host._start_container,
        'stop': host._stop_container,
        'restart': host._restart_container,
        'remove': host._remove_container,
        'pause': host._pause_container,
        'unpause': host._unpause_container,
        'rename': host._rename_container,
    }

    if operation not in operations:
        host.console.print(f"[bold red]❌ Unknown operation: {operation}[/bold red]")
        return False

    progress_verbs = {
        "start": "Starting",
        "stop": "Stopping",
        "restart": "Restarting",
        "remove": "Removing",
        "pause": "Pausing",
        "unpause": "Unpausing",
        "rename": "Renaming",
    }
    success_verbs = {
        "start": "started",
        "stop": "stopped",
        "restart": "restarted",
        "remove": "removed",
        "pause": "paused",
        "unpause": "unpaused",
        "rename": "renamed",
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=host.console,
    ) as progress:
        verb_ing = progress_verbs.get(operation, f"{operation.title()}ing")
        verb_past = success_verbs.get(operation, f"{operation}ed")
        task = progress.add_task(f"{verb_ing} container {container_name}...", total=None)

        try:
            result = operations[operation](container_name, **kwargs)
            if result:
                progress.update(task, description=f"✅ Container {container_name} {verb_past} successfully")
            else:
                progress.update(task, description=f"❌ Failed to {operation} container {container_name}")
            return result
        except Exception as exc:
            progress.update(task, description=f"❌ Failed to {operation} container {container_name}")
            host.logger.error(f"Container {operation} failed: {exc}")
            return False


def update_restart_policy(host: Any, container_name: str, policy: str = 'unless-stopped') -> bool:
    """Set restart policy on a container."""
    try:
        container = host.client.containers.get(container_name)
        host.console.print(f"[cyan]Updating restart policy for container {container.name} to '{policy}'...[/cyan]")
        container.update(restart_policy={"Name": policy})
        host.console.print(f"[green]Restart policy set to '{policy}'[/green]")
        return True
    except docker.errors.NotFound:
        host.console.print(f"[bold red]Container not found: {container_name}[/bold red]")
        return False
    except docker.errors.APIError as exc:
        host.console.print(f"[bold red]Docker API error during update:[/bold red] {exc}")
        return False


def rename_container(host: Any, container_name: str, new_name: str) -> bool:
    """Rename a container through the unified operation path."""
    return host.container_operation('rename', container_name, new_name=new_name)


def start_container(host: Any, container_name: str, **kwargs) -> bool:
    """Start container with enhanced validation."""
    with host._error_handler("start container", container_name):
        container = host.client.containers.get(container_name)

        if container.status == "running":
            host.console.print(f"[yellow]⚠️ Container {container_name} is already running[/yellow]")
            return True

        container.start()
        host._wait_for_container_status(container_name, "running", timeout=30)
        host.logger.info(f"Container {container_name} started successfully")
        return True


def stop_container(host: Any, container_name: str, timeout: int = 10, **kwargs) -> bool:
    """Stop container with graceful shutdown."""
    with host._error_handler("stop container", container_name):
        container = host.client.containers.get(container_name)

        if container.status == "exited":
            host.console.print(f"[yellow]⚠️ Container {container_name} is already stopped[/yellow]")
            return True

        container.stop(timeout=timeout)
        host.logger.info(f"Container {container_name} stopped successfully")
        return True


def restart_container(host: Any, container_name: str, timeout: int = 10, **kwargs) -> bool:
    """Restart container with health check."""
    with host._error_handler("restart container", container_name):
        container = host.client.containers.get(container_name)
        container.restart(timeout=timeout)
        host._wait_for_container_status(container_name, "running", timeout=30)
        host.logger.info(f"Container {container_name} restarted successfully")
        return True


def remove_container(host: Any, container_name: str, force: bool = False, **kwargs) -> bool:
    """Remove container with safety checks."""
    with host._error_handler("remove container", container_name):
        container = host.client.containers.get(container_name)

        if container.status == "running" and not force:
            if not Confirm.ask(f"Container {container_name} is running. Force removal?"):
                host.console.print("[yellow]❌ Removal cancelled[/yellow]")
                return False

        container.remove(force=force)
        host.logger.info(f"Container {container_name} removed successfully")
        return True


def pause_container(host: Any, container_name: str, **kwargs) -> bool:
    """Pause container."""
    with host._error_handler("pause container", container_name):
        container = host.client.containers.get(container_name)
        container.pause()
        host.logger.info(f"Container {container_name} paused successfully")
        return True


def unpause_container(host: Any, container_name: str, **kwargs) -> bool:
    """Unpause container."""
    with host._error_handler("unpause container", container_name):
        container = host.client.containers.get(container_name)
        container.unpause()
        host.logger.info(f"Container {container_name} unpaused successfully")
        return True


def rename_container_internal(host: Any, container_name: str, new_name: str, **kwargs) -> bool:
    """Rename container implementation used by the operation dispatcher."""
    with host._error_handler("rename container", container_name):
        container = host.client.containers.get(container_name)
        container.rename(new_name)
        host.logger.info(f"Container {container_name} renamed to {new_name} successfully")
        return True


def wait_for_container_status(host: Any, container_name: str, expected_status: str, timeout: int = 30) -> bool:
    """Wait for a container to reach the expected status."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            container = host.client.containers.get(container_name)
            if container.status == expected_status:
                return True
            time.sleep(1)
        except Exception:
            time.sleep(1)

    host.logger.warning(f"Container {container_name} did not reach status {expected_status} within {timeout}s")
    return False


def stop_and_remove_container(host: Any, container_name: str, timeout: int = 10) -> bool:
    """Stop and remove a container in one operation."""
    with host._error_handler(f"stop and remove {container_name}", container_name):
        container = host.client.containers.get(container_name)

        host.console.print(f"[cyan]🛑 Stopping container {container_name}...[/cyan]")
        if container.status == "running":
            container.stop(timeout=timeout)
            host.console.print("[green]✅ Container stopped[/green]")
        else:
            host.console.print("[yellow]ℹ️ Container was not running[/yellow]")

        host.console.print(f"[cyan]🗑️ Removing container {container_name}...[/cyan]")
        container.remove()
        host.console.print(f"[green]✅ Container {container_name} removed[/green]")

        host.logger.info(f"Container {container_name} stopped and removed")
        return True

    return False
