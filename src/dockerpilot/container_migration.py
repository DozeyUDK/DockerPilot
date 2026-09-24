"""Container data migration helpers extracted from BackupRestoreMixin."""

from pathlib import Path
from typing import Any

from .models import DeploymentConfig


def migrate_container_data(host: Any, source_container, target_container, config: DeploymentConfig) -> bool:
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
            host.logger.info("No source container to migrate data from")
            return True

        host.console.print(f"[cyan]📦 Migrating data from '{source_container.name}' to '{target_container.name}'...[/cyan]")

        # Get mounts from both containers
        source_mounts = source_container.attrs.get('Mounts', [])
        target_mounts = target_container.attrs.get('Mounts', [])

        if not source_mounts:
            host.logger.info("No mounts in source container to migrate")
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
                host.logger.warning(f"Mount point '{mount_point}' not found in target container, skipping")
                continue

            target_info = target_volumes[mount_point]

            # Handle named volumes - copy data between volumes
            if source_info['type'] == 'named_volume' and target_info['type'] == 'named_volume':
                source_volume_name = source_info['name']
                target_volume_name = target_info['name']

                # If volumes are the same, no migration needed
                if source_volume_name == target_volume_name:
                    host.logger.info(f"Volume '{source_volume_name}' is shared, no migration needed")
                    continue

                host.console.print(f"[cyan]Migrating named volume: {source_volume_name} -> {target_volume_name}[/cyan]")

                # Copy data using Docker container
                success = host._copy_volume_data(source_volume_name, target_volume_name, config.container_name)
                if success:
                    migrated_count += 1
                    host.console.print(f"[green]✅ Migrated volume '{source_volume_name}' to '{target_volume_name}'[/green]")
                else:
                    host.logger.warning(f"Failed to migrate volume '{source_volume_name}', continuing...")

            # Handle bind mounts - check if same source path (data is already shared)
            elif source_info['type'] == 'bind_mount' and target_info['type'] == 'bind_mount':
                source_path = source_info['source']
                target_path = target_info['source']

                # If same path, data is already available
                if source_path == target_path:
                    host.logger.info(f"Bind mount '{source_path}' is shared, no migration needed")
                    continue

                # If different paths, copy data
                host.console.print(f"[cyan]Migrating bind mount: {source_path} -> {target_path}[/cyan]")

                if Path(source_path).exists():
                    success = host._copy_bind_mount_data(source_path, target_path, config.container_name)
                    if success:
                        migrated_count += 1
                        host.console.print(f"[green]✅ Migrated bind mount '{source_path}' to '{target_path}'[/green]")
                    else:
                        host.logger.warning(f"Failed to migrate bind mount '{source_path}', continuing...")
                else:
                    host.logger.warning(f"Source bind mount path does not exist: {source_path}")

        # Copy internal configuration files for databases
        db_config = host._get_database_config(config.image_tag)

        if db_config:
            host.console.print(f"[cyan]📋 Detected database container, migrating configuration files...[/cyan]")

            # Get config paths from database configuration
            config_paths = db_config.get('config_paths', [])

            for config_path in config_paths:
                success = host._copy_container_files(source_container, target_container, config_path, config.container_name)
                if success:
                    host.console.print(f"[green]✅ Migrated config from '{config_path}'[/green]")

        host.console.print(f"[green]✅ Data migration completed: {migrated_count} volumes migrated, {skipped_count} skipped[/green]")
        return True

    except Exception as e:
        host.logger.error(f"Data migration failed: {e}")
        host.console.print(f"[yellow]⚠️ Data migration failed: {e}, continuing deployment...[/yellow]")
        # Don't fail deployment if migration fails - just log warning
        return True  # Return True to not block deployment


def copy_volume_data(host: Any, source_volume_name: str, target_volume_name: str, container_name: str = None) -> bool:
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
            host.logger.info(f"Successfully copied data from volume '{source_volume_name}' to '{target_volume_name}'")
            return True
        else:
            host.logger.warning(f"Volume copy warnings: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        host.logger.error(f"Volume copy timed out for {source_volume_name} -> {target_volume_name}")
        return False
    except Exception as e:
        host.logger.error(f"Failed to copy volume data: {e}")
        return False


def copy_bind_mount_data(host: Any, source_path: str, target_path: str, container_name: str = None) -> bool:
    """Copy data from source bind mount path to target bind mount path"""
    try:
        import subprocess
        import shutil

        source = Path(source_path)
        target = Path(target_path)

        if not source.exists():
            host.logger.warning(f"Source path does not exist: {source_path}")
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
            host.logger.info(f"Successfully copied bind mount data from '{source_path}' to '{target_path}'")
            return True
        else:
            host.logger.warning(f"Bind mount copy failed: {result.stderr}")
            return False

    except Exception as e:
        host.logger.error(f"Failed to copy bind mount data: {e}")
        return False


def copy_container_files(host: Any, source_container, target_container, source_path: str, container_name: str = None) -> bool:
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
                host.logger.warning(f"Failed to copy from source container: {result1.stderr.decode()}")
                return False

            # Extract tar to target container
            result2 = subprocess.run(
                ['docker', 'cp', '-', f'{target_container.name}:{Path(source_path).parent}/'],
                stdin=open(tmp_tar_path, 'rb'),
                stderr=subprocess.PIPE,
                timeout=300
            )

            if result2.returncode == 0:
                host.logger.info(f"Successfully copied files from '{source_path}' between containers")
                return True
            else:
                host.logger.warning(f"Failed to copy to target container: {result2.stderr.decode()}")
                return False

        finally:
            # Cleanup temp file
            try:
                Path(tmp_tar_path).unlink()
            except:
                pass

    except Exception as e:
        host.logger.error(f"Failed to copy container files: {e}")
        return False
