"""Container management operations."""
import docker
import time
from typing import List, Any, Optional
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm

from .utils import format_ports, get_container_size, calculate_uptime


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
        with self._error_handler("list containers"):
            containers = self.client.containers.list(all=show_all)
            
            if format_output == "json":
                container_data = []
                for c in containers:
                    container_data.append({
                        'id': c.short_id,
                        'name': c.name,
                        'status': c.status,
                        'image': c.image.tags[0] if c.image.tags else "none",
                        'ports': c.ports,
                        'created': c.attrs['Created'],
                        'size': get_container_size(c)
                    })
                self.console.print_json(data=container_data)
                return containers
            
            # Enhanced table view
            table = Table(title="🐳 Docker Containers", show_header=True, header_style="bold blue")
            table.add_column("Nr", style="bold blue", width=4)
            table.add_column("ID", style="cyan", width=12)
            table.add_column("Name", style="green", width=20)
            table.add_column("Status", style="magenta", width=12)
            table.add_column("Image", style="yellow", width=25)
            table.add_column("Ports", style="bright_blue", width=20)
            table.add_column("Size", style="white", width=10)
            table.add_column("Uptime", style="bright_green", width=15)

            for idx, c in enumerate(containers, start=1):
                # Status formatting
                status_color = "green" if c.status == "running" else "red" if c.status == "exited" else "yellow"
                status = f"[{status_color}]{c.status}[/{status_color}]"
                
                # Ports formatting
                ports = format_ports(c.ports)
                
                # Size calculation
                size = get_container_size(c)
                
                # Uptime calculation
                uptime = calculate_uptime(c)
                
                table.add_row(
                    str(idx),
                    c.short_id,
                    c.name,
                    status,
                    c.image.tags[0] if c.image.tags else "❌ none",
                    ports,
                    size,
                    uptime
                )
            
            self.console.print(table)
            
            # Summary statistics
            running = len([c for c in containers if c.status == "running"])
            stopped = len([c for c in containers if c.status == "exited"])
            total = len(containers)
            
            summary = f"📊 Summary: {total} total, {running} running, {stopped} stopped"
            self.console.print(Panel(summary, style="bright_blue"))
            
            return containers
    
    def container_operation(self, operation: str, container_name: str, **kwargs) -> bool:
        """Unified container operation handler with progress tracking."""
        operations = {
            'start': self._start_container,
            'stop': self._stop_container,
            'restart': self._restart_container,
            'remove': self._remove_container,
            'pause': self._pause_container,
            'unpause': self._unpause_container,
        }
        
        if operation not in operations:
            self.console.print(f"[bold red]❌ Unknown operation: {operation}[/bold red]")
            return False
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            task = progress.add_task(f"{operation.title()}ing container {container_name}...", total=None)
            
            try:
                result = operations[operation](container_name, **kwargs)
                progress.update(task, description=f"✅ Container {container_name} {operation}ed successfully")
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
    
    def run_new_container(self, image_name: str, name: str, ports: dict = None, 
                         command: str = None, **kwargs) -> bool:
        """Run a new container."""
        try:
            self.console.print(f"[cyan]Starting new container {name} from image {image_name}...[/cyan]")
            self.client.containers.run(image_name, name=name, detach=True, ports=ports, command=command)
            self.console.print(f"[green]Container {name} started[/green]")
            return True
        except docker.errors.ImageNotFound:
            self.console.print(f"[bold red]Image not found: {image_name}[/bold red]")
            return False
        except docker.errors.APIError as e:
            self.console.print(f"[bold red]Docker API error:[/bold red] {e}")
            return False
    
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
    
    def view_container_logs(self, container_name: str = None, tail: int = 50):
        """View container logs."""
        if container_name:
            try:
                container = self.client.containers.get(container_name)
                logs = container.logs(tail=tail).decode()
                self.console.print(f"\n[cyan]Showing last {tail} lines of {container_name} logs:[/cyan]\n")
                self.console.print(logs)
            except docker.errors.NotFound:
                self.console.print(f"[red]Container '{container_name}' not found[/red]")
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

