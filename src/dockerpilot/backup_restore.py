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
        """
        Restore container data from backup.
        
        Args:
            container_name: Name of the container to restore data to
            backup_path: Path to backup directory
        
        Returns:
            bool: True if restore successful
        """
        try:
            backup_dir = Path(backup_path)
            if not backup_dir.exists():
                self.console.print(f"[red]❌ Backup directory not found: {backup_path}[/red]")
                return False
            
            metadata_file = backup_dir / 'backup_metadata.json'
            if not metadata_file.exists():
                self.console.print(f"[red]❌ Backup metadata not found: {metadata_file}[/red]")
                return False
            
            with open(metadata_file, 'r') as f:
                backup_metadata = json.load(f)
            
            self.console.print(f"[cyan]📦 Restoring data for container '{container_name}' from backup...[/cyan]")
            self.console.print(f"[cyan]Backup created: {backup_metadata.get('backup_time', 'unknown')}[/cyan]")
            
            container = self.client.containers.get(container_name)
            mounts = container.attrs.get('Mounts', [])
            
            # Show loading indicator during restore
            with self._with_loading("Restoring container data"):
                # Restore each volume
                for volume_info in backup_metadata.get('volumes', []):
                    backup_file = Path(volume_info['backup_file'])
                    if not backup_file.exists():
                        # Try relative to backup_dir
                        backup_file = backup_dir / backup_file.name
                    
                    if not backup_file.exists():
                        self.console.print(f"[yellow]⚠️ Backup file not found: {volume_info['backup_file']}[/yellow]")
                        continue
                    
                    if volume_info['type'] == 'named_volume':
                        volume_name = volume_info['name']
                        self.console.print(f"[cyan]Restoring named volume: {volume_name}[/cyan]")
                        
                        try:
                            volume = self.client.volumes.get(volume_name)
                            volume_path = volume.attrs['Mountpoint']
                            
                            # Extract backup to volume
                            self._restore_from_tar(backup_file, volume_path)
                            self.console.print(f"[green]✅ Restored volume '{volume_name}'[/green]")
                        except Exception as e:
                            self.logger.error(f"Failed to restore volume {volume_name}: {e}")
                            self.console.print(f"[red]❌ Failed to restore volume '{volume_name}': {e}[/red]")
                            return False
                    
                    elif volume_info['type'] == 'bind_mount':
                        source_path = volume_info['source']
                        self.console.print(f"[cyan]Restoring bind mount: {source_path}[/cyan]")
                        
                        try:
                            if Path(source_path).exists():
                                # Backup existing data first
                                existing_backup = Path(source_path).parent / f"{Path(source_path).name}.backup_{int(time.time())}"
                                if Path(source_path).is_dir():
                                    import shutil
                                    shutil.move(str(source_path), str(existing_backup))
                                    Path(source_path).mkdir(parents=True, exist_ok=True)
                                
                                # Extract backup
                                self._restore_from_tar(backup_file, source_path)
                                self.console.print(f"[green]✅ Restored bind mount '{source_path}'[/green]")
                            else:
                                self.console.print(f"[yellow]⚠️ Bind mount path does not exist: {source_path}[/yellow]")
                        except Exception as e:
                            self.logger.error(f"Failed to restore bind mount {source_path}: {e}")
                            self.console.print(f"[red]❌ Failed to restore bind mount '{source_path}': {e}[/red]")
                            return False
            
            self.console.print(f"[bold green]✅ Data restore completed![/bold green]")
            return True
            
        except docker.errors.NotFound:
            self.console.print(f"[red]❌ Container '{container_name}' not found[/red]")
            return False
        except Exception as e:
            self.logger.error(f"Container data restore failed: {e}")
            self.console.print(f"[red]❌ Restore failed: {e}[/red]")
            return False
    
    def _restore_from_tar(self, tar_file: Path, destination: str) -> bool:
        """Extract tar.gz file to destination"""
        try:
            import tarfile
            
            destination_path = Path(destination)
            destination_path.mkdir(parents=True, exist_ok=True)
            
            with tarfile.open(tar_file, 'r:gz') as tar:
                tar.extractall(path=destination_path.parent)
            
            return True
        except Exception as e:
            self.logger.error(f"Failed to extract tar backup: {e}")
            return False
    
    def _migrate_container_data(self, source_container, target_container, config: DeploymentConfig) -> bool:
        """Migrate data from source container to target container during blue-green deployment
        
        This function copies data from the active container to the new container to ensure
        data persistence during blue-green deployment.
        
        Args:
            source_container: The active (source) container to migrate data from
            target_container: The new (target) container to migrate data to
            config: Deployment configuration
            
        Returns:
            bool: True if migration successful or not needed, False on error
        """
        try:
            if not source_container:
                self.logger.info("No source container to migrate data from")
                return True
            
            self.console.print(f"[cyan]📦 Migrating data from '{source_container.name}' to '{target_container.name}'...[/cyan]")
            
            # Get mounts from both containers
            source_mounts = source_container.attrs.get('Mounts', [])
            target_mounts = target_container.attrs.get('Mounts', [])
            
            if not source_mounts:
                self.logger.info("No mounts in source container to migrate")
                return True
            
            # Create mapping of mount points
            source_volumes = {}
            for mount in source_mounts:
                volume_name = mount.get('Name')
                mount_point = mount.get('Destination')
                source_path = mount.get('Source')
                
                if volume_name:
                    source_volumes[mount_point] = {'type': 'named_volume', 'name': volume_name, 'source': None}
                elif source_path:
                    source_volumes[mount_point] = {'type': 'bind_mount', 'name': None, 'source': source_path}
            
            target_volumes = {}
            for mount in target_mounts:
                volume_name = mount.get('Name')
                mount_point = mount.get('Destination')
                source_path = mount.get('Source')
                
                if volume_name:
                    target_volumes[mount_point] = {'type': 'named_volume', 'name': volume_name, 'source': None}
                elif source_path:
                    target_volumes[mount_point] = {'type': 'bind_mount', 'name': None, 'source': source_path}
            
            # System paths to skip
            system_paths_to_skip = [
                '/lib/modules', '/proc', '/sys', '/dev', '/run', '/tmp', '/var/run', '/boot'
            ]
            
            migrated_count = 0
            skipped_count = 0
            
            # Migrate each volume
            for mount_point, source_info in source_volumes.items():
                # Skip system paths
                skip = False
                if source_info['source']:
                    for system_path in system_paths_to_skip:
                        if str(source_info['source']).startswith(system_path):
                            skip = True
                            break
                
                if skip:
                    skipped_count += 1
                    continue
                
                # Check if target has the same mount point
                if mount_point not in target_volumes:
                    self.logger.warning(f"Mount point '{mount_point}' not found in target container, skipping")
                    continue
                
                target_info = target_volumes[mount_point]
                
                # Handle named volumes - copy data between volumes
                if source_info['type'] == 'named_volume' and target_info['type'] == 'named_volume':
                    source_volume_name = source_info['name']
                    target_volume_name = target_info['name']
                    
                    # If volumes are the same, no migration needed
                    if source_volume_name == target_volume_name:
                        self.logger.info(f"Volume '{source_volume_name}' is shared, no migration needed")
                        continue
                    
                    self.console.print(f"[cyan]Migrating named volume: {source_volume_name} -> {target_volume_name}[/cyan]")
                    
                    # Copy data using Docker container
                    success = self._copy_volume_data(source_volume_name, target_volume_name, config.container_name)
                    if success:
                        migrated_count += 1
                        self.console.print(f"[green]✅ Migrated volume '{source_volume_name}' to '{target_volume_name}'[/green]")
                    else:
                        self.logger.warning(f"Failed to migrate volume '{source_volume_name}', continuing...")
                
                # Handle bind mounts - check if same source path (data is already shared)
                elif source_info['type'] == 'bind_mount' and target_info['type'] == 'bind_mount':
                    source_path = source_info['source']
                    target_path = target_info['source']
                    
                    # If same path, data is already available
                    if source_path == target_path:
                        self.logger.info(f"Bind mount '{source_path}' is shared, no migration needed")
                        continue
                    
                    # If different paths, copy data
                    self.console.print(f"[cyan]Migrating bind mount: {source_path} -> {target_path}[/cyan]")
                    
                    if Path(source_path).exists():
                        success = self._copy_bind_mount_data(source_path, target_path, config.container_name)
                        if success:
                            migrated_count += 1
                            self.console.print(f"[green]✅ Migrated bind mount '{source_path}' to '{target_path}'[/green]")
                        else:
                            self.logger.warning(f"Failed to migrate bind mount '{source_path}', continuing...")
                    else:
                        self.logger.warning(f"Source bind mount path does not exist: {source_path}")
            
            # Copy internal configuration files for databases
            db_config = self._get_database_config(config.image_tag)
            
            if db_config:
                self.console.print(f"[cyan]📋 Detected database container, migrating configuration files...[/cyan]")
                
                # Get config paths from database configuration
                config_paths = db_config.get('config_paths', [])
                
                for config_path in config_paths:
                    success = self._copy_container_files(source_container, target_container, config_path, config.container_name)
                    if success:
                        self.console.print(f"[green]✅ Migrated config from '{config_path}'[/green]")
            
            self.console.print(f"[green]✅ Data migration completed: {migrated_count} volumes migrated, {skipped_count} skipped[/green]")
            return True
            
        except Exception as e:
            self.logger.error(f"Data migration failed: {e}")
            self.console.print(f"[yellow]⚠️ Data migration failed: {e}, continuing deployment...[/yellow]")
            # Don't fail deployment if migration fails - just log warning
            return True  # Return True to not block deployment
    
    def _copy_volume_data(self, source_volume_name: str, target_volume_name: str, container_name: str = None) -> bool:
        """Copy data from source named volume to target named volume using Docker"""
        try:
            import subprocess
            
            # Use Docker container to copy data between volumes
            # This runs as root inside container, so no permission issues
            result = subprocess.run(
                [
                    'docker', 'run', '--rm',
                    '-v', f'{source_volume_name}:/source:ro',  # Mount source volume as read-only
                    '-v', f'{target_volume_name}:/target',      # Mount target volume
                    'alpine:latest',  # Lightweight image
                    'sh', '-c',
                    'cp -a /source/. /target/ 2>/dev/null || true'
                ],
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes timeout for large volumes
            )
            
            if result.returncode == 0:
                self.logger.info(f"Successfully copied data from volume '{source_volume_name}' to '{target_volume_name}'")
                return True
            else:
                self.logger.warning(f"Volume copy warnings: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"Volume copy timed out for {source_volume_name} -> {target_volume_name}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to copy volume data: {e}")
            return False
    
    def _copy_bind_mount_data(self, source_path: str, target_path: str, container_name: str = None) -> bool:
        """Copy data from source bind mount path to target bind mount path"""
        try:
            import subprocess
            import shutil
            
            source = Path(source_path)
            target = Path(target_path)
            
            if not source.exists():
                self.logger.warning(f"Source path does not exist: {source_path}")
                return False
            
            # Create target directory if it doesn't exist
            target.mkdir(parents=True, exist_ok=True)
            
            # Use rsync if available, otherwise use cp
            if shutil.which('rsync'):
                result = subprocess.run(
                    ['rsync', '-a', '--info=progress2', f'{source_path}/', f'{target_path}/'],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
            else:
                result = subprocess.run(
                    ['cp', '-a', f'{source_path}/.', f'{target_path}/'],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
            
            if result.returncode == 0:
                self.logger.info(f"Successfully copied bind mount data from '{source_path}' to '{target_path}'")
                return True
            else:
                self.logger.warning(f"Bind mount copy failed: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to copy bind mount data: {e}")
            return False
    
    def _copy_container_files(self, source_container, target_container, source_path: str, container_name: str = None) -> bool:
        """Copy files from source container to target container using docker cp"""
        try:
            import subprocess
            import tempfile
            
            # Create temporary tar file
            with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as tmp_tar:
                tmp_tar_path = tmp_tar.name
            
            try:
                # Copy from source container to tar
                result1 = subprocess.run(
                    ['docker', 'cp', f'{source_container.name}:{source_path}', '-'],
                    stdout=open(tmp_tar_path, 'wb'),
                    stderr=subprocess.PIPE,
                    timeout=300
                )
                
                if result1.returncode != 0:
                    self.logger.warning(f"Failed to copy from source container: {result1.stderr.decode()}")
                    return False
                
                # Extract tar to target container
                result2 = subprocess.run(
                    ['docker', 'cp', '-', f'{target_container.name}:{Path(source_path).parent}/'],
                    stdin=open(tmp_tar_path, 'rb'),
                    stderr=subprocess.PIPE,
                    timeout=300
                )
                
                if result2.returncode == 0:
                    self.logger.info(f"Successfully copied files from '{source_path}' between containers")
                    return True
                else:
                    self.logger.warning(f"Failed to copy to target container: {result2.stderr.decode()}")
                    return False
                    
            finally:
                # Cleanup temp file
                try:
                    Path(tmp_tar_path).unlink()
                except:
                    pass
                    
        except Exception as e:
            self.logger.error(f"Failed to copy container files: {e}")
            return False

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
