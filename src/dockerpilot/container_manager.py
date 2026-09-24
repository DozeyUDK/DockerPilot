"""Container management operations."""
from typing import List, Any, Optional

from .container_creation import (
    normalize_volumes as _normalize_volumes_impl,
    run_new_container as _run_new_container_impl,
)
from .container_inspection import (
    view_container_json as _view_container_json_impl,
    view_container_logs as _view_container_logs_impl,
)
from .container_lifecycle import (
    container_operation as _container_operation_impl,
    pause_container as _pause_container_impl,
    remove_container as _remove_container_impl,
    rename_container as _rename_container_public_impl,
    rename_container_internal as _rename_container_internal_impl,
    restart_container as _restart_container_impl,
    start_container as _start_container_impl,
    stop_and_remove_container as _stop_and_remove_container_impl,
    stop_container as _stop_container_impl,
    unpause_container as _unpause_container_impl,
    update_restart_policy as _update_restart_policy_impl,
    wait_for_container_status as _wait_for_container_status_impl,
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
        return _container_operation_impl(self, operation, container_name, **kwargs)
    
    def update_restart_policy(self, container_name: str, policy: str = 'unless-stopped') -> bool:
        """Set restart policy on container."""
        return _update_restart_policy_impl(self, container_name, policy)
    
    def rename_container(self, container_name: str, new_name: str) -> bool:
        """Rename a container."""
        return _rename_container_public_impl(self, container_name, new_name)
    
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
        return _start_container_impl(self, container_name, **kwargs)
    
    def _stop_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Stop container with graceful shutdown."""
        return _stop_container_impl(self, container_name, timeout=timeout, **kwargs)
    
    def _restart_container(self, container_name: str, timeout: int = 10, **kwargs) -> bool:
        """Restart container with health check."""
        return _restart_container_impl(self, container_name, timeout=timeout, **kwargs)
    
    def _remove_container(self, container_name: str, force: bool = False, **kwargs) -> bool:
        """Remove container with safety checks."""
        return _remove_container_impl(self, container_name, force=force, **kwargs)
    
    def _pause_container(self, container_name: str, **kwargs) -> bool:
        """Pause container."""
        return _pause_container_impl(self, container_name, **kwargs)
    
    def _unpause_container(self, container_name: str, **kwargs) -> bool:
        """Unpause container."""
        return _unpause_container_impl(self, container_name, **kwargs)
    
    def _rename_container(self, container_name: str, new_name: str, **kwargs) -> bool:
        """Rename container."""
        return _rename_container_internal_impl(self, container_name, new_name, **kwargs)
    
    def _wait_for_container_status(self, container_name: str, expected_status: str, timeout: int = 30) -> bool:
        """Wait for container to reach expected status."""
        return _wait_for_container_status_impl(self, container_name, expected_status, timeout)
    
    def view_container_logs(self, container_names: str = None, tail: int = 50):
        """View container logs. Supports multiple containers separated by comma."""
        return _view_container_logs_impl(self, container_names, tail)
    
    def view_container_json(self, container_name: str):
        """Display container information in JSON format."""
        return _view_container_json_impl(self, container_name)

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
        return _stop_and_remove_container_impl(self, container_name, timeout)

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
