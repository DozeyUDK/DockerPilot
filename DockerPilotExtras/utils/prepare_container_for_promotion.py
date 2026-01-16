#!/usr/bin/env python3
"""
Script to prepare containers for promotion by:
1. Backing up container data
2. Extracting container configuration (ports, volumes, env vars, etc.)
3. Creating deployment-dev.yml configuration files
4. Then containers can be promoted through environments

Usage: python3 prepare_container_for_promotion.py <container_name1> [container_name2] ...
Example: python3 prepare_container_for_promotion.py influxdb ollama minikube
"""

import sys
import os
from pathlib import Path
import json
import yaml

# Add parent directory to path to import dockerpilot
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dockerpilot.pilot import DockerPilotEnhanced
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
import docker

def extract_container_config(container):
    """Extract full configuration from running container"""
    attrs = container.attrs
    
    # Extract image tag
    image_tag = attrs.get('Config', {}).get('Image', '')
    if not image_tag:
        image_tag = container.image.tags[0] if container.image.tags else container.image.id
    
    # Extract port mappings
    port_mapping = {}
    if 'NetworkSettings' in attrs:
        ports = attrs['NetworkSettings'].get('Ports', {})
        for container_port, host_bindings in ports.items():
            if host_bindings:
                # Format: "3000/tcp" -> "3000"
                port_num = container_port.split('/')[0]
                # Get first host port
                host_port = host_bindings[0].get('HostPort', '')
                if host_port:
                    port_mapping[port_num] = host_port
    
    # Extract environment variables
    environment = {}
    env_list = attrs.get('Config', {}).get('Env', [])
    for env_var in env_list:
        if '=' in env_var:
            key, value = env_var.split('=', 1)
            environment[key] = value
    
    # Extract volumes
    volumes = {}
    mounts = attrs.get('Mounts', [])
    for mount in mounts:
        source = mount.get('Source', '')
        destination = mount.get('Destination', '')
        volume_name = mount.get('Name', '')
        
        if destination:
            # Prefer named volume if available
            if volume_name:
                # Named volume - use volume name
                volumes[volume_name] = destination
            elif source and not source.startswith('/var/lib/docker/volumes/'):
                # Bind mount (not a Docker internal volume path)
                volumes[source] = destination
            elif source:
                # Docker volume but no name - try to extract from path or use source
                # Extract volume name from path like: /var/lib/docker/volumes/volume_name/_data
                if '/volumes/' in source:
                    vol_path_parts = source.split('/volumes/')
                    if len(vol_path_parts) > 1:
                        vol_id = vol_path_parts[1].split('/')[0]
                        # Use volume ID as name if no name available
                        volumes[vol_id] = destination
                else:
                    # Fallback to source path
                    volumes[source] = destination
    
    # Extract restart policy
    restart_policy = 'no'
    host_config = attrs.get('HostConfig', {})
    restart_policy_config = host_config.get('RestartPolicy', {})
    if restart_policy_config:
        restart_policy = restart_policy_config.get('Name', 'no')
    
    # Extract network mode
    network_mode = host_config.get('NetworkMode', 'bridge')
    if network_mode == 'default':
        network_mode = 'bridge'
    
    # Extract resource limits
    cpu_limit = None
    memory_limit = None
    if 'NanoCpus' in host_config:
        cpu_limit = str(host_config['NanoCpus'] / 1000000000)  # Convert to CPU units
    if 'Memory' in host_config and host_config['Memory'] > 0:
        memory_mb = host_config['Memory'] / (1024 * 1024)
        if memory_mb >= 1024:
            memory_limit = f"{int(memory_mb / 1024)}Gi"
        else:
            memory_limit = f"{int(memory_mb)}Mi"
    
    # Extract command
    cmd = attrs.get('Config', {}).get('Cmd', [])
    command = ' '.join(cmd) if cmd else None
    
    return {
        'image_tag': image_tag,
        'container_name': container.name,
        'port_mapping': port_mapping,
        'environment': environment,
        'volumes': volumes,
        'restart_policy': restart_policy,
        'network': network_mode,
        'cpu_limit': cpu_limit,
        'memory_limit': memory_limit,
        'command': command
    }

def create_deployment_config(container_config, env='dev'):
    """Create deployment YAML configuration from container config"""
    deployment = {
        'deployment': {
            'image_tag': container_config['image_tag'],
            'container_name': container_config['container_name'],
            'port_mapping': container_config['port_mapping'],
            'environment': container_config['environment'],
            'volumes': container_config['volumes'],
            'restart_policy': container_config['restart_policy'],
            'network': container_config['network'],
            'health_check_endpoint': '/health',
            'health_check_timeout': 30,
            'health_check_retries': 10
        }
    }
    
    # Add resource limits if available
    if container_config.get('cpu_limit'):
        deployment['deployment']['cpu_limit'] = container_config['cpu_limit']
    if container_config.get('memory_limit'):
        deployment['deployment']['memory_limit'] = container_config['memory_limit']
    
    # Add command if available
    if container_config.get('command'):
        deployment['deployment']['command'] = container_config['command']
    
    # Environment-specific resource adjustments
    if env == 'dev':
        if 'cpu_limit' in deployment['deployment']:
            # Reduce CPU for dev
            try:
                cpu = float(deployment['deployment']['cpu_limit'])
                deployment['deployment']['cpu_limit'] = str(max(0.5, cpu * 0.5))
            except:
                pass
        if 'memory_limit' in deployment['deployment']:
            # Reduce memory for dev
            mem_str = deployment['deployment']['memory_limit']
            if 'Gi' in mem_str:
                mem_gb = float(mem_str.replace('Gi', ''))
                deployment['deployment']['memory_limit'] = f"{max(0.5, mem_gb * 0.5)}Gi"
            elif 'Mi' in mem_str:
                mem_mb = float(mem_str.replace('Mi', ''))
                deployment['deployment']['memory_limit'] = f"{max(512, mem_mb * 0.5)}Mi"
    
    return deployment

