"""Container restore helpers extracted from BackupRestoreMixin."""

from dockerpilot.ui_icons import icon
from dockerpilot.services.safe_tar import _parts, extract_safe
from pathlib import Path
from typing import Any
import json
import shutil
import tarfile
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
            host.console.print(f"[red]{icon('error')} Backup directory not found: {backup_path}[/red]")
            return False

        metadata_file = backup_dir / 'backup_metadata.json'
        if not metadata_file.exists():
            host.console.print(f"[red]{icon('error')} Backup metadata not found: {metadata_file}[/red]")
            return False

        with open(metadata_file, 'r') as f:
            backup_metadata = json.load(f)

        host.console.print(f"[cyan]{icon('package')} Restoring data for container '{container_name}' from backup...[/cyan]")
        host.console.print(f"[cyan]Backup created: {backup_metadata.get('backup_time', 'unknown')}[/cyan]")

        container = host.client.containers.get(container_name)
        mounts = container.attrs.get("Mounts", [])
        named_volumes = {
            str(m.get("Name")) for m in mounts if m.get("Name")
        }
        bind_sources = {
            str(m.get("Source")) for m in mounts
            if m.get("Type") == "bind" or (not m.get("Type") and not m.get("Name"))
        }

        # Show loading indicator during restore
        with host._with_loading("Restoring container data"):
            # Restore each volume
            for volume_info in backup_metadata.get('volumes', []):
                # Only read archives from the selected backup directory.
                # Absolute filenames in older metadata are converted to their
                # basename, keeping copied backups portable and confined.
                backup_file = backup_dir / Path(volume_info['backup_file']).name
                if backup_file.is_symlink() or not backup_file.is_file():
                    host.console.print(f"[red]{icon('error')} Backup file not found: {volume_info['backup_file']}[/red]")
                    return False

                if volume_info['type'] == 'named_volume':
                    volume_name = volume_info['name']
                    if volume_name not in named_volumes:
                        host.logger.error(f"Backup volume not mounted to container: {volume_name}")
                        return False
                    host.console.print(f"[cyan]Restoring named volume: {volume_name}[/cyan]")

                    try:
                        volume = host.client.volumes.get(volume_name)
                        volume_path = volume.attrs['Mountpoint']

                        # Extract backup to volume
                        if not host._restore_from_tar(backup_file, volume_path):
                            raise RuntimeError(f"Archive extraction failed for volume '{volume_name}'")
                        host.console.print(f"[green]{icon('success')} Restored volume '{volume_name}'[/green]")
                    except Exception as e:
                        host.logger.error(f"Failed to restore volume {volume_name}: {e}")
                        host.console.print(f"[red]{icon('error')} Failed to restore volume '{volume_name}': {e}[/red]")
                        return False

                elif volume_info['type'] == 'bind_mount':
                    source_path = volume_info['source']
                    if source_path not in bind_sources:
                        host.logger.error(f"Backup bind source not mounted to container: {source_path}")
                        return False
                    host.console.print(f"[cyan]Restoring bind mount: {source_path}[/cyan]")

                    original_path = Path(source_path)
                    previous_backup = None
                    try:
                        if original_path.is_symlink() or not original_path.exists():
                            raise RuntimeError(f"Bind mount path is missing or is a symlink: {source_path}")
                        # Keep the original directory until the restored contents are usable.
                        # On any extraction failure, roll it back instead of leaving an empty mount.
                        if original_path.is_dir():
                            previous_backup = original_path.parent / (
                                f"{original_path.name}.backup_{time.time_ns()}"
                            )
                            shutil.move(str(original_path), str(previous_backup))
                            original_path.mkdir(parents=True, exist_ok=True)

                        if not host._restore_from_tar(backup_file, source_path):
                            raise RuntimeError(f"Archive extraction failed for bind mount '{source_path}'")
                        host.console.print(f"[green]{icon('success')} Restored bind mount '{source_path}'[/green]")
                    except Exception as e:
                        if previous_backup is not None and previous_backup.exists():
                            try:
                                if original_path.is_dir() and not original_path.is_symlink():
                                    shutil.rmtree(original_path)
                                elif original_path.is_symlink() or original_path.exists():
                                    original_path.unlink()
                                shutil.move(str(previous_backup), str(original_path))
                            except Exception as rollback_error:
                                host.logger.error(
                                    f"Could not roll back bind mount {source_path}: {rollback_error}; "
                                    f"original data remains in {previous_backup}"
                                )
                        host.logger.error(f"Failed to restore bind mount {source_path}: {e}")
                        host.console.print(f"[red]{icon('error')} Failed to restore bind mount '{source_path}': {e}[/red]")
                        return False
                else:
                    host.logger.error(f"Unsupported backup volume type: {volume_info['type']!r}")
                    return False

        host.console.print(f"[bold green]{icon('success')} Data restore completed![/bold green]")
        return True

    except docker.errors.NotFound:
        host.console.print(f"[red]{icon('error')} Container '{container_name}' not found[/red]")
        return False
    except Exception as e:
        host.logger.error(f"Container data restore failed: {e}")
        host.console.print(f"[red]{icon('error')} Restore failed: {e}[/red]")
        return False


def restore_from_tar(host: Any, tar_file: Path, destination: str) -> bool:
    """Safely restore the two supported layouts: volume contents or named root."""
    try:
        destination_path = Path(destination)
        with tarfile.open(tar_file, "r:gz") as archive:
            members = archive.getmembers()
            # Docker volume backups use -C /volume . and contain "./" entries.
            # Bind mount backups use -C source.parent source.name, so their root
            # is the destination directory name and must be stripped once.
            # Do not reinterpret explicit dot-rooted volumes as named-root
            # archives when a volume happens to contain a same-named folder.
            dot_rooted = any(
                member.name == "." or member.name.startswith("./")
                for member in members
            )
            named_root = (
                bool(members)
                and not dot_rooted
                and all(
                    _parts(member.name)
                    and _parts(member.name)[0] == destination_path.name
                    for member in members
                )
            )
            extract_safe(
                archive,
                destination_path,
                strip_root=destination_path.name if named_root else None,
            )
        return True
    except Exception as e:
        host.logger.error(f"Failed to extract tar backup: {e}")
        return False
