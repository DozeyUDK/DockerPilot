"""Container backup orchestration extracted from BackupRestoreMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import subprocess
import time

import docker


def backup_container_data(host: Any, container_name: str, backup_path: str = None, reuse_existing: bool = True, max_backup_age_hours: int = 24) -> bool:
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
        container = host.client.containers.get(container_name)

        # Check for existing backup first if reuse_existing is True
        # Even if backup_path is provided, we check for existing backup first
        if reuse_existing:
            existing_backup = host.find_existing_backup(container_name, max_backup_age_hours)
            if existing_backup:
                host.logger.info(f"Found existing backup for {container_name}: {existing_backup}")
                host.console.print(f"[green]✅ Found existing backup: {existing_backup}[/green]")

                # Verify backup is complete
                metadata_file = existing_backup / 'backup_metadata.json'
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)

                        volumes = metadata.get('volumes', [])
                        total_size_mb = metadata.get('total_size', 0) / (1024 * 1024)
                        backup_time = metadata.get('backup_time', 'unknown')

                        host.console.print(f"[green]Backup created: {backup_time}[/green]")
                        host.console.print(f"[green]Total size: {total_size_mb:.2f} MB[/green]")
                        host.console.print(f"[green]Volumes backed up: {len(volumes)}[/green]")
                        host.console.print(f"[cyan]ℹ️ Reusing existing backup instead of creating new one[/cyan]")

                        # Set backup_path to the existing backup path for consistency
                        backup_path = str(existing_backup)
                        return True
                    except Exception as e:
                        host.logger.warning(f"Error reading existing backup metadata: {e}")
                        # Continue to create new backup

        if not backup_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_{container_name}_{timestamp}"

        backup_dir = Path(backup_path)
        backup_dir.mkdir(exist_ok=True, parents=True)

        # Pre-check: will we need sudo?
        requires_sudo, privileged_paths, mount_info = host._check_sudo_required_for_backup(container_name)

        if requires_sudo:
            host.console.print(f"[yellow]⚠️  BACKUP REQUIRES SUDO ACCESS[/yellow]")
            host.console.print(f"[yellow]Privileged paths ({len(privileged_paths)}):[/yellow]")
            for path in privileged_paths[:3]:  # Show first 3
                host.console.print(f"[dim]  - {path}[/dim]")
            if len(privileged_paths) > 3:
                host.console.print(f"[dim]  ... and {len(privileged_paths) - 3} more[/dim]")
            host.console.print(f"[yellow]You may be prompted for sudo password during backup.[/yellow]")
            host.console.print(f"[yellow]To skip backup: use --skip-backup flag[/yellow]")

            # Give user 3 seconds to cancel
            import sys
            for i in range(3, 0, -1):
                sys.stdout.write(f"\rContinuing in {i}s... (Ctrl+C to cancel)")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\r" + " " * 50 + "\r")  # Clear line

        host.console.print(f"[cyan]📦 Creating data backup for container '{container_name}'...[/cyan]")

        # Get container mounts
        mounts = container.attrs.get('Mounts', [])

        if not mounts:
            host.console.print(f"[yellow]⚠️ No volumes mounted to container '{container_name}'[/yellow]")
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
        with host._with_loading("Backing up container data"):
            # Backup each volume
            backed_up_volumes = []
            total_mounts = len([m for m in mounts if m.get('Source') or m.get('Name')])
            processed_mounts = 0

            for mount in mounts:
                # Check for cancellation before each mount backup
                if host._check_cancel_flag(container_name):
                    host.logger.warning(f"Backup cancelled by user for {container_name}")
                    host.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
                    return False

                volume_name = mount.get('Name')
                mount_point = mount.get('Destination')  # Path inside container
                source = mount.get('Source')  # Path on host (for bind mounts)

                # Skip system paths and root filesystem FIRST
                if source:
                    source_path = Path(source)

                    # ALWAYS skip root filesystem - this is critical!
                    if str(source_path) == '/' or str(source_path).resolve() == Path('/'):
                        host.logger.warning(f"Skipping root filesystem bind mount: {source} -> {mount_point} (CRITICAL: root filesystem should never be backed up)")
                        host.console.print(f"[red]⚠️ SKIPPING root filesystem bind mount '{source}' (root filesystem should never be backed up!)[/red]")
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
                        host.logger.info(f"Skipping external storage bind mount: {source} -> {mount_point} (external storage, not container data)")
                        host.console.print(f"[cyan]ℹ️ Skipping external disk '{source}' (this is not container data, just a mounted disk)[/cyan]")
                        continue

                # Skip system paths
                if source:
                    source_path = Path(source)

                    # Check if source is a system path to skip
                    skip_mount = False
                    for system_path in system_paths_to_skip:
                        if str(source_path) == system_path or str(source_path).startswith(system_path + '/'):
                            host.logger.warning(f"Skipping system bind mount: {source} -> {mount_point}")
                            host.console.print(f"[yellow]⚠️ Skipping system bind mount '{source}' (system path)[/yellow]")
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
                                host.logger.warning(f"Large mount detected: {source} ({size_tb:.2f} TB) - this will take a very long time to backup")
                                host.console.print(f"[yellow]⚠️ Large mount detected: {source} ({size_tb:.2f} TB)[/yellow]")
                                host.console.print(f"[yellow]   This backup may take many hours. Consider skipping this mount.[/yellow]")
                    except (subprocess.TimeoutExpired, ValueError, IndexError, FileNotFoundError):
                        # If size check fails or times out, continue anyway
                        # (might be a network mount or permission issue)
                        pass

                if volume_name:
                    # Named volume - backup using Docker container (no sudo needed!)
                    host.console.print(f"[cyan]Backing up named volume: {volume_name} -> {mount_point}[/cyan]")
                    try:
                        backup_file = backup_dir / f"{volume_name}.tar.gz"

                        # Update progress for volume backup
                        if container_name:
                            progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                            host._update_progress('backup', progress_pct, f'📦 Creating backup of volume: {volume_name}...')

                        # Use Docker to backup volume (runs as root inside container)
                        # This avoids permission issues without requiring sudo
                        success = host._backup_volume_using_docker(volume_name, backup_file, container_name)

                        # Check for cancellation after backup
                        if host._check_cancel_flag(container_name):
                            host.logger.warning(f"Backup cancelled by user for {container_name}")
                            host.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
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
                            host.console.print(f"[green]✅ Backed up volume '{volume_name}' to {backup_file}[/green]")

                            # Update progress after successful backup
                            if container_name:
                                progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                                host._update_progress('backup', progress_pct, f'✅ Zbackupowano volume: {volume_name} ({processed_mounts}/{total_mounts})')
                        else:
                            host.logger.warning(f"Failed to backup volume {volume_name}, continuing...")
                            host.console.print(f"[yellow]⚠️ Failed to backup volume '{volume_name}', continuing...[/yellow]")
                            # Don't return False - continue with other volumes
                    except Exception as e:
                        host.logger.error(f"Failed to backup volume {volume_name}: {e}")
                        host.console.print(f"[yellow]⚠️ Failed to backup volume '{volume_name}': {e}, continuing...[/yellow]")
                        # Don't return False - continue with other volumes

                elif source:
                    # Bind mount - backup using Docker container (faster and no sudo needed for many paths)
                    host.console.print(f"[cyan]Backing up bind mount: {source} -> {mount_point}[/cyan]")
                    # Update progress for bind mount backup
                    if container_name:
                        source_name = Path(source).name
                        progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                        host._update_progress('backup', progress_pct, f'📦 Creating backup of bind mount: {source_name}...')
                    try:
                        if Path(source).exists():
                            # Create safe filename from path
                            safe_name = source.replace('/', '_').replace('\\', '_').strip('_')
                            backup_file = backup_dir / f"bind_{safe_name}.tar.gz"

                            # Use Docker container for backup (faster, no sudo needed, better for large directories)
                            success = host._backup_bind_mount_using_docker(source, backup_file, container_name)

                            # Check for cancellation after backup
                            if host._check_cancel_flag(container_name):
                                host.logger.warning(f"Backup cancelled by user for {container_name}")
                                host.console.print(f"[yellow]⚠️ Backup cancelled by user[/yellow]")
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
                                host.console.print(f"[green]✅ Backed up bind mount '{source}' to {backup_file}[/green]")

                                # Update progress after successful backup
                                if container_name:
                                    progress_pct = 5 + int((processed_mounts / max(total_mounts, 1)) * 15)  # 5-20% range
                                    host._update_progress('backup', progress_pct, f'✅ Zbackupowano bind mount: {source_name} ({processed_mounts}/{total_mounts})')
                            else:
                                host.logger.warning(f"Failed to backup bind mount {source}, continuing...")
                                host.console.print(f"[yellow]⚠️ Failed to backup bind mount '{source}', continuing...[/yellow]")
                                # Don't return False - continue with other volumes
                        else:
                            host.logger.warning(f"Bind mount source does not exist: {source}")
                            host.console.print(f"[yellow]⚠️ Bind mount source not found: {source}[/yellow]")
                    except Exception as e:
                        host.logger.error(f"Failed to backup bind mount {source}: {e}")
                        host.console.print(f"[yellow]⚠️ Failed to backup bind mount '{source}': {e}, continuing...[/yellow]")
                        # Don't return False - continue with other volumes

            # Save backup metadata (inside loading context)
            if container_name:
                host._update_progress('backup', 18, '💾 Saving backup metadata...')

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
                host._update_progress('backup', 20, '✅ Backup completed')

        # Show results after loading completes
        total_size_mb = sum(v.get('size', 0) for v in backed_up_volumes) / (1024 * 1024)
        host.console.print(f"[bold green]✅ Data backup completed![/bold green]")
        host.console.print(f"[green]Backup location: {backup_path}[/green]")
        host.console.print(f"[green]Total size: {total_size_mb:.2f} MB[/green]")
        host.console.print(f"[green]Volumes backed up: {len(backed_up_volumes)}[/green]")

        return True

    except docker.errors.NotFound:
        host.console.print(f"[red]❌ Container '{container_name}' not found[/red]")
        return False
    except Exception as e:
        host.logger.error(f"Container data backup failed: {e}")
        host.console.print(f"[red]❌ Backup failed: {e}[/red]")
        return False
