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
from .deployment_state_backup import (
    backup_deployment_state as _backup_deployment_state_impl,
    restore_deployment_state as _restore_deployment_state_impl,
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
        """Create a snapshot of current Docker deployment state."""
        return _backup_deployment_state_impl(self, backup_path)

    def restore_deployment_state(self, backup_path: str) -> bool:
        """Restore Docker deployment state metadata from a snapshot."""
        return _restore_deployment_state_impl(self, backup_path)
