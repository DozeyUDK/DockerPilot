"""Image management operations."""
import docker
from typing import List, Any
from rich.table import Table
from rich.panel import Panel

from .utils import format_image_size, format_creation_date, count_containers_using_image


class ImageManager:
    """Manages Docker image operations."""
    
    def __init__(self, client, console, logger, error_handler):
        """Initialize image manager."""
        self.client = client
        self.console = console
        self.logger = logger
        self._error_handler = error_handler
    
    def list_images(self, show_all: bool = True, format_output: str = "table") -> List[Any]:
        """Enhanced image listing with multiple output formats."""
        with self._error_handler("list images"):
            images = self.client.images.list(all=show_all)
            
            if format_output == "json":
                image_data = []
                for img in images:
                    image_data.append({
                        'id': img.id.split(":")[1][:12],
                        'tags': img.tags,
                        'created': img.attrs.get('Created'),
                        'size': format_image_size(img.attrs.get('Size', 0)),
                        'architecture': img.attrs.get('Architecture'),
                        'os': img.attrs.get('Os')
                    })
                self.console.print_json(data=image_data)
                return images
            
            # Enhanced table view with auto-scaling to terminal width
            # Get terminal width for dynamic column sizing
            terminal_width = self.console.width if hasattr(self.console, 'width') else 120
            # Reserve space for borders and padding (approximately 8 characters per column)
            available_width = max(80, terminal_width - 20)  # Minimum 80 chars, reserve 20 for borders
            
            # Calculate proportional widths based on content importance
            # Priority: Repository > Tag > Created > Size > ID > Used By > Nr
            table = Table(
                title="📦 Docker Images", 
                show_header=True, 
                header_style="bold blue",
                expand=True,  # Allow table to expand to terminal width
                show_lines=False  # Disable lines for better space usage
            )
            
            # Track if "Used By" column was added
            include_used_by = available_width >= 100
            
            # Use proportional widths that adapt to terminal size
            # For smaller terminals, some columns will be narrower
            if available_width >= 140:
                # Large terminal - full width columns
                table.add_column("Nr", style="bold blue", width=4, overflow="fold")
                table.add_column("ID", style="cyan", width=12, overflow="fold")
                table.add_column("Repository", style="green", width=min(30, int(available_width * 0.25)), overflow="fold")
                table.add_column("Tag", style="yellow", width=min(18, int(available_width * 0.15)), overflow="fold")
                table.add_column("Size", style="magenta", width=12, overflow="fold")
                table.add_column("Created", style="bright_blue", width=min(20, int(available_width * 0.15)), overflow="fold")
                table.add_column("Used By", style="white", width=8, overflow="fold")
            elif available_width >= 100:
                # Medium terminal - reduce some columns
                table.add_column("Nr", style="bold blue", width=3, overflow="fold")
                table.add_column("ID", style="cyan", width=10, overflow="fold")
                table.add_column("Repository", style="green", width=min(25, int(available_width * 0.28)), overflow="fold")
                table.add_column("Tag", style="yellow", width=min(15, int(available_width * 0.18)), overflow="fold")
                table.add_column("Size", style="magenta", width=10, overflow="fold")
                table.add_column("Created", style="bright_blue", width=min(18, int(available_width * 0.18)), overflow="fold")
                table.add_column("Used By", style="white", width=7, overflow="fold")
            else:
                # Small terminal - minimal columns, remove less critical ones
                table.add_column("Nr", style="bold blue", width=3, overflow="fold")
                table.add_column("ID", style="cyan", width=8, overflow="fold")
                table.add_column("Repository", style="green", width=min(22, int(available_width * 0.35)), overflow="fold")
                table.add_column("Tag", style="yellow", width=min(12, int(available_width * 0.20)), overflow="fold")
                table.add_column("Size", style="magenta", width=9, overflow="fold")
                table.add_column("Created", style="bright_blue", width=min(15, int(available_width * 0.25)), overflow="fold")
                # Remove "Used By" for very small terminals to save space

            for idx, img in enumerate(images, start=1):
                # Parse repository and tag
                if img.tags:
                    repo_tag = img.tags[0]
                    if ':' in repo_tag:
                        repository, tag = repo_tag.rsplit(':', 1)
                    else:
                        repository, tag = repo_tag, "latest"
                else:
                    repository = "<none>"
                    tag = "<none>"
                
                # Format size
                size = format_image_size(img.attrs.get('Size', 0))
                
                # Format creation date
                created = format_creation_date(img.attrs.get('Created'))
                
                # Build row data
                row_data = [
                    str(idx),
                    img.id.split(":")[1][:12],
                    repository,
                    tag,
                    size,
                    created
                ]
                
                # Add "Used By" only if column exists
                if include_used_by:
                    used_by = count_containers_using_image(self.client, img.id)
                    row_data.append(str(used_by))
                
                table.add_row(*row_data)
            
            self.console.print(table)
            
            # Summary statistics
            total_size = sum(img.attrs.get('Size', 0) for img in images)
            total_size_formatted = format_image_size(total_size)
            
            summary = f"📊 Summary: {len(images)} images, {total_size_formatted} total size"
            self.console.print(Panel(summary, style="bright_blue"))
            
            return images
    
    def remove_image(self, image_name: str, force: bool = False) -> bool:
        """Remove Docker image."""
        with self._error_handler("remove image", image_name):
            try:
                self.client.images.remove(image=image_name, force=force, noprune=False)
                self.console.print(f"[green]✅ Image {image_name} removed successfully[/green]")
                self.logger.info(f"Image {image_name} removed successfully")
                return True
            except docker.errors.ImageNotFound:
                self.console.print(f"[bold red]Image not found: {image_name}[/bold red]")
                return False
            except docker.errors.APIError as e:
                self.console.print(f"[bold red]Docker API error during image removal:[/bold red] {e}")
                return False
    
    def build_image(self, dockerfile_path: str, tag: str, no_cache: bool = False, 
                   pull: bool = True, build_args: dict = None) -> bool:
        """Build Docker image from Dockerfile."""
        try:
            self.console.print(f"[cyan]Building image {tag} from {dockerfile_path}...[/cyan]")
            
            # Build image
            image, logs = self.client.images.build(
                path=dockerfile_path,
                tag=tag,
                pull=pull,
                rm=True,
                forcerm=True,
                nocache=no_cache,
                buildargs=build_args or {}
            )
            
            self.console.print(f"[green]✅ Image {tag} built successfully[/green]")
            self.logger.info(f"Image {tag} built successfully")
            return True
        except docker.errors.BuildError as e:
            self.console.print(f"[bold red]Build failed:[/bold red] {e}")
            self.logger.error(f"Image build failed: {e}")
            return False
        except Exception as e:
            self.console.print(f"[bold red]Error building image:[/bold red] {e}")
            self.logger.error(f"Image build error: {e}")
            return False

