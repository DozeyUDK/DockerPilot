"""Deployment-state snapshot/restore helpers extracted from BackupRestoreMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any
import json

import docker


def backup_deployment_state(host: Any, backup_path: str = None) -> bool:
    """Create backup of current deployment state"""
    if not backup_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"backup_{timestamp}"

    backup_dir = Path(backup_path)
    backup_dir.mkdir(exist_ok=True)

    try:
        # Backup running containers info
        containers = host.client.containers.list(all=True)
        containers_backup = []

        for container in containers:
            container_info = {
                'name': container.name,
                'image': container.image.tags[0] if container.image.tags else container.image.id,
                'status': container.status,
                'ports': container.ports,
                'environment': container.attrs.get('Config', {}).get('Env', []),
                'volumes': container.attrs.get('Mounts', []),
                'command': container.attrs.get('Config', {}).get('Cmd'),
                'created': container.attrs.get('Created'),
                'restart_policy': container.attrs.get('HostConfig', {}).get('RestartPolicy', {})
            }
            containers_backup.append(container_info)

        # Save containers backup
        with open(backup_dir / 'containers.json', 'w') as f:
            json.dump(containers_backup, f, indent=2)

        # Backup Docker images
        images = host.client.images.list()
        images_backup = []

        for image in images:
            if image.tags:  # Only backup tagged images
                image_info = {
                    'tags': image.tags,
                    'id': image.id,
                    'created': image.attrs.get('Created'),
                    'size': image.attrs.get('Size')
                }
                images_backup.append(image_info)

        with open(backup_dir / 'images.json', 'w') as f:
            json.dump(images_backup, f, indent=2)

        # Backup networks
        networks = host.client.networks.list()
        networks_backup = []

        for network in networks:
            if not network.name.startswith(('bridge', 'host', 'none')):  # Skip default networks
                network_info = {
                    'name': network.name,
                    'driver': network.attrs.get('Driver'),
                    'options': network.attrs.get('Options', {}),
                    'labels': network.attrs.get('Labels', {}),
                    'created': network.attrs.get('Created')
                }
                networks_backup.append(network_info)

        with open(backup_dir / 'networks.json', 'w') as f:
            json.dump(networks_backup, f, indent=2)

        # Backup volumes
        volumes = host.client.volumes.list()
        volumes_backup = []

        for volume in volumes:
            volume_info = {
                'name': volume.name,
                'driver': volume.attrs.get('Driver'),
                'mountpoint': volume.attrs.get('Mountpoint'),
                'labels': volume.attrs.get('Labels', {}),
                'created': volume.attrs.get('CreatedAt')
            }
            volumes_backup.append(volume_info)

        with open(backup_dir / 'volumes.json', 'w') as f:
            json.dump(volumes_backup, f, indent=2)

        # Create backup summary
        summary = {
            'backup_time': datetime.now().isoformat(),
            'containers_count': len(containers_backup),
            'images_count': len(images_backup),
            'networks_count': len(networks_backup),
            'volumes_count': len(volumes_backup),
            'docker_version': host.client.version()['Version']
        }

        with open(backup_dir / 'summary.json', 'w') as f:
            json.dump(summary, f, indent=2)

        host.console.print(f"[green]Deployment state backed up to {backup_path}/[/green]")
        host.console.print(f"[cyan]Backup contains: {len(containers_backup)} containers, {len(images_backup)} images[/cyan]")

        return True

    except Exception as e:
        host.logger.error(f"Backup failed: {e}")
        return False


def restore_deployment_state(host: Any, backup_path: str) -> bool:
    """Restore deployment state from backup"""
    backup_dir = Path(backup_path)

    if not backup_dir.exists():
        host.console.print(f"[red]Backup directory not found: {backup_path}[/red]")
        return False

    try:
        # Load backup summary
        with open(backup_dir / 'summary.json', 'r') as f:
            summary = json.load(f)

        host.console.print(f"[cyan]Restoring backup from {summary['backup_time']}[/cyan]")

        # Restore networks first
        if (backup_dir / 'networks.json').exists():
            with open(backup_dir / 'networks.json', 'r') as f:
                networks = json.load(f)

            for network_info in networks:
                try:
                    host.client.networks.create(
                        name=network_info['name'],
                        driver=network_info['driver'],
                        options=network_info.get('options', {}),
                        labels=network_info.get('labels', {})
                    )
                    host.console.print(f"[green]Restored network: {network_info['name']}[/green]")
                except docker.errors.APIError as e:
                    if "already exists" in str(e):
                        continue
                    host.logger.warning(f"Failed to restore network {network_info['name']}: {e}")

        # Restore volumes
        if (backup_dir / 'volumes.json').exists():
            with open(backup_dir / 'volumes.json', 'r') as f:
                volumes = json.load(f)

            for volume_info in volumes:
                try:
                    host.client.volumes.create(
                        name=volume_info['name'],
                        driver=volume_info['driver'],
                        labels=volume_info.get('labels', {})
                    )
                    host.console.print(f"[green]Restored volume: {volume_info['name']}[/green]")
                except docker.errors.APIError as e:
                    if "already exists" in str(e):
                        continue
                    host.logger.warning(f"Failed to restore volume {volume_info['name']}: {e}")

        # Note: Images and containers would need more complex restoration logic
        # This is a simplified implementation
        host.console.print("[yellow]Note: Complete container restoration requires image availability[/yellow]")
        host.console.print("[yellow]Consider using docker save/load for complete image backup[/yellow]")

        return True

    except Exception as e:
        host.logger.error(f"Restore failed: {e}")
        return False
