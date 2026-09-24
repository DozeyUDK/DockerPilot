"""Container restore helpers extracted from BackupRestoreMixin."""

from pathlib import Path
from typing import Any
import json
import time

import docker


def restore_container_data(host: Any, container_name: str, backup_path: str) -> bool:
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
            host.console.print(f"[red]❌ Backup directory not found: {backup_path}[/red]")
            return False

        metadata_file = backup_dir / 'backup_metadata.json'
        if not metadata_file.exists():
            host.console.print(f"[red]❌ Backup metadata not found: {metadata_file}[/red]")
            return False

        with open(metadata_file, 'r') as f:
            backup_metadata = json.load(f)

        host.console.print(f"[cyan]📦 Restoring data for container '{container_name}' from backup...[/cyan]")
        host.console.print(f"[cyan]Backup created: {backup_metadata.get('backup_time', 'unknown')}[/cyan]")

        container = host.client.containers.get(container_name)
        mounts = container.attrs.get('Mounts', [])

        # Show loading indicator during restore
        with host._with_loading("Restoring container data"):
            # Restore each volume
            for volume_info in backup_metadata.get('volumes', []):
                backup_file = Path(volume_info['backup_file'])
                if not backup_file.exists():
                    # Try relative to backup_dir
                    backup_file = backup_dir / backup_file.name

                if not backup_file.exists():
                    host.console.print(f"[yellow]⚠️ Backup file not found: {volume_info['backup_file']}[/yellow]")
                    continue

                if volume_info['type'] == 'named_volume':
                    volume_name = volume_info['name']
                    host.console.print(f"[cyan]Restoring named volume: {volume_name}[/cyan]")

                    try:
                        volume = host.client.volumes.get(volume_name)
                        volume_path = volume.attrs['Mountpoint']

                        # Extract backup to volume
                        host._restore_from_tar(backup_file, volume_path)
                        host.console.print(f"[green]✅ Restored volume '{volume_name}'[/green]")
                    except Exception as e:
                        host.logger.error(f"Failed to restore volume {volume_name}: {e}")
                        host.console.print(f"[red]❌ Failed to restore volume '{volume_name}': {e}[/red]")
                        return False

                elif volume_info['type'] == 'bind_mount':
                    source_path = volume_info['source']
                    host.console.print(f"[cyan]Restoring bind mount: {source_path}[/cyan]")

                    try:
                        if Path(source_path).exists():
                            # Backup existing data first
                            existing_backup = Path(source_path).parent / f"{Path(source_path).name}.backup_{int(time.time())}"
                            if Path(source_path).is_dir():
                                import shutil
                                shutil.move(str(source_path), str(existing_backup))
                                Path(source_path).mkdir(parents=True, exist_ok=True)

                            # Extract backup
                            host._restore_from_tar(backup_file, source_path)
                            host.console.print(f"[green]✅ Restored bind mount '{source_path}'[/green]")
                        else:
                            host.console.print(f"[yellow]⚠️ Bind mount path does not exist: {source_path}[/yellow]")
                    except Exception as e:
                        host.logger.error(f"Failed to restore bind mount {source_path}: {e}")
                        host.console.print(f"[red]❌ Failed to restore bind mount '{source_path}': {e}[/red]")
                        return False

        host.console.print(f"[bold green]✅ Data restore completed![/bold green]")
        return True

    except docker.errors.NotFound:
        host.console.print(f"[red]❌ Container '{container_name}' not found[/red]")
        return False
    except Exception as e:
        host.logger.error(f"Container data restore failed: {e}")
        host.console.print(f"[red]❌ Restore failed: {e}[/red]")
        return False


def restore_from_tar(host: Any, tar_file: Path, destination: str) -> bool:
    """Extract tar.gz file to destination"""
    try:
        import tarfile

        destination_path = Path(destination)
        destination_path.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tar_file, 'r:gz') as tar:
            tar.extractall(path=destination_path.parent)

        return True
    except Exception as e:
        host.logger.error(f"Failed to extract tar backup: {e}")
        return False
