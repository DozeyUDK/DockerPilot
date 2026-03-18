"""Backup and restore services extracted from DockerPilotEnhanced."""

from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import os
import subprocess
import time
import docker
from .models import DeploymentConfig


class BackupRestoreMixin:
    """Mixin containing backup/restore logic for DockerPilot."""

    def _check_sudo_required_for_backup(self, container_name: str) -> tuple[bool, list[str], dict]:
        """Check if backup will require sudo access and get mount information
        
        Returns:
            tuple: (requires_sudo: bool, privileged_paths: list[str], mount_info: dict)
                   mount_info contains: {'large_mounts': list, 'total_size_gb': float, 'mounts': list}
        """
        try:
            import shutil
            
            container = self.client.containers.get(container_name)
            mounts = container.attrs.get('Mounts', [])
            
            privileged_paths = []
            large_mounts = []  # Mounts > 1TB
            mount_info_list = []  # List of all mounts with size info
            total_size_bytes = 0
            
            for mount in mounts:
                source = mount.get('Source')
                if source:
                    source_path = Path(source)
                    requires_sudo = (
                        str(source).startswith('/var/lib/docker/volumes/') or
                        str(source).startswith('/var/lib/docker/') or
                        str(source).startswith('/root/') or
                        not os.access(source, os.R_OK)
                    )
                    
                    if requires_sudo:
                        privileged_paths.append(source)
                    
                    # Check mount size (if it's a directory)
                    mount_size_bytes = 0
                    mount_size_gb = 0
                    mount_total_capacity_gb = 0  # Total disk capacity
                    is_large = False
                    
                    try:
                        if source_path.exists() and source_path.is_dir():
                            # First, check total disk capacity using df (faster than du for large dirs)
                            try:
                                df_result = subprocess.run(
                                    ['df', '-B1', str(source_path)],  # -B1 = block size 1 byte
                                    capture_output=True,
                                    timeout=5,
                                    text=True
                                )
                                if df_result.returncode == 0:
                                    # Parse df output: Filesystem Size Used Avail Use% Mounted
                                    lines = df_result.stdout.strip().split('\n')
                                    if len(lines) > 1:
                                        parts = lines[1].split()
                                        if len(parts) >= 2:
                                            mount_total_capacity_gb = int(parts[1]) / (1024 ** 3)
                            except (subprocess.TimeoutExpired, ValueError, IndexError, FileNotFoundError):
                                # df failed, continue with du
                                pass
                            
                            # Use du command for actual used size (faster than Python for large dirs)
                            try:
                                result = subprocess.run(
                                    ['du', '-sb', str(source_path)],
                                    capture_output=True,
                                    timeout=30,  # Increased timeout for large directories
                                    text=True
                                )
                                if result.returncode == 0:
                                    mount_size_bytes = int(result.stdout.split()[0])
                                    mount_size_gb = mount_size_bytes / (1024 ** 3)
                                    total_size_bytes += mount_size_bytes
                                    
                                    # Consider large if:
                                    # - Used space > 500GB (more reasonable threshold)
                                    # - OR total disk capacity > 1TB (even if not fully used)
                                    is_large = mount_size_gb > 500 or mount_total_capacity_gb > 1024
                                    
                                    if is_large:
                                        large_mounts.append({
                                            'path': source,
                                            'size_gb': mount_size_gb,
                                            'size_tb': mount_size_gb / 1024,
                                            'total_capacity_gb': mount_total_capacity_gb,
                                            'total_capacity_tb': mount_total_capacity_gb / 1024
                                        })
                            except (subprocess.TimeoutExpired, ValueError, IndexError):
                                # If du fails or times out, check if we have capacity info from df
                                if mount_total_capacity_gb > 1024:
                                    is_large = True
                                    large_mounts.append({
                                        'path': source,
                                        'size_gb': 0,  # Unknown actual size
                                        'size_tb': 0,
                                        'total_capacity_gb': mount_total_capacity_gb,
                                        'total_capacity_tb': mount_total_capacity_gb / 1024,
                                        'note': 'Size check timed out, but disk capacity is large'
                                    })
                                # Don't try shutil fallback for large dirs - it's too slow
                    except Exception as e:
                        self.logger.debug(f"Could not check size for {source}: {e}")
                    
                    mount_info_list.append({
                        'path': source,
                        'mount_point': mount.get('Destination'),
                        'requires_sudo': requires_sudo,
                        'size_gb': mount_size_gb,
                        'total_capacity_gb': mount_total_capacity_gb,
                        'is_large': is_large
                    })
            
            total_size_gb = total_size_bytes / (1024 ** 3)
            
            mount_info = {
                'large_mounts': large_mounts,
                'total_size_gb': total_size_gb,
                'total_size_tb': total_size_gb / 1024,
                'mounts': mount_info_list
            }
            
            return len(privileged_paths) > 0, privileged_paths, mount_info
            
        except Exception as e:
            self.logger.warning(f"Could not check sudo requirements: {e}")
            return False, [], {'large_mounts': [], 'total_size_gb': 0, 'total_size_tb': 0, 'mounts': []}
    
    def find_existing_backup(self, container_name: str, max_age_hours: int = 24) -> Optional[Path]:
        """
        Find existing backup for container that is recent enough.
        
        Args:
            container_name: Name of the container to find backup for
            max_age_hours: Maximum age of backup in hours (default: 24)
        
        Returns:
            Path to backup directory if found, None otherwise
        """
        try:
            # Search in current directory and common backup locations
            search_paths = [
                Path('.'),
                Path.home() / '.dockerpilot_extras' / 'backups',
                Path('/tmp'),
            ]
            
            # Also search in parent directories for backups
            current_dir = Path.cwd()
            for parent in [current_dir] + list(current_dir.parents)[:3]:  # Check up to 3 levels up
                search_paths.append(parent)
            
            best_backup = None
            best_backup_time = None
            
            for search_path in search_paths:
                if not search_path.exists():
                    continue
                
                # Look for backup directories matching pattern
                pattern = f"backup_{container_name}_*"
                for backup_dir in search_path.glob(pattern):
                    if not backup_dir.is_dir():
                        continue
                    
                    # Check if backup metadata exists
                    metadata_file = backup_dir / 'backup_metadata.json'
                    if not metadata_file.exists():
                        continue
                    
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        
                        # Verify it's for the right container
                        if metadata.get('container_name') != container_name:
                            continue
                        
                        # Check backup age
                        backup_time_str = metadata.get('backup_time')
                        if backup_time_str:
                            backup_time = datetime.fromisoformat(backup_time_str.replace('Z', '+00:00'))
                            if backup_time.tzinfo is None:
                                # Assume local time if no timezone
                                backup_time = backup_time.replace(tzinfo=datetime.now().astimezone().tzinfo)
                            
                            age_hours = (datetime.now(backup_time.tzinfo) - backup_time).total_seconds() / 3600
                            
                            if age_hours <= max_age_hours:
                                # Check if all backup files exist
                                volumes = metadata.get('volumes', [])
                                all_files_exist = True
                                for vol in volumes:
                                    backup_file = Path(vol.get('backup_file', ''))
                                    if not backup_file.is_absolute():
                                        backup_file = backup_dir / backup_file.name
                                    if not backup_file.exists():
                                        all_files_exist = False
                                        break
                                
                                if all_files_exist:
                                    # This is a valid backup, check if it's newer than current best
                                    if best_backup is None or (backup_time > best_backup_time):
                                        best_backup = backup_dir
                                        best_backup_time = backup_time
                    except Exception as e:
                        self.logger.debug(f"Error reading backup metadata {metadata_file}: {e}")
                        continue
            
            return best_backup
            
        except Exception as e:
            self.logger.warning(f"Error searching for existing backup: {e}")
            return None
    
    def backup_container_data(self, container_name: str, backup_path: str = None, reuse_existing: bool = True, max_backup_age_hours: int = 24) -> bool:
        """
        Backup ALL data from container volumes (actual data, not just metadata).
        Creates a complete backup of all volumes mounted to the container.
        If reuse_existing is True, will check for existing recent backup first.
        
        Args:
            container_name: Name of the container to backup
            backup_path: Optional path for backup (auto-generated if not provided)
            reuse_existing: If True, reuse existing backup if found (default: True)
            max_backup_age_hours: Maximum age of backup to reuse in hours (default: 24)
        
        Returns:
            bool: True if backup successful or existing backup reused
        """
        try:
            container = self.client.containers.get(container_name)
            
            # Check for existing backup first if reuse_existing is True
            # Even if backup_path is provided, we check for existing backup first
            if reuse_existing:
                existing_backup = self.find_existing_backup(container_name, max_backup_age_hours)
                if existing_backup:
                    self.logger.info(f"Found existing backup for {container_name}: {existing_backup}")
                    self.console.print(f"[green]✅ Found existing backup: {existing_backup}[/green]")
                    
                    # Verify backup is complete
                    metadata_file = existing_backup / 'backup_metadata.json'
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, 'r') as f:
                                metadata = json.load(f)
                            
                            volumes = metadata.get('volumes', [])
                            total_size_mb = metadata.get('total_size', 0) / (1024 * 1024)
                            backup_time = metadata.get('backup_time', 'unknown')
                            
                            self.console.print(f"[green]Backup created: {backup_time}[/green]")
                            self.console.print(f"[green]Total size: {total_size_mb:.2f} MB[/green]")
                            self.console.print(f"[green]Volumes backed up: {len(volumes)}[/green]")
                            self.console.print(f"[cyan]ℹ️ Reusing existing backup instead of creating new one[/cyan]")
                            
                            # Set backup_path to the existing backup path for consistency
                            backup_path = str(existing_backup)
                            return True
                        except Exception as e:
                            self.logger.warning(f"Error reading existing backup metadata: {e}")
                            # Continue to create new backup
            
            if not backup_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = f"backup_{container_name}_{timestamp}"
            
            backup_dir = Path(backup_path)
            backup_dir.mkdir(exist_ok=True, parents=True)
            
            # Pre-check: will we need sudo?
            requires_sudo, privileged_paths, mount_info = self._check_sudo_required_for_backup(container_name)
            
            if requires_sudo:
                self.console.print(f"[yellow]⚠️  BACKUP REQUIRES SUDO ACCESS[/yellow]")
                self.console.print(f"[yellow]Privileged paths ({len(privileged_paths)}):[/yellow]")
                for path in privileged_paths[:3]:  # Show first 3
                    self.console.print(f"[dim]  - {path}[/dim]")
                if len(privileged_paths) > 3:
                    self.console.print(f"[dim]  ... and {len(privileged_paths) - 3} more[/dim]")
                self.console.print(f"[yellow]You may be prompted for sudo password during backup.[/yellow]")
                self.console.print(f"[yellow]To skip backup: use --skip-backup flag[/yellow]")
                
                # Give user 3 seconds to cancel
                import sys
                for i in range(3, 0, -1):
                    sys.stdout.write(f"\rContinuing in {i}s... (Ctrl+C to cancel)")
                    sys.stdout.flush()
                    time.sleep(1)
                sys.stdout.write("\r" + " " * 50 + "\r")  # Clear line
            
            self.console.print(f"[cyan]📦 Creating data backup for container '{container_name}'...[/cyan]")
            
            # Get container mounts
            mounts = container.attrs.get('Mounts', [])
            
            if not mounts:
                self.console.print(f"[yellow]⚠️ No volumes mounted to container '{container_name}'[/yellow]")
                return True  # Not an error, just no data to backup
            
            # System paths that should be skipped during backup (they can hang or are too large)
            system_paths_to_skip = [
                '/',  # Root filesystem - NEVER backup this!
                '/lib/modules',
                '/proc',
                '/sys',
                '/dev',
                '/run',
                '/tmp',
                '/var/run',
                '/boot',
                '/usr',
                '/bin',
                '/sbin',
                '/etc',
                '/var/lib',
                '/var/log',
            ]
            
            # Show loading indicator during backup
            with self._with_loading("Backing up container data"):
                # Backup each volume
                backed_up_volumes = []
                total_mounts = len([m for m in mounts if m.get('Source') or m.get('Name')])
                processed_mounts = 0
                
                for mount in mounts:
                    # Check for cancellation before each mount backup
                    if self._check_cancel_flag(container_name):
                        self.logger.warning(f"Backup cancelled by user for {container_name}")
                        self.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
                        return False
                    
                    volume_name = mount.get('Name')
                    mount_point = mount.get('Destination')  # Path inside container
                    source = mount.get('Source')  # Path on host (for bind mounts)
                    
                    # Skip system paths and root filesystem FIRST
                    if source:
                        source_path = Path(source)
                        
                        # ALWAYS skip root filesystem - this is critical!
                        if str(source_path) == '/' or str(source_path).resolve() == Path('/'):
                            self.logger.warning(f"Skipping root filesystem bind mount: {source} -> {mount_point} (CRITICAL: root filesystem should never be backed up)")
                            self.console.print(f"[red]⚠️ SKIPPING root filesystem bind mount '{source}' (root filesystem should never be backed up!)[/red]")
                            continue
                    
                    # For migration: Skip external data mounts (not container-specific data)
                    # Named volumes are always backed up (they're container-specific)
                    # Bind mounts to external storage (/mnt/*, /media/*) should be skipped
                    # Bind mounts to application directories (/opt/*, /var/www/*) should be backed up
                    if source and not volume_name:
                        # This is a bind mount (not a named volume)
                        source_path = Path(source)
                        
                        # Skip external storage mounts (these are not container data, just mounted storage)
                        external_storage_patterns = [
                            '/mnt/',  # External mounts like /mnt/sdc_share, /mnt/sda3, etc.
                            '/media/',  # Removable media
                        ]
                        
                        # Check if this is an external storage mount
                        is_external_storage = any(
                            str(source_path).startswith(pattern) 
                            for pattern in external_storage_patterns
                        )
                        
                        # Exception: application directories in /opt, /var/www, etc. should be backed up
                        application_patterns = [
                            '/opt/',  # Application installations
                            '/var/www/',  # Web applications
                            '/var/lib/',  # Application data (but not /var/lib/docker)
                            '/srv/',  # Service data
                        ]
                        
                        is_application_data = any(
                            str(source_path).startswith(pattern) 
                            for pattern in application_patterns
                        ) and not str(source_path).startswith('/var/lib/docker')
                        
                        if is_external_storage and not is_application_data:
                            self.logger.info(f"Skipping external storage bind mount: {source} -> {mount_point} (external storage, not container data)")
                            self.console.print(f"[cyan]ℹ️ Skipping external disk '{source}' (this is not container data, just a mounted disk)[/cyan]")
                            continue
                    
                    # Skip system paths
                    if source:
                        source_path = Path(source)
                        
                        # Check if source is a system path to skip
                        skip_mount = False
                        for system_path in system_paths_to_skip:
                            if str(source_path) == system_path or str(source_path).startswith(system_path + '/'):
                                self.logger.warning(f"Skipping system bind mount: {source} -> {mount_point}")
                                self.console.print(f"[yellow]⚠️ Skipping system bind mount '{source}' (system path)[/yellow]")
                                skip_mount = True
                                break
                        
                        if skip_mount:
                            continue
                        
                        # Check if mount is very large (> 1TB) - warn but don't skip automatically
                        # User should have been warned in the modal, but double-check here
                        try:
                            # Quick size check using du (with timeout to avoid hanging)
                            result = subprocess.run(
                                ['du', '-sb', str(source_path)],
                                capture_output=True,
                                timeout=5,  # 5 second timeout for size check
                                text=True
                            )
                            if result.returncode == 0:
                                size_bytes = int(result.stdout.split()[0])
                                size_tb = size_bytes / (1024 ** 4)
                                if size_tb > 1:
                                    self.logger.warning(f"Large mount detected: {source} ({size_tb:.2f} TB) - this will take a very long time to backup")
                                    self.console.print(f"[yellow]⚠️ Large mount detected: {source} ({size_tb:.2f} TB)[/yellow]")
                                    self.console.print(f"[yellow]   This backup may take many hours. Consider skipping this mount.[/yellow]")
                        except (subprocess.TimeoutExpired, ValueError, IndexError, FileNotFoundError):
                            # If size check fails or times out, continue anyway
                            # (might be a network mount or permission issue)
                            pass
                    
                    if volume_name:
                        # Named volume - backup using Docker container (no sudo needed!)
                        self.console.print(f"[cyan]Backing up named volume: {volume_name} -> {mount_point}[/cyan]")
                        try:
                            backup_file = backup_dir / f"{volume_name}.tar.gz"
                            
                            # Update progress for volume backup
                            if container_name:
                                progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                                self._update_progress('backup', progress_pct, f'📦 Creating backup of volume: {volume_name}...')
                            
                            # Use Docker to backup volume (runs as root inside container)
                            # This avoids permission issues without requiring sudo
                            success = self._backup_volume_using_docker(volume_name, backup_file, container_name)
                            
                            # Check for cancellation after backup
                            if self._check_cancel_flag(container_name):
                                self.logger.warning(f"Backup cancelled by user for {container_name}")
                                self.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
                                return False
                            
                            if success:
                                processed_mounts += 1
                                backed_up_volumes.append({
                                    'type': 'named_volume',
                                    'name': volume_name,
                                    'mount_point': mount_point,
                                    'backup_file': str(backup_file),
                                    'size': backup_file.stat().st_size if backup_file.exists() else 0
                                })
                                self.console.print(f"[green]✅ Backed up volume '{volume_name}' to {backup_file}[/green]")
                                
                                # Update progress after successful backup
                                if container_name:
                                    progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                                    self._update_progress('backup', progress_pct, f'✅ Zbackupowano volume: {volume_name} ({processed_mounts}/{total_mounts})')
                            else:
                                self.logger.warning(f"Failed to backup volume {volume_name}, continuing...")
                                self.console.print(f"[yellow]⚠️ Failed to backup volume '{volume_name}', continuing...[/yellow]")
                                # Don't return False - continue with other volumes
                        except Exception as e:
                            self.logger.error(f"Failed to backup volume {volume_name}: {e}")
                            self.console.print(f"[yellow]⚠️ Failed to backup volume '{volume_name}': {e}, continuing...[/yellow]")
                            # Don't return False - continue with other volumes
                
                    elif source:
                        # Bind mount - backup using Docker container (faster and no sudo needed for many paths)
                        self.console.print(f"[cyan]Backing up bind mount: {source} -> {mount_point}[/cyan]")
                        # Update progress for bind mount backup
                        if container_name:
                            source_name = Path(source).name
                            progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                            self._update_progress('backup', progress_pct, f'📦 Creating backup of bind mount: {source_name}...')
                        try:
                            if Path(source).exists():
                                # Create safe filename from path
                                safe_name = source.replace('/', '_').replace('\\', '_').strip('_')
                                backup_file = backup_dir / f"bind_{safe_name}.tar.gz"
                                
                                # Use Docker container for backup (faster, no sudo needed, better for large directories)
                                success = self._backup_bind_mount_using_docker(source, backup_file, container_name)
                                
                                # Check for cancellation after backup
                                if self._check_cancel_flag(container_name):
                                    self.logger.warning(f"Backup cancelled by user for {container_name}")
                                    self.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
                                    return False
                                
                                if success:
                                    processed_mounts += 1
                                    backed_up_volumes.append({
                                        'type': 'bind_mount',
                                        'source': source,
                                        'mount_point': mount_point,
                                        'backup_file': str(backup_file),
                                        'size': backup_file.stat().st_size if backup_file.exists() else 0
                                    })
                                    self.console.print(f"[green]✅ Backed up bind mount '{source}' to {backup_file}[/green]")
                                    
                                    # Update progress after successful backup
                                    if container_name:
                                        progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                                        self._update_progress('backup', progress_pct, f'✅ Zbackupowano bind mount: {source_name} ({processed_mounts}/{total_mounts})')
                                else:
                                    self.logger.warning(f"Failed to backup bind mount {source}, continuing...")
                                    self.console.print(f"[yellow]⚠️ Failed to backup bind mount '{source}', continuing...[/yellow]")
                                    # Don't return False - continue with other volumes
                            else:
                                self.logger.warning(f"Bind mount source does not exist: {source}")
                                self.console.print(f"[yellow]⚠️ Bind mount source not found: {source}[/yellow]")
                        except Exception as e:
                            self.logger.error(f"Failed to backup bind mount {source}: {e}")
                            self.console.print(f"[yellow]⚠️ Failed to backup bind mount '{source}': {e}, continuing...[/yellow]")
                            # Don't return False - continue with other volumes
                
                # Save backup metadata (inside loading context)
                if container_name:
                    self._update_progress('backup', 18, '💾 Saving backup metadata...')
                
                backup_metadata = {
                    'container_name': container_name,
                    'backup_time': datetime.now().isoformat(),
                    'container_image': container.image.tags[0] if container.image.tags else container.image.id,
                    'volumes': backed_up_volumes,
                    'total_size': sum(v.get('size', 0) for v in backed_up_volumes)
                }
                
                metadata_file = backup_dir / 'backup_metadata.json'
                with open(metadata_file, 'w') as f:
                    json.dump(backup_metadata, f, indent=2)
                
                # Final progress update
                if container_name:
                    self._update_progress('backup', 20, '✅ Backup completed')
            
            # Show results after loading completes
            total_size_mb = sum(v.get('size', 0) for v in backed_up_volumes) / (1024 * 1024)
            self.console.print(f"[bold green]✅ Data backup completed![/bold green]")
            self.console.print(f"[green]Backup location: {backup_path}[/green]")
            self.console.print(f"[green]Total size: {total_size_mb:.2f} MB[/green]")
            self.console.print(f"[green]Volumes backed up: {len(backed_up_volumes)}[/green]")
            
            return True
            
        except docker.errors.NotFound:
            self.console.print(f"[red]❌ Container '{container_name}' not found[/red]")
            return False
        except Exception as e:
            self.logger.error(f"Container data backup failed: {e}")
            self.console.print(f"[red]❌ Backup failed: {e}[/red]")
            return False
    
    def _backup_volume_using_docker(self, volume_name: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup Docker volume using a temporary container (no sudo needed!)
        
        This method uses Docker itself to backup volumes, avoiding permission issues.
        The container runs as root and can access the volume data.
        
        Args:
            volume_name: Name of the Docker volume to backup
            backup_file: Path to the backup file to create
            container_name: Container name for cancel flag checking
        """
        try:
            import subprocess
            import signal
            
            # Get current user UID and GID for ownership fix
            uid = os.getuid()
            gid = os.getgid()
            
            # Use docker run to create tar backup of volume
            # This runs as root inside container, so no permission issues
            # We also fix ownership of the backup file
            process = subprocess.Popen(
                [
                    'docker', 'run', '--rm',
                    '-v', f'{volume_name}:/volume:ro',  # Mount volume as read-only
                    '-v', f'{backup_file.parent.absolute()}:/backup',  # Mount backup dir
                    'alpine:latest',  # Lightweight image
                    'sh', '-c',
                    f'tar -czf /backup/{backup_file.name} -C /volume . 2>/dev/null'
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait with periodic cancel checks and progress updates
            timeout = 600  # 10 minutes timeout (large volumes like influxdb2)
            start_time = time.time()
            check_interval = 2  # Check cancel flag every 2 seconds
            last_size = 0
            last_log_time = start_time
            log_interval = 10  # Log progress every 10 seconds
            
            self.logger.info(f"Starting backup of volume '{volume_name}' (timeout: {timeout}s)")
            
            last_progress_update = 0
            progress_update_interval = 5  # Update progress every 5 seconds
            
            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    self.logger.error(f"Volume backup timed out for {volume_name} after {elapsed:.1f}s")
                    if container_name:
                        self._update_progress('backup', 95, f'❌ Backup timeout for volume: {volume_name}')
                    # Clean up any orphaned backup containers
                    self._cleanup_backup_containers()
                    return False
                
                # Check for cancellation
                if container_name and self._check_cancel_flag(container_name):
                    self.logger.warning(f"Backup cancelled during volume backup: {volume_name}")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    if container_name:
                        progress_pct = min(90, int((elapsed / timeout) * 100))
                        self._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {volume_name}')
                    # Clean up any orphaned backup containers
                    self._cleanup_backup_containers()
                    return False
                
                # Check if process finished
                if process.poll() is not None:
                    self.logger.info(f"Volume backup completed for '{volume_name}' in {elapsed:.1f}s")
                    break
                
                # Update progress periodically (for web interface)
                if container_name and elapsed - last_progress_update >= progress_update_interval:
                    current_size = backup_file.stat().st_size if backup_file.exists() else 0
                    size_mb = current_size / (1024 * 1024) if current_size > 0 else 0
                    progress_pct = min(90, int((elapsed / timeout) * 100))
                    self._update_progress('backup', progress_pct, f'📦 Creating backup of volume: {volume_name}... ({int(elapsed)}s, {size_mb:.1f} MB)')
                    last_progress_update = elapsed
                
                # Log progress periodically (for console)
                if time.time() - last_log_time >= log_interval:
                    current_size = backup_file.stat().st_size if backup_file.exists() else 0
                    size_mb = current_size / (1024 * 1024) if current_size > 0 else 0
                    progress_pct = min(95, int((elapsed / timeout) * 100))
                    
                    if current_size > last_size:
                        self.logger.info(f"Backup progress: {progress_pct}% | Elapsed: {elapsed:.1f}s | Size: {size_mb:.1f} MB | Volume: {volume_name}")
                        last_size = current_size
                    else:
                        self.logger.info(f"Backup progress: {progress_pct}% | Elapsed: {elapsed:.1f}s | Volume: {volume_name}")
                    last_log_time = time.time()
                
                # Wait a bit before next check
                time.sleep(check_interval)
            
            # Get result
            stdout, stderr = process.communicate()
            returncode = process.returncode
            
            # Fix ownership of backup file after container finishes
            if returncode == 0:
                if backup_file.exists():
                    try:
                        # Try to fix ownership - first try without sudo, then with sudo if needed
                        try:
                            # Try direct chown first (might work if file is already accessible)
                            os.chown(backup_file, uid, gid)
                            self.logger.debug(f"Fixed ownership of {backup_file} without sudo")
                        except (PermissionError, OSError):
                            # If direct chown fails, try with sudo if password is available
                            if hasattr(self, '_sudo_password') and self._sudo_password:
                                self._run_sudo_command(['chown', f'{uid}:{gid}', str(backup_file)], timeout=10)
                                self.logger.debug(f"Fixed ownership of {backup_file} with sudo")
                            else:
                                # No sudo password available - log warning but don't fail
                                # Backup was successful, ownership is just a convenience
                                self.logger.warning(f"Could not fix ownership of {backup_file} - no sudo password available")
                                self.logger.warning("Backup file may be owned by root - this is not critical, backup was successful")
                    except Exception as e:
                        # Any other error - log but don't fail (backup was successful)
                        self.logger.warning(f"Could not fix ownership of {backup_file}: {e} - may require manual chown")
                
                self.logger.info(f"Volume {volume_name} backed up successfully using Docker")
                if container_name:
                    self._update_progress('backup', 90, f'✅ Zbackupowano volume: {volume_name}')
                return True
            else:
                # Log stderr but don't fail on socket warnings
                if stderr and 'socket ignored' not in stderr:
                    self.logger.warning(f"Docker volume backup warnings: {stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Docker volume backup failed: {e}")
            return False
    
    def _cleanup_backup_containers(self):
        """Clean up any leftover backup containers (alpine:latest) that may have been orphaned
        
        This method finds and removes containers using alpine:latest image that are in exited state
        or have been running for too long. These are typically backup containers that weren't
        properly cleaned up due to process interruption.
        """
        try:
            if not self.client:
                return
            
            # Find all containers using alpine:latest image
            # These are likely backup containers that weren't cleaned up
            all_containers = self.client.containers.list(all=True, filters={'ancestor': 'alpine:latest'})
            
            cleaned = 0
            for container in all_containers:
                try:
                    container.reload()  # Refresh container state
                    
                    # Remove exited containers (these are definitely orphaned)
                    if container.status == 'exited':
                        container.remove()
                        cleaned += 1
                        self.logger.debug(f"Cleaned up exited backup container: {container.id[:12]}")
                    elif container.status == 'running':
                        # Check how long it's been running
                        # Normal backup containers should finish in seconds/minutes
                        try:
                            created_str = container.attrs.get('Created', '')
                            if created_str:
                                # Docker timestamps are in ISO format: "2025-12-21T23:22:24.123456789Z"
                                # Remove microseconds and timezone for simpler parsing
                                created_str_clean = created_str.split('.')[0].replace('Z', '')
                                created_time = datetime.strptime(created_str_clean, '%Y-%m-%dT%H:%M:%S')
                                running_time = (datetime.now() - created_time).total_seconds()
                                
                                # If running for more than 10 minutes, it's likely orphaned
                                if running_time > 600:
                                    container.stop(timeout=5)
                                    container.remove()
                                    cleaned += 1
                                    self.logger.debug(f"Cleaned up orphaned backup container (running {running_time:.0f}s): {container.id[:12]}")
                        except (ValueError, TypeError, KeyError) as e:
                            # If we can't parse the timestamp, check if container has been running too long
                            # by checking its uptime attribute if available
                            try:
                                uptime_str = container.attrs.get('State', {}).get('StartedAt', '')
                                if uptime_str:
                                    uptime_clean = uptime_str.split('.')[0].replace('Z', '')
                                    started_time = datetime.strptime(uptime_clean, '%Y-%m-%dT%H:%M:%S')
                                    running_time = (datetime.now() - started_time).total_seconds()
                                    if running_time > 600:
                                        container.stop(timeout=5)
                                        container.remove()
                                        cleaned += 1
                                        self.logger.debug(f"Cleaned up orphaned backup container (running {running_time:.0f}s): {container.id[:12]}")
                            except (ValueError, TypeError, KeyError):
                                # If we still can't determine, just log and skip
                                self.logger.debug(f"Could not determine container age, skipping: {container.id[:12]}")
                except docker.errors.NotFound:
                    # Container was already removed, skip
                    pass
                except Exception as e:
                    self.logger.debug(f"Could not clean up container {container.id[:12]}: {e}")
            
            if cleaned > 0:
                self.logger.info(f"Cleaned up {cleaned} orphaned backup container(s)")
        except Exception as e:
            self.logger.debug(f"Error cleaning up backup containers: {e}")
    
    def _backup_bind_mount_using_docker(self, source_path: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup bind mount directory using a temporary Docker container (no sudo needed!)
        
        This method uses Docker container to backup directories, which is faster and avoids
        permission issues. The container runs as root and can access the directory data.
        
        Args:
            source_path: Path to the directory on host to backup
            backup_file: Path to the backup file to create
            container_name: Container name for cancel flag checking
        """
        try:
            import subprocess
            
            source = Path(source_path)
            if not source.exists():
                self.logger.warning(f"Source path does not exist: {source_path}")
                return False
            
            # Get current user UID and GID for ownership fix
            uid = os.getuid()
            gid = os.getgid()
            
            # Use docker run to create tar backup of directory
            # This runs as root inside container, so no permission issues
            # Mount the parent directory and backup the child directory name
            source_parent = str(source.parent.absolute())
            source_name = source.name
            
            process = subprocess.Popen(
                [
                    'docker', 'run', '--rm',
                    '-v', f'{source_parent}:/source:ro',  # Mount parent dir as read-only
                    '-v', f'{backup_file.parent.absolute()}:/backup',  # Mount backup dir
                    'alpine:latest',  # Lightweight image
                    'sh', '-c',
                    f'tar -czf /backup/{backup_file.name} -C /source {source_name} 2>/dev/null || true'
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait with periodic cancel checks and progress updates
            timeout = 600  # 10 minutes timeout (large directories like influxdb)
            start_time = time.time()
            check_interval = 2  # Check cancel flag every 2 seconds
            last_progress_update = 0
            progress_update_interval = 5  # Update progress every 5 seconds
            
            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    self.logger.error(f"Bind mount backup timed out for {source_path}")
                    # Clean up any orphaned backup containers
                    self._cleanup_backup_containers()
                    if container_name:
                        self._update_progress('backup', 95, f'❌ Backup timeout for {source.name}')
                    return False
                
                # Check for cancellation
                if container_name and self._check_cancel_flag(container_name):
                    self.logger.warning(f"Backup cancelled during bind mount backup: {source_path}")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    # Clean up any orphaned backup containers
                    self._cleanup_backup_containers()
                    if container_name:
                        progress_pct = min(90, int((elapsed / timeout) * 100))
                        self._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {source.name}')
                    return False
                
                # Update progress periodically during backup
                if container_name and elapsed - last_progress_update >= progress_update_interval:
                    progress_pct = min(90, int((elapsed / timeout) * 100))
                    self._update_progress('backup', progress_pct, f'📦 Creating backup of {source.name}... ({int(elapsed)}s)')
                    last_progress_update = elapsed
                
                # Check if process finished
                if process.poll() is not None:
                    break
                
                # Wait a bit before next check
                time.sleep(check_interval)
            
            # Get result
            stdout, stderr = process.communicate()
            returncode = process.returncode
            
            # Fix ownership of backup file after container finishes
            if returncode == 0 and backup_file.exists():
                try:
                    # Try to fix ownership - first try without sudo, then with sudo if needed
                    try:
                        # Try direct chown first (might work if file is already accessible)
                        os.chown(backup_file, uid, gid)
                        self.logger.debug(f"Fixed ownership of {backup_file} without sudo")
                    except (PermissionError, OSError):
                        # If direct chown fails, try with sudo if password is available
                        if hasattr(self, '_sudo_password') and self._sudo_password:
                            self._run_sudo_command(['chown', f'{uid}:{gid}', str(backup_file)], timeout=10)
                            self.logger.debug(f"Fixed ownership of {backup_file} with sudo")
                        else:
                            # No sudo password available - log warning but don't fail
                            # Backup was successful, ownership is just a convenience
                            self.logger.warning(f"Could not fix ownership of {backup_file} - no sudo password available")
                            self.logger.warning("Backup file may be owned by root - this is not critical, backup was successful")
                except Exception as e:
                    # Any other error - log but don't fail (backup was successful)
                    self.logger.warning(f"Could not fix ownership of {backup_file}: {e} - may require manual chown")
                
                self.logger.info(f"Bind mount {source_path} backed up successfully using Docker")
                return True
            else:
                # If Docker method failed, fall back to direct tar with timeout
                self.logger.info(f"Docker backup method failed, falling back to direct tar for {source_path}")
                return self._backup_directory(source_path, backup_file, container_name)
                
        except Exception as e:
            self.logger.error(f"Docker bind mount backup failed: {e}, falling back to direct method")
            return self._backup_directory(source_path, backup_file, container_name)
    
    def _backup_directory(self, source_path: str, backup_file: Path, container_name: str = None) -> bool:
        """Backup a directory to tar.gz file using tar command with timeout
        
        Uses sudo for paths that require elevated privileges (like /var/lib/docker/volumes/).
        Uses tar command instead of tarfile module to avoid hanging on large directories.
        """
        try:
            import subprocess
            import threading
            
            source = Path(source_path)
            if not source.exists():
                self.logger.warning(f"Source path does not exist: {source_path}")
                return False
            
            # Check if path requires sudo (Docker volume paths or system paths)
            requires_sudo = (
                str(source_path).startswith('/var/lib/docker/volumes/') or
                str(source_path).startswith('/var/lib/docker/') or
                str(source_path).startswith('/root/') or
                not os.access(source_path, os.R_OK)  # Check if we can read without sudo
            )
            
            timeout = 600  # 10 minutes timeout for large directories
            tar_cmd = ['tar', '-czf', str(backup_file), '-C', str(source.parent), source.name]
            
            if requires_sudo:
                self.logger.info(f"Using sudo for backup of privileged path: {source_path}")
                # Use _run_sudo_command to pass password if available
                if hasattr(self, '_sudo_password') and self._sudo_password:
                    # For long-running operations like tar, use Popen with password passing
                    # instead of communicate() which may not work well for long operations
                    password_bytes = (self._sudo_password + '\n').encode('utf-8')
                    sudo_cmd = ['sudo', '-S'] + tar_cmd  # -S reads password from stdin
                    
                    try:
                        process = subprocess.Popen(
                            sudo_cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=False  # Use binary mode for better performance
                        )
                        # Send password immediately
                        process.stdin.write(password_bytes)
                        process.stdin.close()
                        
                        # Wait with periodic cancel checks and progress updates
                        start_time = time.time()
                        check_interval = 2
                        last_progress_update = 0
                        progress_update_interval = 5  # Update progress every 5 seconds
                        
                        while True:
                            elapsed = time.time() - start_time
                            if elapsed > timeout:
                                process.terminate()
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                self.logger.error(f"Backup timed out for {source_path}")
                                if container_name:
                                    self._update_progress('backup', 95, f'❌ Backup timeout for {Path(source_path).name}')
                                return False
                            
                            # Check for cancellation
                            if container_name and self._check_cancel_flag(container_name):
                                self.logger.warning(f"Backup cancelled during directory backup: {source_path}")
                                process.terminate()
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                if container_name:
                                    self._update_progress('backup', int((elapsed / timeout) * 100), f'⚠️ Backup cancelled: {Path(source_path).name}')
                                return False
                            
                            # Update progress periodically during backup
                            if container_name and elapsed - last_progress_update >= progress_update_interval:
                                progress_pct = min(90, int((elapsed / timeout) * 100))
                                self._update_progress('backup', progress_pct, f'📦 Creating backup of {Path(source_path).name}... ({int(elapsed)}s)')
                                last_progress_update = elapsed
                            
                            # Check if process finished
                            if process.poll() is not None:
                                break
                            
                            # Wait a bit before next check
                            time.sleep(check_interval)
                        
                        # Get result
                        stdout, stderr = process.communicate()
                        returncode = process.returncode
                        
                        if returncode == 0:
                            # Fix ownership of created backup file
                            if backup_file.exists():
                                try:
                                    self._run_sudo_command(['chown', f"{os.getuid()}:{os.getgid()}", str(backup_file)], timeout=10)
                                except:
                                    pass  # Ignore chown errors
                            return True
                        else:
                            error_msg = stderr.decode('utf-8', errors='ignore').strip() if stderr else "Unknown error"
                            # Check if sudo prompted for password (this shouldn't happen with -S)
                            if 'password' in error_msg.lower() or '[sudo] password' in error_msg.lower():
                                self.logger.error(f"Sudo password prompt appeared during backup - password may be incorrect or not passed correctly")
                                self.logger.error(f"Command: {' '.join(sudo_cmd)}")
                                self.logger.error(f"Error: {error_msg}")
                                self.logger.error("This should not happen - password should be passed via stdin with -S flag")
                            self.logger.error(f"Tar backup failed for {source_path}: {error_msg}")
                            return False
                    except Exception as e:
                        self.logger.error(f"Failed to run sudo tar backup: {e}")
                        self.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
                        return False
                else:
                    # No password available - but we need sudo
                    # This should not happen in web interface, but if it does, log warning and fail
                    self.logger.error(f"Sudo password required for backup of {source_path} but password not available")
                    self.logger.error("This should not happen in web interface - password should be set from session")
                    self.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
                    self.console.print(f"[red]❌ Sudo password required but not available. Cannot backup {source_path}[/red]")
                    self.console.print(f"[red]Please provide sudo password via the web interface modal[/red]")
                    return False
            else:
                self.logger.info(f"Using direct tar for backup of: {source_path}")
            
            # Show progress bar for non-sudo backup as well
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=self.console
            ) as progress:
                backup_task = progress.add_task(
                    f"📦 Backing up {source.name}...",
                    total=100
                )
                
                # Use Popen for better cancellation support (for non-sudo or when password not available)
                process = subprocess.Popen(
                    tar_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Wait with periodic cancel checks and progress updates
                start_time = time.time()
                check_interval = 2  # Check cancel flag every 2 seconds
                last_size = 0
                last_progress_update = 0
                progress_update_interval = 5  # Update progress every 5 seconds
                
                while True:
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        progress.update(backup_task, description="❌ Backup timed out")
                        self.logger.error(f"Backup timed out for {source_path}")
                        if container_name:
                            self._update_progress('backup', 95, f'❌ Backup timeout for {source.name}')
                        return False
                    
                    # Check for cancellation
                    if container_name and self._check_cancel_flag(container_name):
                        self.logger.warning(f"Backup cancelled during directory backup: {source_path}")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        progress.update(backup_task, description="⚠️ Backup cancelled")
                        if container_name:
                            progress_pct = min(90, int((elapsed / timeout) * 100))
                            self._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {source.name}')
                        return False
                    
                    # Update progress periodically during backup (for web interface)
                    if container_name and elapsed - last_progress_update >= progress_update_interval:
                        progress_pct = min(90, int((elapsed / timeout) * 100))
                        self._update_progress('backup', progress_pct, f'📦 Creating backup of {source.name}... ({int(elapsed)}s)')
                        last_progress_update = elapsed
                    
                    # Check if process finished
                    if process.poll() is not None:
                        progress.update(backup_task, completed=100, description="✅ Backup completed")
                        break
                    
                    # Update progress based on file size growth (for console)
                    if backup_file.exists():
                        current_size = backup_file.stat().st_size
                        if current_size > last_size:
                            # Estimate progress based on time elapsed vs timeout
                            # This is a rough estimate since we don't know total size
                            progress_pct = min(95, int((elapsed / timeout) * 100))
                            progress.update(backup_task, completed=progress_pct)
                            last_size = current_size
                    
                    # Wait a bit before next check
                    time.sleep(check_interval)
            
                # Get result
                stdout, stderr = process.communicate()
                returncode = process.returncode
                
                if returncode == 0:
                    if requires_sudo and backup_file.exists():
                        # Fix ownership of created backup file
                        try:
                            self._run_sudo_command(['chown', f"{os.getuid()}:{os.getgid()}", str(backup_file)], timeout=10)
                        except:
                            pass  # Ignore chown errors
                    return True
                else:
                    error_msg = stderr.strip() if stderr else "Unknown error"
                    self.logger.error(f"Tar backup failed for {source_path}: {error_msg}")
                    return False
                    
        except Exception as e:
            self.logger.error(f"Failed to create tar backup: {e}")
            return False
    
    def _run_sudo_command(self, command_args, timeout=10, check=False):
        """Run sudo command with password if available
        
        Args:
            command_args: List of command arguments (without 'sudo')
            timeout: Command timeout in seconds
            check: If True, raise exception on non-zero return code
        
        Raises:
            RuntimeError: If sudo password is required but not available
            subprocess.TimeoutExpired: If command times out and check=True
            subprocess.CalledProcessError: If command fails and check=True
        """
        sudo_cmd = ['sudo'] + command_args
        
        # If password is available (from web session), use it
        if hasattr(self, '_sudo_password') and self._sudo_password:
            # Use subprocess with stdin to pass password to sudo -S (read from stdin)
            # -S makes sudo read password from stdin
            # We pass password + newline to stdin
            password_bytes = (self._sudo_password + '\n').encode('utf-8')
            
            try:
                sudo_process = subprocess.Popen(
                    sudo_cmd + ['-S'],  # -S reads password from stdin
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = sudo_process.communicate(input=password_bytes, timeout=timeout)
                returncode = sudo_process.returncode
                
                # Check if sudo prompted for password (this shouldn't happen with -S)
                if stderr and b'password' in stderr.lower() and returncode != 0:
                    error_msg = stderr.decode('utf-8', errors='ignore').strip()
                    self.logger.error(f"Sudo command failed - password may be incorrect or sudo prompt appeared: {error_msg}")
                    if check:
                        raise RuntimeError(f"Sudo command failed: {error_msg}")
            except subprocess.TimeoutExpired:
                sudo_process.kill()
                stdout, stderr = sudo_process.communicate()
                returncode = -1
                if check:
                    raise subprocess.TimeoutExpired(sudo_cmd, timeout)
        else:
            # No password available - this should not happen in web interface
            # Log warning and return error instead of prompting
            self.logger.error(f"Sudo password required for command {command_args} but password not available")
            self.logger.error("This should not happen in web interface - password should be set from session")
            self.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
            raise RuntimeError(f"Sudo password required but not available. Cannot execute: {' '.join(command_args)}")
        
        if check and returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore').strip() if stderr else "Unknown error"
            raise subprocess.CalledProcessError(returncode, sudo_cmd, stdout, stderr)
        
        return subprocess.CompletedProcess(sudo_cmd, returncode, stdout, stderr)
    
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
