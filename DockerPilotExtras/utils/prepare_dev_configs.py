#!/usr/bin/env python3
"""
Script to prepare deployment-dev.yml configurations for all running containers.
This will assign all containers to DEV environment.
"""

import docker
import yaml
from pathlib import Path
from datetime import datetime
import json
import sys

def get_container_info(client, container_name):
    """Get detailed information about a container"""
    try:
        container = client.containers.get(container_name)
        attrs = container.attrs
        
        # Get image
        image = container.image.tags[0] if container.image.tags else container.image.id
        
        # Get ports
        port_mapping = {}
        ports = attrs.get('NetworkSettings', {}).get('Ports', {})
        if ports:
            for container_port, host_bindings in ports.items():
                if host_bindings:
                    # Extract host port from binding
                    host_port = host_bindings[0].get('HostPort', '')
                    # Remove /tcp, /udp suffix
                    container_port_clean = container_port.split('/')[0]
                    if host_port:
                        port_mapping[container_port_clean] = host_port
        
        # Get environment variables
        env_vars = {}
        env_list = attrs.get('Config', {}).get('Env', [])
        for env in env_list:
            if '=' in env:
                key, value = env.split('=', 1)
                env_vars[key] = value
        
        # Get volumes
        volumes = {}
        mounts = attrs.get('Mounts', [])
        for mount in mounts:
            source = mount.get('Source', '')
            destination = mount.get('Destination', '')
            if source and destination:
                volumes[source] = destination
        
        # Get restart policy
        restart_policy = attrs.get('HostConfig', {}).get('RestartPolicy', {}).get('Name', 'unless-stopped')
        
        # Get network
        network_mode = attrs.get('HostConfig', {}).get('NetworkMode', 'bridge')
        networks = attrs.get('NetworkSettings', {}).get('Networks', {})
        network_name = None
        if network_mode != 'bridge' and network_mode not in ['host', 'none']:
            network_name = network_mode
        elif networks:
            network_name = list(networks.keys())[0]
        
        # Get resource limits
        host_config = attrs.get('HostConfig', {})
        cpu_limit = None
        memory_limit = None
        
        if 'NanoCpus' in host_config and host_config['NanoCpus']:
            cpu_limit = str(host_config['NanoCpus'] / 1000000000)  # Convert to CPUs
        
        if 'Memory' in host_config and host_config['Memory']:
            memory_mb = host_config['Memory'] / (1024 * 1024)
            if memory_mb >= 1024:
                memory_limit = f"{memory_mb / 1024:.1f}g"
            else:
                memory_limit = f"{int(memory_mb)}m"
        
        return {
            'image': image,
            'port_mapping': port_mapping,
            'environment': env_vars,
            'volumes': volumes,
            'restart_policy': restart_policy,
            'network': network_name or 'bridge',
            'cpu_limit': cpu_limit,
            'memory_limit': memory_limit
        }
    except Exception as e:
        print(f"Error getting info for {container_name}: {e}")
        return None

def create_deployment_config(container_name, container_info, deployments_dir):
    """Create deployment-dev.yml for a container"""
    
    # Clean container name (remove leading slash, handle blue/green variants)
    clean_name = container_name.lstrip('/')
    base_name = clean_name
    for suffix in ['_blue', '_green', '_canary', '_new', '_old']:
        if base_name.endswith(suffix):
            base_name = base_name[:-len(suffix)]
            break
    
    # Create deployment directory
    deployment_id = f"{base_name}_{int(datetime.now().timestamp())}"
    deployment_dir = deployments_dir / deployment_id
    deployment_dir.mkdir(exist_ok=True, parents=True)
    
    # Create metadata
    metadata = {
        'container_name': base_name,
        'image_tag': container_info['image'],
        'created_at': datetime.now().isoformat(),
        'deployment_id': deployment_id,
        'last_updated': datetime.now().isoformat(),
        'env_dev_config': str(deployment_dir / 'deployment-dev.yml')
    }
    
    metadata_path = deployment_dir / 'metadata.json'
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)
    
    # Create deployment-dev.yml
    deployment_config = {
        'deployment': {
            'image_tag': container_info['image'],
            'container_name': base_name,
            'port_mapping': container_info['port_mapping'] or {},
            'environment': container_info['environment'] or {},
            'volumes': container_info['volumes'] or {},
            'restart_policy': container_info['restart_policy'] or 'unless-stopped',
            'network': container_info['network'] or 'bridge',
            'health_check_endpoint': '/health',
            'health_check_timeout': 30,
            'health_check_retries': 10
        }
    }
    
    # Add resource limits if available
    if container_info['cpu_limit']:
        deployment_config['deployment']['cpu_limit'] = container_info['cpu_limit']
    if container_info['memory_limit']:
        deployment_config['deployment']['memory_limit'] = container_info['memory_limit']
    
    # Write config file
    config_path = deployment_dir / 'deployment-dev.yml'
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(deployment_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    return config_path

def main():
    """Main function to prepare DEV configs for all running containers"""
    
    # Connect to Docker
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        print(f"Error connecting to Docker: {e}")
        sys.exit(1)
    
    # Get deployments directory
    config_dir = Path.home() / ".dockerpilot_extras"
    deployments_dir = config_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True, parents=True)
    
    print(f"📦 Preparing DEV configurations for all running containers...")
    print(f"📁 Deployments directory: {deployments_dir}\n")
    
    # Get all running containers
    containers = client.containers.list(filters={'status': 'running'})
    
    if not containers:
        print("No running containers found.")
        return
    
    print(f"Found {len(containers)} running container(s):\n")
    
    created_configs = []
    
    for container in containers:
        container_name = container.name
        print(f"Processing: {container_name}...")
        
        # Get container info
        container_info = get_container_info(client, container_name)
        if not container_info:
            print(f"  ⚠️  Skipped: Could not get container info")
            continue
        
        # Create deployment config
        try:
            config_path = create_deployment_config(container_name, container_info, deployments_dir)
            created_configs.append({
                'container': container_name,
                'config': str(config_path),
                'image': container_info['image']
            })
            print(f"  ✅ Created: {config_path}")
        except Exception as e:
            print(f"  ❌ Error creating config: {e}")
    
    print(f"\n{'='*60}")
    print(f"✅ Successfully created {len(created_configs)} DEV configuration(s)")
    print(f"{'='*60}\n")
    
    # Summary
    for item in created_configs:
        print(f"  • {item['container']} -> {item['config']}")

if __name__ == '__main__':
    main()

