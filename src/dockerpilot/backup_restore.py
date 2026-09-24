"""Backup and restore services extracted from DockerPilotEnhanced."""

from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import os
import subprocess
import time
import docker
from .execution_context import resolve_sudo_password
from .backup_discovery import find_existing_backup as _find_existing_backup_impl
from .container_backup import backup_container_data as _backup_container_data_impl
from .container_restore import (
    restore_container_data as _restore_container_data_impl,
    restore_from_tar as _restore_from_tar_impl,
)
from .container_migration import (
    copy_bind_mount_data as _copy_bind_mount_data_impl,
    copy_container_files as _copy_container_files_impl,
    copy_volume_data as _copy_volume_data_impl,
    migrate_container_data as _migrate_container_data_impl,
)
from .backup_mounts import check_sudo_required_for_backup as _check_sudo_required_for_backup_impl
from .backup_archive import (
    backup_bind_mount_using_docker as _backup_bind_mount_using_docker_impl,
    backup_directory as _backup_directory_impl,
    backup_volume_using_docker as _backup_volume_using_docker_impl,
    cleanup_backup_containers as _cleanup_backup_containers_impl,
    run_sudo_command as _run_sudo_command_impl,
)
from .models import DeploymentConfig


class BackupRestoreMixin:
    """Mixin containing backup/restore logic for DockerPilot."""

    def _get_sudo_password(self) -> Optional[str]:
        """Resolve a per-execution credential before the legacy fallback."""

        return resolve_sudo_password(getattr(self, '_sudo_password', None))

    def _check_sudo_required_for_backup(self, container_name: str) -> tuple[bool, list[str], dict]:
        """Check whether backup mount access requires sudo and collect mount metadata."""
        return _check_sudo_required_for_backup_impl(self, container_name)
    
    def find_existing_backup(self, container_name: str, max_age_hours: int = 24) -> Optional[Path]:
        """Find a recent complete backup for a container."""
        return _find_existing_backup_impl(self, container_name, max_age_hours)
    
    def backup_container_data(self, container_name: str, backup_path: str = None, reuse_existing: bool = True, max_backup_age_hours: int = 24) -> bool:
        """Backup all data mounted into a container."""
        return _backup_container_data_impl(
            self,
            container_name,
            backup_path=backup_path,
            reuse_existing=reuse_existing,
            max_backup_age_hours=max_backup_age_hours,
        )
    
    def _backup_volume_using_docker(self, volume_name: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup a Docker volume through the archive runtime helper."""
        return _backup_volume_using_docker_impl(self, volume_name, backup_file, container_name)
    
    def _cleanup_backup_containers(self):
        """Clean orphaned temporary backup containers."""
        return _cleanup_backup_containers_impl(self)
    
    def _backup_bind_mount_using_docker(self, source_path: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup a bind mount through the archive runtime helper."""
        return _backup_bind_mount_using_docker_impl(self, source_path, backup_file, container_name)
    
    def _backup_directory(self, source_path: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup a directory through the direct tar fallback."""
        return _backup_directory_impl(self, source_path, backup_file, container_name)
    
    def _run_sudo_command(self, command_args, timeout=10, check=False):
        """Run a sudo command through the backup archive runtime."""
        return _run_sudo_command_impl(self, command_args, timeout=timeout, check=check)
    
    def restore_container_data(self, container_name: str, backup_path: str) -> bool:
        """Restore container data from a backup directory."""
        return _restore_container_data_impl(self, container_name, backup_path)
    
    def _restore_from_tar(self, tar_file: Path, destination: str) -> bool:
        """Extract a tar.gz backup into the destination."""
        return _restore_from_tar_impl(self, tar_file, destination)
    
    def _migrate_container_data(self, source_container, target_container, config: DeploymentConfig) -> bool:
        """Migrate data between deployment containers."""
        return _migrate_container_data_impl(self, source_container, target_container, config)
    
    def _copy_volume_data(self, source_volume_name: str, target_volume_name: str, container_name: str = None) -> bool:
        """Copy data between named Docker volumes."""
        return _copy_volume_data_impl(self, source_volume_name, target_volume_name, container_name)
    
    def _copy_bind_mount_data(self, source_path: str, target_path: str, container_name: str = None) -> bool:
        """Copy data between bind-mount paths."""
        return _copy_bind_mount_data_impl(self, source_path, target_path, container_name)
    
    def _copy_container_files(self, source_container, target_container, source_path: str, container_name: str = None) -> bool:
        """Copy internal files between containers."""
        return _copy_container_files_impl(self, source_container, target_container, source_path, container_name)

    def backup_deployment_state(self, backup_path: str = None) -> bool:
        """Create backup of current deployment state"""
        if not backup_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_{timestamp}"
        
        backup_dir = Path(backup_path)
        backup_dir.mkdir(exist_ok=True)
        
        try:
            # Backup running containers info
            containers = self.client.containers.list(all=True)
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
            images = self.client.images.list()
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
            networks = self.client.networks.list()
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
            volumes = self.client.volumes.list()
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
                'docker_version': self.client.version()['Version']
            }
            
            with open(backup_dir / 'summary.json', 'w') as f:
                json.dump(summary, f, indent=2)
            
            self.console.print(f"[green]Deployment state backed up to {backup_path}/[/green]")
            self.console.print(f"[cyan]Backup contains: {len(containers_backup)} containers, {len(images_backup)} images[/cyan]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Backup failed: {e}")
            return False

    def restore_deployment_state(self, backup_path: str) -> bool:
        """Restore deployment state from backup"""
        backup_dir = Path(backup_path)
        
        if not backup_dir.exists():
            self.console.print(f"[red]Backup directory not found: {backup_path}[/red]")
            return False
        
        try:
            # Load backup summary
            with open(backup_dir / 'summary.json', 'r') as f:
                summary = json.load(f)
            
            self.console.print(f"[cyan]Restoring backup from {summary['backup_time']}[/cyan]")
            
            # Restore networks first
            if (backup_dir / 'networks.json').exists():
                with open(backup_dir / 'networks.json', 'r') as f:
                    networks = json.load(f)
                
                for network_info in networks:
                    try:
                        self.client.networks.create(
                            name=network_info['name'],
                            driver=network_info['driver'],
                            options=network_info.get('options', {}),
                            labels=network_info.get('labels', {})
                        )
                        self.console.print(f"[green]Restored network: {network_info['name']}[/green]")
                    except docker.errors.APIError as e:
                        if "already exists" in str(e):
                            continue
                        self.logger.warning(f"Failed to restore network {network_info['name']}: {e}")
            
            # Restore volumes
            if (backup_dir / 'volumes.json').exists():
                with open(backup_dir / 'volumes.json', 'r') as f:
                    volumes = json.load(f)
                
                for volume_info in volumes:
                    try:
                        self.client.volumes.create(
                            name=volume_info['name'],
                            driver=volume_info['driver'],
                            labels=volume_info.get('labels', {})
                        )
                        self.console.print(f"[green]Restored volume: {volume_info['name']}[/green]")
                    except docker.errors.APIError as e:
                        if "already exists" in str(e):
                            continue
                        self.logger.warning(f"Failed to restore volume {volume_info['name']}: {e}")
            
            # Note: Images and containers would need more complex restoration logic
            # This is a simplified implementation
            self.console.print("[yellow]Note: Complete container restoration requires image availability[/yellow]")
            self.console.print("[yellow]Consider using docker save/load for complete image backup[/yellow]")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Restore failed: {e}")
            return False
