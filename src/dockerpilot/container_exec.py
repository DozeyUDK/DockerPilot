"""Interactive and non-interactive container command execution."""

from typing import Any
import subprocess


def exec_container(host: Any, container_name: str, command: str = "/bin/bash") -> bool:
    """Execute an interactive command in a running container."""
    with host._error_handler(f"exec into container {container_name}", container_name):
        container = host.client.containers.get(container_name)
        if container.status != 'running':
            host.console.print(
                f"[bold red]❌ Container '{container_name}' is not running (status: {container.status})[/bold red]"
            )
            return False

        host.logger.info(f"Executing interactive command in container {container_name}: {command}")
        host.console.print(f"[cyan]📟 Executing '{command}' in container '{container_name}'...[/cyan]")
        host.console.print("[dim]Type 'exit' to leave the container shell[/dim]\n")

        try:
            result = subprocess.run(
                ['docker', 'exec', '-it', container_name, command],
                check=False,
            )

            if result.returncode == 0:
                host.console.print(f"\n[green]✅ Exited from container '{container_name}'[/green]")
                return True

            host.console.print(f"\n[yellow]⚠️ Exec command exited with code {result.returncode}[/yellow]")
            return False

        except FileNotFoundError:
            host.console.print(
                "[bold red]❌ Docker CLI not found. Please ensure Docker is installed and in PATH.[/bold red]"
            )
            return False
        except Exception as exc:
            host.console.print(f"[bold red]❌ Failed to execute command: {exc}[/bold red]")
            host.logger.error(f"Exec failed: {exc}")
            return False

    return False


def exec_command_non_interactive(host: Any, container_name: str, command: str) -> bool:
    """Execute a command in a container through the Docker SDK."""
    with host._error_handler(f"exec command in {container_name}", container_name):
        container = host.client.containers.get(container_name)

        if container.status != 'running':
            host.console.print(f"[red]❌ Container '{container_name}' is not running[/red]")
            return False

        host.console.print(f"[cyan]⚙️ Executing: {command}[/cyan]")
        exec_log = container.exec_run(command)

        output = exec_log.output.decode()
        host.console.print(output)

        if exec_log.exit_code == 0:
            host.console.print("[green]✅ Command executed successfully[/green]")
            return True

        host.console.print(f"[yellow]⚠️ Command exited with code {exec_log.exit_code}[/yellow]")
        return False

    return False
