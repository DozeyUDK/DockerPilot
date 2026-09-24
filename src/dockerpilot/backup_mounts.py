"""Backup mount inspection helpers extracted from BackupRestoreMixin."""

from pathlib import Path
from typing import Any
import os
import subprocess


def check_sudo_required_for_backup(host: Any, container_name: str) -> tuple[bool, list[str], dict]:
    """Check if backup will require sudo access and get mount information

    Returns:
        tuple: (requires_sudo: bool, privileged_paths: list[str], mount_info: dict)
               mount_info contains: {'large_mounts': list, 'total_size_gb': float, 'mounts': list}
    """
    try:
        import shutil

        container = host.client.containers.get(container_name)
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
                    host.logger.debug(f"Could not check size for {source}: {e}")

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
        host.logger.warning(f"Could not check sudo requirements: {e}")
        return False, [], {'large_mounts': [], 'total_size_gb': 0, 'total_size_tb': 0, 'mounts': []}
