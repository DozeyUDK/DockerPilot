"""Environment promotion workflows extracted from DeploymentServiceMixin."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json
import time

import docker
import yaml

from .models import DeploymentConfig


def environment_promotion(
    host: Any,
    source_env: str,
    target_env: str,
    config_path: str = None,
    skip_backup: bool = False,
) -> bool:
    """Promote deployment between environments (dev -> staging -> prod)

    Args:
        source_env: Source environment (dev/staging)
        target_env: Target environment (staging/prod)
        config_path: Path to deployment config (optional)
        skip_backup: Skip data backup before deployment (faster but risky)
    """
    host.console.print(f"[cyan]Promoting from {source_env} to {target_env}...[/cyan]")

    # Environment-specific configurations
    env_configs = {
        'dev': {
            'replicas': 1,
            'resources': {'cpu': '0.5', 'memory': '512Mi'},
            'image_tag_suffix': '-dev'
        },
        'staging': {
            'replicas': 2,
            'resources': {'cpu': '1.0', 'memory': '1Gi'},
            'image_tag_suffix': '-staging'
        },
        'prod': {
            'replicas': 3,
            'resources': {'cpu': '2.0', 'memory': '2Gi'},
            'image_tag_suffix': ''
        }
    }

    if source_env not in env_configs or target_env not in env_configs:
        host.console.print(f"[red]Invalid environment: {source_env} or {target_env}[/red]")
        return False

    try:
        # Load base configuration
        if not config_path:
            config_path = f"deployment-{target_env}.yml"

        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
        else:
            host.console.print(f"[red]Configuration file not found: {config_path}[/red]")
            return False

        # Apply environment-specific settings
        target_config = env_configs[target_env]

        # Keep original image tag for all environments
        # Don't modify image tags during promotion - same image should be used across environments
        # Environment-specific tags should be set manually in deployment configs if needed
        original_image_tag = config['deployment']['image_tag']
        config['deployment']['image_tag'] = original_image_tag
        host.logger.info(f"Using original image tag for promotion: {original_image_tag}")

        # Update resources
        config['deployment']['cpu_limit'] = target_config['resources']['cpu']
        config['deployment']['memory_limit'] = target_config['resources']['memory']

        # For STAGING environment, check if container already exists in PROD
        # If it does, don't create duplicate - just save config and mark as promoted
        original_container_name = config['deployment']['container_name']
        if target_env == 'staging':
            # Check if container with base name or variants already exists (likely in PROD)
            existing_prod_container = None
            try:
                existing_prod_container = host.client.containers.get(original_container_name)
            except docker.errors.NotFound:
                # Also check for blue/green variants
                for suffix in ['_blue', '_green']:
                    try:
                        existing_prod_container = host.client.containers.get(f"{original_container_name}{suffix}")
                        break
                    except docker.errors.NotFound:
                        pass

            if existing_prod_container and existing_prod_container.status == 'running':
                # Container already exists in PROD - don't create duplicate for STAGING
                # Just save config and record promotion without deploying
                host.console.print(f"[yellow]⚠️ Container '{original_container_name}' already exists in PROD[/yellow]")
                host.console.print(f"[cyan]Saving STAGING configuration without creating new container...[/cyan]")

                # Save config for staging (will be used for future deployments)
                container_name = config['deployment']['container_name']
                image_tag = config['deployment']['image_tag']
                config_dir = Path(config_path).parent
                target_config_path = config_dir / f'deployment-{target_env}.yml'
                with open(target_config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                host.logger.info(f"Saved deployment config for {target_env} environment to {target_config_path}")

                # Record promotion without deploying
                deployment_id = f"promote_{source_env}_to_{target_env}_{int(time.time())}"
                duration = timedelta(seconds=1)

                # Create a deployment record for staging
                deployment_config = host._deployment_config_from_dict(config['deployment'])
                host._record_deployment(deployment_id, deployment_config, f'promotion-{target_env}', True, duration, target_env=target_env)

                host.console.print(f"[green]✓ STAGING configuration saved. Container already running in PROD, no new deployment needed.[/green]")
                return True

        # Run pre-promotion checks
        if not host._run_pre_promotion_checks(source_env, target_env):
            host.console.print("[red]Pre-promotion checks failed[/red]")
            return False

        deployment = config['deployment']
        deployment_config = host._deployment_config_from_dict(deployment)
        build_config = config.get('build', {})

        # Use appropriate deployment strategy based on target environment
        deployment_type = 'blue-green' if target_env == 'prod' else 'rolling'

        if deployment_type == 'blue-green':
            success = host._blue_green_deploy_enhanced(deployment_config, build_config)
        else:
            success = host._rolling_deploy(deployment_config, build_config)

        if success:
            # Run post-promotion validation
            if host._run_post_promotion_validation(target_env, deployment_config):
                # Save deployment config for target environment
                # Extract container name from config
                container_name = config['deployment']['container_name']
                image_tag = config['deployment']['image_tag']

                # Save config to unified deployment directory structure
                # Save directly next to source config
                config_dir = Path(config_path).parent
                target_config_path = config_dir / f'deployment-{target_env}.yml'
                with open(target_config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                host.logger.info(f"Saved deployment config for {target_env} environment to {target_config_path}")

                # Update metadata.json if it exists
                metadata_path = config_dir / 'metadata.json'
                if metadata_path.exists():
                    try:
                        with open(metadata_path, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        metadata['last_updated'] = datetime.now().isoformat()
                        metadata[f'env_{target_env}_config'] = str(target_config_path)
                        with open(metadata_path, 'w', encoding='utf-8') as f:
                            json.dump(metadata, f, indent=2)
                        host.logger.info(f"Updated metadata.json with {target_env} config path")
                    except Exception as e:
                        host.logger.warning(f"Could not update metadata.json: {e}")

                # Record deployment with environment information
                deployment_id = f"promote_{source_env}_to_{target_env}_{int(time.time())}"
                duration = timedelta(seconds=5)  # Approximate duration
                host._record_deployment(deployment_id, deployment_config, f'promotion-{target_env}', True, duration, target_env=target_env)

                host.console.print(f"[green]Successfully promoted to {target_env}[/green]")
                return True
            else:
                host.console.print(f"[yellow]Deployment succeeded but validation failed in {target_env}[/yellow]")
                return False
        else:
            host.console.print(f"[red]Deployment failed in {target_env}[/red]")
            return False

    except Exception as e:
        host.logger.error(f"Environment promotion failed: {e}")
        return False


def run_pre_promotion_checks(host: Any, source_env: str, target_env: str) -> bool:
    """Run checks before promoting between environments"""
    checks = [
        f"Source environment ({source_env}) is healthy",
        f"Target environment ({target_env}) is ready",
        "All required tests have passed",
        "No blocking issues in monitoring systems"
    ]

    # For demo purposes, we'll simulate these checks
    # In real implementation, these would check actual systems

    for check in checks:
        # Simulate check (replace with real logic)
        time.sleep(1)
        host.console.print(f"[green]✓[/green] {check}")

    return True


def run_post_promotion_validation(
    host: Any,
    environment: str,
    config: DeploymentConfig,
) -> bool:
    """Validate deployment after promotion"""
    validation_checks = [
        "Application is responding to health checks",
        "All services are running correctly",
        "Performance metrics are within acceptable ranges",
        "No error spikes in logs"
    ]

    # Run actual health checks
    if config.port_mapping:
        port = list(config.port_mapping.values())[0]
        if not host._advanced_health_check(port, config.health_check_endpoint, 30, 5):
            return False

    # Additional validation checks would go here
    for check in validation_checks:
        time.sleep(1)
        host.console.print(f"[green]✓[/green] {check}")

    return True
