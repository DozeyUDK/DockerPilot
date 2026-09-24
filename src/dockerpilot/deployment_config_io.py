"""Deployment config/template file helpers."""

from pathlib import Path
from typing import Any


def create_dockerfile_template(host: Any, destination: str, template_name: str) -> bool:
    """Create a starter Dockerfile in the requested directory."""
    template_body = host._DOCKERFILE_TEMPLATE_BODIES.get(template_name)
    if not template_body:
        host.console.print(f"[red]Unknown Dockerfile template: {template_name}[/red]")
        return False

    destination_path = Path(destination).expanduser()
    if not destination_path.is_absolute():
        destination_path = Path.cwd() / destination_path
    if destination_path.suffix or host._is_dockerfile_candidate(destination_path):
        destination_path = destination_path.parent
    destination_path.mkdir(parents=True, exist_ok=True)

    dockerfile_path = destination_path / "Dockerfile"
    if dockerfile_path.exists():
        host.console.print(f"[yellow]Dockerfile already exists at {dockerfile_path}[/yellow]")
        return False

    dockerfile_path.write_text(template_body, encoding="utf-8")
    host.console.print(f"[green]✅ Created {template_name} Dockerfile template at {dockerfile_path}[/green]")
    host.logger.info(f"Created Dockerfile template '{template_name}' at {dockerfile_path}")
    return True


def create_deployment_config(host: Any, config_path: str = "deployment.yml") -> bool:
    """Create deployment configuration template from file"""
    try:
        # Load template from configs directory
        template_path = Path(__file__).parent / "configs" / "deployment.yml.template"

        if not template_path.exists():
            host.logger.error(f"Template file not found: {template_path}")
            host.console.print(f"[red]Template file not found: {template_path}[/red]")
            return False

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(template_content)

        host.console.print(f"[green]✅ Deployment configuration template created: {config_path}[/green]")
        return True
    except Exception as e:
        host.logger.error(f"Failed to create config template: {e}")
        return False
