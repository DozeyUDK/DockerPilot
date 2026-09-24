"""Backup discovery helpers extracted from BackupRestoreMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import json


def find_existing_backup(host: Any, container_name: str, max_age_hours: int = 24) -> Optional[Path]:
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
                    host.logger.debug(f"Error reading backup metadata {metadata_file}: {e}")
                    continue

        return best_backup

    except Exception as e:
        host.logger.warning(f"Error searching for existing backup: {e}")
        return None
