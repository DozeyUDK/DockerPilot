#!/usr/bin/env python3
"""
Script to promote all containers from one environment to another.
Usage: python3 promote_all_containers.py <from_env> <to_env>
Example: python3 promote_all_containers.py dev staging
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import dockerpilot
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dockerpilot.pilot import DockerPilotEnhanced
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

def find_all_deployment_configs(env: str = 'dev') -> list:
    """Find all deployment configs for given environment"""
    import yaml
    configs = []
    deployments_dir = Path.home() / '.dockerpilot_extras' / 'deployments'
    
    if not deployments_dir.exists():
        return configs
    
    for deployment_dir in deployments_dir.iterdir():
        if deployment_dir.is_dir():
            config_path = deployment_dir / f'deployment-{env}.yml'
            if config_path.exists():
                # Try to get container name from config file
                try:
                    with open(config_path, 'r') as f:
                        config = yaml.safe_load(f)
                        container_name = config.get('deployment', {}).get('container_name', deployment_dir.name.split('_')[0])
                except:
                    # Fallback to directory name
                    container_name = deployment_dir.name.split('_')[0]
                
                configs.append({
                    'path': str(config_path),
                    'container': container_name
                })
    
    return configs

def promote_all_containers(from_env: str, to_env: str, dry_run: bool = False):
    """Promote all containers from one environment to another"""
    console = Console()
    
    console.print(f"[bold cyan]Promoting all containers from {from_env.upper()} to {to_env.upper()}[/bold cyan]")
    
    # Find all deployment configs
    configs = find_all_deployment_configs(from_env)
    
    if not configs:
        console.print(f"[red]No deployment configs found for {from_env} environment[/red]")
        return False
    
    console.print(f"[green]Found {len(configs)} containers to promote[/green]")
    
    if dry_run:
        console.print("[yellow]DRY RUN MODE - No actual promotion will be performed[/yellow]")
        for config_info in configs:
            console.print(f"  Would promote: {config_info['container']} ({config_info['path']})")
        return True
    
    # Initialize DockerPilot
    pilot = DockerPilotEnhanced()
    
    # Track results
    results = {
        'success': [],
        'failed': []
    }
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        
        for i, config_info in enumerate(configs, 1):
            config_path = config_info['path']
            container_name = config_info['container']
            
            task = progress.add_task(f"[{i}/{len(configs)}] Promoting {container_name}...", total=None)
            
            try:
                success = pilot.environment_promotion(from_env, to_env, config_path)
                
                if success:
                    progress.update(task, description=f"[{i}/{len(configs)}] ✅ {container_name} promoted successfully")
                    results['success'].append(container_name)
                else:
                    progress.update(task, description=f"[{i}/{len(configs)}] ❌ {container_name} promotion failed")
                    results['failed'].append(container_name)
                    
            except Exception as e:
                progress.update(task, description=f"[{i}/{len(configs)}] ❌ {container_name} error: {str(e)[:50]}")
                results['failed'].append((container_name, str(e)))
                console.print(f"[red]Error promoting {container_name}: {e}[/red]")
    
    # Summary
    console.print(f"\n[bold]Promotion Summary:[/bold]")
    console.print(f"[green]✅ Successfully promoted: {len(results['success'])}[/green]")
    if results['failed']:
        console.print(f"[red]❌ Failed: {len(results['failed'])}[/red]")
        for item in results['failed']:
            if isinstance(item, tuple):
                console.print(f"  - {item[0]}: {item[1]}")
            else:
                console.print(f"  - {item}")
    
    return len(results['failed']) == 0

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 promote_all_containers.py <from_env> <to_env> [--dry-run]")
        print("Example: python3 promote_all_containers.py dev staging")
        sys.exit(1)
    
    from_env = sys.argv[1].lower()
    to_env = sys.argv[2].lower()
    dry_run = '--dry-run' in sys.argv
    
    if from_env not in ['dev', 'staging', 'prod'] or to_env not in ['dev', 'staging', 'prod']:
        print("Error: Environments must be: dev, staging, or prod")
        sys.exit(1)
    
    success = promote_all_containers(from_env, to_env, dry_run)
    sys.exit(0 if success else 1)

