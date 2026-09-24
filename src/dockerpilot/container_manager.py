"""Container management operations."""
import docker
import time
from typing import List, Any, Optional
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm

from .container_creation import (
    normalize_volumes as _normalize_volumes_impl,
    run_new_container as _run_new_container_impl,
)
from .container_listing import (
    container_image_label as _container_image_label,
    list_containers as _list_containers_impl,
)




class ContainerManager:
    """Manages Docker container operations."""
    
    def __init__(self, client, console, logger, error_handler):
        """Initialize container manager."""
        self.client = client
        self.console = console
        self.logger = logger
        self._error_handler = error_handler
    
    def list_containers(self, show_all: bool = True, format_output: str = "table") -> List[Any]:
        """Enhanced container listing with multiple output formats."""
        return _list_containers_impl(self, show_all=show_all, format_output=format_output)
    
    def container_operation(self, operation: str, container_name: str, **kwargs) -> bool:
        """Unified container operation handler with progress tracking."""
        operations = {
            'start': self._start_container,
            'stop': self._stop_container,
            'restart': self._restart_container,
            'remove': self._remove_container,
            'pause': self._pause_container,
            'unpause': self._unpause_container,
            'rename': self._rename_container,
        }
        
        if operation not in operations:
            self.console.print(f"[bold red]❌ Unknown operation: {operation}[/bold red]")
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
            console=self.console
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
            except Exception as e:
                progress.update(task, description=f"❌ Failed to {operation} container {container_name}")
                self.logger.error(f"Container {operation} failed: {e}")
                return False
    
    def update_restart_policy(self, container_name: str, policy: str = 'unless-stopped') -> bool:
        """Set restart policy on container."""
        try:
            container = self.client.containers.get(container_name)
            self.console.print(f"[cyan]Updating restart policy for container {container.name} to '{policy}'...[/cyan]")
            container.update(restart_policy={"Name": policy})
            self.console.print(f"[green]Restart policy set to '{policy}'[/green]")
            return True
        except docker.errors.NotFound:
            self.console.print(f"[bold red]Container not found: {container_name}[/bold red]")
            return False
        except docker.errors.APIError as e:
            self.console.print(f"[bold red]Docker API error during update:[/bold red] {e}")
            return False
    
    def rename_container(self, container_name: str, new_name: str) -> bool:
        """Rename a container."""
        return self.container_operation('rename', container_name, new_name=new_name)
    
    def run_new_container(self, image_name: str, name: str, ports: dict = None, 
                         command: str = None, environment: dict = None, 
                         volumes: dict = None, restart_policy: str = 'unless-stopped',
                         network: str = None, privileged: bool = False,
                         cpu_limit: str = None, memory_limit: str = None,
                         **kwargs) -> bool:
        """Run a new container with full configuration options."""
        return _run_new_container_impl(
            self, image_name, name, ports=ports, command=command, environment=environment,
            volumes=volumes, restart_policy=restart_policy, network=network, privileged=privileged,
            cpu_limit=cpu_limit, memory_limit=memory_limit, **kwargs,
        )
    
    def _normalize_volumes(self, volumes):
        """Normalize volumes format for Docker API."""
        return _normalize_volumes_impl(self, volumes)
    
    def _start_container(self, container_name: str, **kwargs) -> bool:
        """Start container with enhanced validation."""
        with self._error_handler("start container", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status == "running":
                self.console.print(f"[yellow]⚠️ Container {container_name} is already running[/yellow]")
                return True
            
            container.start()
            self._wait_for_container_status(container_name, "running", timeout=30)
            self.logger.info(f"Container {container_name} started successfully")
            return True
    
    def _stop_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Stop container with graceful shutdown."""
        with self._error_handler("stop container", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status == "exited":
                self.console.print(f"[yellow]⚠️ Container {container_name} is already stopped[/yellow]")
                return True
            
            container.stop(timeout=timeout)
            self.logger.info(f"Container {container_name} stopped successfully")
            return True
    
    def _restart_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Restart container with health check."""
        with self._error_handler("restart container", container_name):
            container = self.client.containers.get(container_name)
            container.restart(timeout=timeout)
            self._wait_for_container_status(container_name, "running", timeout=30)
            self.logger.info(f"Container {container_name} restarted successfully")
            return True
    
    def _remove_container(self, container_name: str, force: bool = False, **kwargs) -> bool:
        """Remove container with safety checks."""
        with self._error_handler("remove container", container_name):
            container = self.client.containers.get(container_name)
            
            if container.status == "running" and not force:
                if not Confirm.ask(f"Container {container_name} is running. Force removal?"):
                    self.console.print("[yellow]❌ Removal cancelled[/yellow]")
                    return False
            
            container.remove(force=force)
            self.logger.info(f"Container {container_name} removed successfully")
            return True
    
    def _pause_container(self, container_name: str, **kwargs) -> bool:
        """Pause container."""
        with self._error_handler("pause container", container_name):
            container = self.client.containers.get(container_name)
            container.pause()
            self.logger.info(f"Container {container_name} paused successfully")
            return True
    
    def _unpause_container(self, container_name: str, **kwargs) -> bool:
        """Unpause container."""
        with self._error_handler("unpause container", container_name):
            container = self.client.containers.get(container_name)
            container.unpause()
            self.logger.info(f"Container {container_name} unpaused successfully")
            return True
    
    def _rename_container(self, container_name: str, new_name: str, **kwargs) -> bool:
        """Rename container."""
        with self._error_handler("rename container", container_name):
            container = self.client.containers.get(container_name)
            container.rename(new_name)
            self.logger.info(f"Container {container_name} renamed to {new_name} successfully")
            return True
    
    def _wait_for_container_status(self, container_name: str, expected_status: str, timeout: int = 30) -> bool:
        """Wait for container to reach expected status."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                container = self.client.containers.get(container_name)
                if container.status == expected_status:
                    return True
                time.sleep(1)
            except Exception:
                time.sleep(1)
        
        self.logger.warning(f"Container {container_name} did not reach status {expected_status} within {timeout}s")
        return False
    
    def view_container_logs(self, container_names: str = None, tail: int = 50):
        """View container logs. Supports multiple containers separated by comma.
        
        Args:
            container_names: Single container name/ID or comma-separated list of names/IDs
            tail: Number of log lines to show per container
        """
        if container_names:
            # Parse multiple container names if comma-separated
            if ',' in container_names:
                names_list = [name.strip() for name in container_names.split(',') if name.strip()]
            else:
                names_list = [container_names.strip()]
            
            # Show logs for each container
            for container_name in names_list:
                try:
                    container = self.client.containers.get(container_name)
                    logs = container.logs(tail=tail).decode()
                    self.console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
                    self.console.print(f"[cyan]Container: {container_name} - Last {tail} lines[/cyan]")
                    self.console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
                    self.console.print(logs)
                except docker.errors.NotFound:
                    self.console.print(f"[red]Container '{container_name}' not found[/red]")
                except Exception as e:
                    self.console.print(f"[red]Error reading logs for '{container_name}': {e}[/red]")
        else:
            containers = self.client.containers.list(all=True)
            if not containers:
                self.console.print("[red]No containers found[/red]")
                return
            
            self.console.print("\nSelect a container to view logs:")
            for i, c in enumerate(containers, start=1):
                self.console.print(f"{i}. {c.name} ({c.status})")
            
            choice = input("Enter number: ")
            try:
                idx = int(choice) - 1
                container = containers[idx]
                logs = container.logs(tail=tail).decode()
                self.console.print(f"\n[cyan]Showing last {tail} lines of {container.name} logs:[/cyan]\n")
                self.console.print(logs)
            except (ValueError, IndexError):
                self.console.print("[red]Invalid selection[/red]")
    
    def view_container_json(self, container_name: str):
        """Display container information in JSON format."""
        import json
        try:
            container = self.client.containers.get(container_name)
            data = container.attrs
            json_str = json.dumps(data, indent=4, ensure_ascii=False)
            self.console.print(Panel(json_str, title=f"Container JSON: {container_name}", expand=True))
        except docker.errors.NotFound:
            self.console.print(f"[red]Container '{container_name}' not found[/red]")
        except Exception as e:
            self.console.print(f"[red]Error fetching JSON for container '{container_name}': {e}[/red]")

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
