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
                    # Get state from container attributes
                    state = c.attrs.get('State', {}).get('Status', c.status).lower()
                    container_data.append({
                        'id': c.short_id,
                        'name': c.name,
                        'status': c.status,
                        'state': state,
                        'image': c.image.tags[0] if c.image.tags else "none",
                        'ports': c.ports,
                        'created': c.attrs['Created'],
                        'size': get_container_size(c)
                    })
                # Don't print JSON in API context, just return data
                # self.console.print_json(data=container_data)
                return container_data
            
            # Enhanced table view with auto-scaling to terminal width
            # Get terminal width for dynamic column sizing
            terminal_width = self.console.width if hasattr(self.console, 'width') else 120
            # Reserve space for borders and padding (approximately 8 characters per column)
            available_width = max(80, terminal_width - 20)  # Minimum 80 chars, reserve 20 for borders
            
            # Calculate proportional widths based on content importance
            # Priority: Name > Image > Ports > Status > ID > Uptime > Size > Nr
            table = Table(
                title="🐳 Docker Containers", 
                show_header=True, 
                header_style="bold blue",
                expand=True,  # Allow table to expand to terminal width
                show_lines=False  # Disable lines for better space usage
            )
            
            # Track if Size and Uptime columns were added
            include_size_uptime = available_width >= 100
            
            # Use proportional widths that adapt to terminal size
            # For smaller terminals, some columns will be narrower
            if available_width >= 140:
                # Large terminal - full width columns
                table.add_column("Nr", style="bold blue", width=4, overflow="fold")
                table.add_column("ID", style="cyan", width=12, overflow="fold")
                table.add_column("Name", style="green", width=min(25, int(available_width * 0.15)), overflow="fold")
                table.add_column("Status", style="magenta", width=10, overflow="fold")
                table.add_column("Image", style="yellow", width=min(30, int(available_width * 0.20)), overflow="fold")
                table.add_column("Ports", style="bright_blue", width=min(30, int(available_width * 0.20)), overflow="fold")
                table.add_column("Size", style="white", width=10, overflow="fold")
                table.add_column("Uptime", style="bright_green", width=12, overflow="fold")
            elif available_width >= 100:
                # Medium terminal - reduce some columns
                table.add_column("Nr", style="bold blue", width=3, overflow="fold")
                table.add_column("ID", style="cyan", width=10, overflow="fold")
                table.add_column("Name", style="green", width=min(20, int(available_width * 0.18)), overflow="fold")
                table.add_column("Status", style="magenta", width=8, overflow="fold")
                table.add_column("Image", style="yellow", width=min(25, int(available_width * 0.22)), overflow="fold")
                table.add_column("Ports", style="bright_blue", width=min(25, int(available_width * 0.22)), overflow="fold")
                table.add_column("Size", style="white", width=8, overflow="fold")
                table.add_column("Uptime", style="bright_green", width=10, overflow="fold")
            else:
                # Small terminal - minimal columns, remove less critical ones
                table.add_column("Nr", style="bold blue", width=3, overflow="fold")
                table.add_column("ID", style="cyan", width=8, overflow="fold")
                table.add_column("Name", style="green", width=min(18, int(available_width * 0.25)), overflow="fold")
                table.add_column("Status", style="magenta", width=7, overflow="fold")
                table.add_column("Image", style="yellow", width=min(20, int(available_width * 0.30)), overflow="fold")
                table.add_column("Ports", style="bright_blue", width=min(20, int(available_width * 0.30)), overflow="fold")
                # Remove Size and Uptime for very small terminals to save space

            for idx, c in enumerate(containers, start=1):
                # Status formatting
                status_color = "green" if c.status == "running" else "red" if c.status == "exited" else "yellow"
                status = f"[{status_color}]{c.status}[/{status_color}]"
                
                # Ports formatting
                ports = format_ports(c.ports)
                
                # Build row data
                row_data = [
                    str(idx),
                    c.short_id,
                    c.name,
                    status,
                    c.image.tags[0] if c.image.tags else "❌ none",
                    ports
                ]
                
                # Add Size and Uptime only if columns exist
                if include_size_uptime:
                    size = get_container_size(c)
                    uptime = calculate_uptime(c)
                    row_data.extend([size, uptime])
                
                table.add_row(*row_data)
            
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
                         command: str = None, environment: dict = None, 
                         volumes: dict = None, restart_policy: str = 'unless-stopped',
                         network: str = None, privileged: bool = False,
                         cpu_limit: str = None, memory_limit: str = None,
                         **kwargs) -> bool:
        """Run a new container with full configuration options.
        
        Args:
            image_name: Docker image name/tag
            name: Container name
            ports: Port mapping dict (e.g., {'80': '8080'})
            command: Command to run in container
            environment: Environment variables dict
            volumes: Volume mappings dict (supports both formats)
            restart_policy: Restart policy (no, on-failure, always, unless-stopped)
            network: Network name or 'host' for host network
            privileged: Run container in privileged mode
            cpu_limit: CPU limit (e.g., '1.5' for 1.5 CPUs)
            memory_limit: Memory limit (e.g., '1g' for 1GB)
        """
        try:
            self.console.print(f"[cyan]🚀 Starting new container '{name}' from image '{image_name}'...[/cyan]")
            
            # Prepare container creation parameters
            container_kwargs = {
                'image': image_name,
                'name': name,
                'detach': True,
            }
            
            # Add ports if provided
            if ports:
                container_kwargs['ports'] = ports
                self.console.print(f"[dim]  Ports: {ports}[/dim]")
            
            # Add command if provided
            if command:
                container_kwargs['command'] = command
                self.console.print(f"[dim]  Command: {command}[/dim]")
            
            # Add environment variables if provided
            if environment:
                container_kwargs['environment'] = environment
                env_count = len(environment)
                self.console.print(f"[dim]  Environment variables: {env_count} set[/dim]")
            
            # Add volumes if provided
            if volumes:
                # Normalize volumes format
                normalized_volumes = self._normalize_volumes(volumes)
                container_kwargs['volumes'] = normalized_volumes
                vol_count = len(normalized_volumes)
                self.console.print(f"[dim]  Volumes: {vol_count} mounted[/dim]")
            
            # Add restart policy
            container_kwargs['restart_policy'] = {"Name": restart_policy}
            self.console.print(f"[dim]  Restart policy: {restart_policy}[/dim]")
            
            # Add network if specified
            if network:
                if network == 'host':
                    container_kwargs['network_mode'] = 'host'
                else:
                    container_kwargs['network'] = network
                self.console.print(f"[dim]  Network: {network}[/dim]")
            
            # Add privileged mode if requested
            if privileged:
                container_kwargs['privileged'] = True
                self.console.print(f"[dim]  Privileged mode: enabled[/dim]")
            
            # Add resource limits
            resource_limits = {}
            if cpu_limit:
                try:
                    cpu_limit_nano = float(cpu_limit) * 1000000000
                    resource_limits['nano_cpus'] = int(cpu_limit_nano)
                    self.console.print(f"[dim]  CPU limit: {cpu_limit}[/dim]")
                except ValueError:
                    self.logger.warning(f"Invalid CPU limit format: {cpu_limit}")
            
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
                    self.console.print(f"[dim]  Memory limit: {memory_limit}[/dim]")
                except ValueError:
                    self.logger.warning(f"Invalid memory limit format: {memory_limit}")
            
            if resource_limits:
                container_kwargs.update(resource_limits)
            
            # Create and start container
            container = self.client.containers.run(**container_kwargs)
            
            self.console.print(f"[green]✅ Container '{name}' started successfully (ID: {container.short_id})[/green]")
            self.logger.info(f"Container {name} started from image {image_name}")
            return True
            
        except docker.errors.ImageNotFound:
            self.console.print(f"[bold red]❌ Image not found: {image_name}[/bold red]")
            self.logger.error(f"Image not found: {image_name}")
            return False
        except docker.errors.APIError as e:
            error_msg = str(e)
            self.console.print(f"[bold red]❌ Docker API error:[/bold red] {error_msg}")
            self.logger.error(f"Docker API error: {e}")
            return False
        except Exception as e:
            self.console.print(f"[bold red]❌ Failed to start container: {e}[/bold red]")
            self.logger.error(f"Container start failed: {e}")
            return False
    
    def _normalize_volumes(self, volumes):
        """Normalize volumes format for Docker API.
        
        Supports multiple formats:
        - Dict with bind/mode: {'/host/path': {'bind': '/container/path', 'mode': 'rw'}}
        - Simple dict: {'/host/path': '/container/path'}
        - Named volumes: {'volume_name': '/container/path'}
        """
        if not volumes:
            return []
        
        if isinstance(volumes, list):
            return volumes
        
        if not isinstance(volumes, dict):
            self.logger.warning(f"Volumes is not a dict or list, got {type(volumes)}")
            return []
        
        normalized = []
        for key, value in volumes.items():
            if isinstance(value, dict):
                # Already in correct format with bind and mode
                if 'bind' in value:
                    bind_path = value['bind']
                    mode = value.get('mode', 'rw')
                    normalized.append(f"{key}:{bind_path}:{mode}")
                else:
                    self.logger.warning(f"Volume dict for '{key}' missing 'bind', skipping")
            elif isinstance(value, str):
                # Simple format: host_path -> container_path
                # Check if it's a named volume (doesn't start with /)
                if not key.startswith('/') and not key.startswith('./') and not key.startswith('../'):
                    # Named volume
                    normalized.append(f"{key}:{value}")
                else:
                    # Bind mount
                    normalized.append(f"{key}:{value}")
            else:
                self.logger.warning(f"Unknown volume format for key '{key}': {type(value)}")
        
        return normalized
    
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