def prepare_container_for_promotion(container_name, dry_run=False):
    """Prepare a single container for promotion"""
    console = Console()
    
    try:
        # Initialize DockerPilot without banner and with minimal logging
        import logging
        logging.getLogger('DockerPilot').setLevel(logging.WARNING)
        
        pilot = DockerPilotEnhanced()
        client = pilot.client
        
        # Get container
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            console.print(f"[red]❌ Container '{container_name}' not found[/red]")
            return False
        
        console.print(f"[cyan]📦 Preparing container: {container_name}[/cyan]")
        
        # Step 1: Skip backup - can be done separately to avoid hanging on system paths
        # Use: dockerpilot backup container-data <container_name>
        if not dry_run:
            console.print(f"[yellow]💾 Step 1: Backup skipped (use 'dockerpilot backup container-data {container_name}' separately if needed)[/yellow]")
        else:
            console.print(f"[yellow]💾 [DRY RUN] Would backup container data for {container_name}[/yellow]")
        
        # Step 2: Extract container configuration
        console.print(f"[yellow]🔍 Step 2: Extracting container configuration...[/yellow]")
        container_config = extract_container_config(container)
        
        console.print(f"[green]✓ Image: {container_config['image_tag']}[/green]")
        console.print(f"[green]✓ Ports: {len(container_config['port_mapping'])} mapped[/green]")
        console.print(f"[green]✓ Volumes: {len(container_config['volumes'])} mounted[/green]")
        console.print(f"[green]✓ Environment vars: {len(container_config['environment'])}[/green]")
        
        # Step 3: Create deployment-dev.yml
        console.print(f"[yellow]📝 Step 3: Creating deployment-dev.yml...[/yellow]")
        deployment_config = create_deployment_config(container_config, env='dev')
        
        # Save to unified deployment directory structure
        # Use the same logic as backend but without importing Flask app
        from datetime import datetime
        import hashlib
        import re
        
        # Generate deployment directory
        deployments_dir = Path.home() / '.dockerpilot_extras' / 'deployments'
        deployments_dir.mkdir(exist_ok=True, parents=True)
        
        # Find or create deployment directory
        container_name = container_config['container_name']
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', container_name.lower())
        
        # Find existing deployment or create new
        deployment_dir = None
        for d in deployments_dir.iterdir():
            if d.is_dir() and d.name.startswith(safe_name + '_'):
                metadata_path = d / 'metadata.json'
                if metadata_path.exists():
                    try:
                        with open(metadata_path, 'r') as f:
                            metadata = json.load(f)
                            if metadata.get('container_name', '').lower() == container_name.lower():
                                deployment_dir = d
                                break
                    except:
                        pass
        
        if not deployment_dir:
            # Create new deployment directory
            timestamp = datetime.now().isoformat()
            hash_input = f"{container_name}_{container_config['image_tag']}_{timestamp}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
            unique_id = f"{hash_value[:4]}!{hash_value[4:8]}{hash_value[8:12]}"
            deployment_id = f"{safe_name}_{unique_id}"
            deployment_dir = deployments_dir / deployment_id
            deployment_dir.mkdir(exist_ok=True, parents=True)
            
            # Create metadata
            metadata = {
                'container_name': container_name,
                'image_tag': container_config['image_tag'],
                'created_at': datetime.now().isoformat(),
                'deployment_id': deployment_id
            }
            metadata_path = deployment_dir / 'metadata.json'
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
        
        # Save deployment-dev.yml
        config_path = deployment_dir / 'deployment-dev.yml'
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(deployment_config, f, default_flow_style=False, allow_unicode=True)
        
        # Update metadata
        metadata_path = deployment_dir / 'metadata.json'
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        else:
            metadata = {}
        
        metadata['last_updated'] = datetime.now().isoformat()
        metadata['env_dev_config'] = str(config_path)
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        
        console.print(f"[green]✅ Configuration saved to: {config_path}[/green]")
        
        return True
        
    except Exception as e:
        console.print(f"[red]❌ Error preparing {container_name}: {e}[/red]")
        import traceback
        console.print(f"[red]{traceback.format_exc()}[/red]")
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 prepare_container_for_promotion.py <container_name1> [container_name2] ...")
        print("Example: python3 prepare_container_for_promotion.py influxdb ollama minikube")
        sys.exit(1)
    
    container_names = sys.argv[1:]
    dry_run = '--dry-run' in container_names
    if dry_run:
        container_names.remove('--dry-run')
    
    console = Console()
    console.print(f"[bold cyan]Preparing {len(container_names)} containers for promotion...[/bold cyan]")
    
    results = {'success': [], 'failed': []}
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        
        for i, container_name in enumerate(container_names, 1):
            task = progress.add_task(f"[{i}/{len(container_names)}] {container_name}...", total=None)
            
            success = prepare_container_for_promotion(container_name, dry_run)
            
            if success:
                progress.update(task, description=f"[{i}/{len(container_names)}] ✅ {container_name}")
                results['success'].append(container_name)
            else:
                progress.update(task, description=f"[{i}/{len(container_names)}] ❌ {container_name}")
                results['failed'].append(container_name)
    
    # Summary
    console.print(f"\n[bold]Preparation Summary:[/bold]")
    console.print(f"[green]✅ Successfully prepared: {len(results['success'])}[/green]")
    if results['failed']:
        console.print(f"[red]❌ Failed: {len(results['failed'])}[/red]")
        for name in results['failed']:
            console.print(f"  - {name}")
    
    return len(results['failed']) == 0

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

