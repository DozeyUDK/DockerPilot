"""Low-level backup archive/runtime helpers extracted from BackupRestoreMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any
import os
import subprocess
import time

import docker
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


def backup_volume_using_docker(host: Any, volume_name: str, backup_file: Path, container_name: str = None) -> bool:
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

        host.logger.info(f"Starting backup of volume '{volume_name}' (timeout: {timeout}s)")

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
                host.logger.error(f"Volume backup timed out for {volume_name} after {elapsed:.1f}s")
                if container_name:
                    host._update_progress('backup', 95, f'❌ Backup timeout for volume: {volume_name}')
                # Clean up any orphaned backup containers
                host._cleanup_backup_containers()
                return False

            # Check for cancellation
            if container_name and host._check_cancel_flag(container_name):
                host.logger.warning(f"Backup cancelled during volume backup: {volume_name}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                if container_name:
                    progress_pct = min(90, int((elapsed / timeout) * 100))
                    host._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {volume_name}')
                # Clean up any orphaned backup containers
                host._cleanup_backup_containers()
                return False

            # Check if process finished
            if process.poll() is not None:
                host.logger.info(f"Volume backup completed for '{volume_name}' in {elapsed:.1f}s")
                break

            # Update progress periodically (for web interface)
            if container_name and elapsed - last_progress_update >= progress_update_interval:
                current_size = backup_file.stat().st_size if backup_file.exists() else 0
                size_mb = current_size / (1024 * 1024) if current_size > 0 else 0
                progress_pct = min(90, int((elapsed / timeout) * 100))
                host._update_progress('backup', progress_pct, f'📦 Creating backup of volume: {volume_name}... ({int(elapsed)}s, {size_mb:.1f} MB)')
                last_progress_update = elapsed

            # Log progress periodically (for console)
            if time.time() - last_log_time >= log_interval:
                current_size = backup_file.stat().st_size if backup_file.exists() else 0
                size_mb = current_size / (1024 * 1024) if current_size > 0 else 0
                progress_pct = min(95, int((elapsed / timeout) * 100))

                if current_size > last_size:
                    host.logger.info(f"Backup progress: {progress_pct}% | Elapsed: {elapsed:.1f}s | Size: {size_mb:.1f} MB | Volume: {volume_name}")
                    last_size = current_size
                else:
                    host.logger.info(f"Backup progress: {progress_pct}% | Elapsed: {elapsed:.1f}s | Volume: {volume_name}")
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
                        host.logger.debug(f"Fixed ownership of {backup_file} without sudo")
                    except (PermissionError, OSError):
                        # If direct chown fails, try with sudo if password is available
                        if host._get_sudo_password():
                            host._run_sudo_command(['chown', f'{uid}:{gid}', str(backup_file)], timeout=10)
                            host.logger.debug(f"Fixed ownership of {backup_file} with sudo")
                        else:
                            # No sudo password available - log warning but don't fail
                            # Backup was successful, ownership is just a convenience
                            host.logger.warning(f"Could not fix ownership of {backup_file} - no sudo password available")
                            host.logger.warning("Backup file may be owned by root - this is not critical, backup was successful")
                except Exception as e:
                    # Any other error - log but don't fail (backup was successful)
                    host.logger.warning(f"Could not fix ownership of {backup_file}: {e} - may require manual chown")

            host.logger.info(f"Volume {volume_name} backed up successfully using Docker")
            if container_name:
                host._update_progress('backup', 90, f'✅ Zbackupowano volume: {volume_name}')
            return True
        else:
            # Log stderr but don't fail on socket warnings
            if stderr and 'socket ignored' not in stderr:
                host.logger.warning(f"Docker volume backup warnings: {stderr}")
            return False

    except Exception as e:
        host.logger.error(f"Docker volume backup failed: {e}")
        return False


def cleanup_backup_containers(host: Any):
    """Clean up any leftover backup containers (alpine:latest) that may have been orphaned

    This method finds and removes containers using alpine:latest image that are in exited state
    or have been running for too long. These are typically backup containers that weren't
    properly cleaned up due to process interruption.
    """
    try:
        if not host.client:
            return

        # Find all containers using alpine:latest image
        # These are likely backup containers that weren't cleaned up
        all_containers = host.client.containers.list(all=True, filters={'ancestor': 'alpine:latest'})

        cleaned = 0
        for container in all_containers:
            try:
                container.reload()  # Refresh container state

                # Remove exited containers (these are definitely orphaned)
                if container.status == 'exited':
                    container.remove()
                    cleaned += 1
                    host.logger.debug(f"Cleaned up exited backup container: {container.id[:12]}")
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
                                host.logger.debug(f"Cleaned up orphaned backup container (running {running_time:.0f}s): {container.id[:12]}")
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
                                    host.logger.debug(f"Cleaned up orphaned backup container (running {running_time:.0f}s): {container.id[:12]}")
                        except (ValueError, TypeError, KeyError):
                            # If we still can't determine, just log and skip
                            host.logger.debug(f"Could not determine container age, skipping: {container.id[:12]}")
            except docker.errors.NotFound:
                # Container was already removed, skip
                pass
            except Exception as e:
                host.logger.debug(f"Could not clean up container {container.id[:12]}: {e}")

        if cleaned > 0:
            host.logger.info(f"Cleaned up {cleaned} orphaned backup container(s)")
    except Exception as e:
        host.logger.debug(f"Error cleaning up backup containers: {e}")


def backup_bind_mount_using_docker(host: Any, source_path: str, backup_file: Path, container_name: str = None) -> bool:
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
            host.logger.warning(f"Source path does not exist: {source_path}")
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
                host.logger.error(f"Bind mount backup timed out for {source_path}")
                # Clean up any orphaned backup containers
                host._cleanup_backup_containers()
                if container_name:
                    host._update_progress('backup', 95, f'❌ Backup timeout for {source.name}')
                return False

            # Check for cancellation
            if container_name and host._check_cancel_flag(container_name):
                host.logger.warning(f"Backup cancelled during bind mount backup: {source_path}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                # Clean up any orphaned backup containers
                host._cleanup_backup_containers()
                if container_name:
                    progress_pct = min(90, int((elapsed / timeout) * 100))
                    host._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {source.name}')
                return False

            # Update progress periodically during backup
            if container_name and elapsed - last_progress_update >= progress_update_interval:
                progress_pct = min(90, int((elapsed / timeout) * 100))
                host._update_progress('backup', progress_pct, f'📦 Creating backup of {source.name}... ({int(elapsed)}s)')
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
                    host.logger.debug(f"Fixed ownership of {backup_file} without sudo")
                except (PermissionError, OSError):
                    # If direct chown fails, try with sudo if password is available
                    if host._get_sudo_password():
                        host._run_sudo_command(['chown', f'{uid}:{gid}', str(backup_file)], timeout=10)
                        host.logger.debug(f"Fixed ownership of {backup_file} with sudo")
                    else:
                        # No sudo password available - log warning but don't fail
                        # Backup was successful, ownership is just a convenience
                        host.logger.warning(f"Could not fix ownership of {backup_file} - no sudo password available")
                        host.logger.warning("Backup file may be owned by root - this is not critical, backup was successful")
            except Exception as e:
                # Any other error - log but don't fail (backup was successful)
                host.logger.warning(f"Could not fix ownership of {backup_file}: {e} - may require manual chown")

            host.logger.info(f"Bind mount {source_path} backed up successfully using Docker")
            return True
        else:
            # If Docker method failed, fall back to direct tar with timeout
            host.logger.info(f"Docker backup method failed, falling back to direct tar for {source_path}")
            return host._backup_directory(source_path, backup_file, container_name)

    except Exception as e:
        host.logger.error(f"Docker bind mount backup failed: {e}, falling back to direct method")
        return host._backup_directory(source_path, backup_file, container_name)


def backup_directory(host: Any, source_path: str, backup_file: Path, container_name: str = None) -> bool:
    """Backup a directory to tar.gz file using tar command with timeout

    Uses sudo for paths that require elevated privileges (like /var/lib/docker/volumes/).
    Uses tar command instead of tarfile module to avoid hanging on large directories.
    """
    try:
        import subprocess
        import threading

        source = Path(source_path)
        if not source.exists():
            host.logger.warning(f"Source path does not exist: {source_path}")
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
            host.logger.info(f"Using sudo for backup of privileged path: {source_path}")
            # Use _run_sudo_command to pass password if available
            sudo_password = host._get_sudo_password()
            if sudo_password:
                # For long-running operations like tar, use Popen with password passing
                # instead of communicate() which may not work well for long operations
                password_bytes = (sudo_password + '\n').encode('utf-8')
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
                            host.logger.error(f"Backup timed out for {source_path}")
                            if container_name:
                                host._update_progress('backup', 95, f'❌ Backup timeout for {Path(source_path).name}')
                            return False

                        # Check for cancellation
                        if container_name and host._check_cancel_flag(container_name):
                            host.logger.warning(f"Backup cancelled during directory backup: {source_path}")
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            if container_name:
                                host._update_progress('backup', int((elapsed / timeout) * 100), f'⚠️ Backup cancelled: {Path(source_path).name}')
                            return False

                        # Update progress periodically during backup
                        if container_name and elapsed - last_progress_update >= progress_update_interval:
                            progress_pct = min(90, int((elapsed / timeout) * 100))
                            host._update_progress('backup', progress_pct, f'📦 Creating backup of {Path(source_path).name}... ({int(elapsed)}s)')
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
                                host._run_sudo_command(['chown', f"{os.getuid()}:{os.getgid()}", str(backup_file)], timeout=10)
                            except:
                                pass  # Ignore chown errors
                        return True
                    else:
                        error_msg = stderr.decode('utf-8', errors='ignore').strip() if stderr else "Unknown error"
                        # Check if sudo prompted for password (this shouldn't happen with -S)
                        if 'password' in error_msg.lower() or '[sudo] password' in error_msg.lower():
                            host.logger.error(f"Sudo password prompt appeared during backup - password may be incorrect or not passed correctly")
                            host.logger.error(f"Command: {' '.join(sudo_cmd)}")
                            host.logger.error(f"Error: {error_msg}")
                            host.logger.error("This should not happen - password should be passed via stdin with -S flag")
                        host.logger.error(f"Tar backup failed for {source_path}: {error_msg}")
                        return False
                except Exception as e:
                    host.logger.error(f"Failed to run sudo tar backup: {e}")
                    host.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
                    return False
            else:
                # No password available - but we need sudo
                # This should not happen in web interface, but if it does, log warning and fail
                host.logger.error(f"Sudo password required for backup of {source_path} but password not available")
                host.logger.error("This should not happen in web interface - password should be set from session")
                host.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
                host.console.print(f"[red]❌ Sudo password required but not available. Cannot backup {source_path}[/red]")
                host.console.print(f"[red]Please provide sudo password via the web interface modal[/red]")
                return False
        else:
            host.logger.info(f"Using direct tar for backup of: {source_path}")

        # Show progress bar for non-sudo backup as well
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=host.console
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
                    host.logger.error(f"Backup timed out for {source_path}")
                    if container_name:
                        host._update_progress('backup', 95, f'❌ Backup timeout for {source.name}')
                    return False

                # Check for cancellation
                if container_name and host._check_cancel_flag(container_name):
                    host.logger.warning(f"Backup cancelled during directory backup: {source_path}")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    progress.update(backup_task, description="⚠️ Backup cancelled")
                    if container_name:
                        progress_pct = min(90, int((elapsed / timeout) * 100))
                        host._update_progress('backup', progress_pct, f'⚠️ Backup cancelled: {source.name}')
                    return False

                # Update progress periodically during backup (for web interface)
                if container_name and elapsed - last_progress_update >= progress_update_interval:
                    progress_pct = min(90, int((elapsed / timeout) * 100))
                    host._update_progress('backup', progress_pct, f'📦 Creating backup of {source.name}... ({int(elapsed)}s)')
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
                        host._run_sudo_command(['chown', f"{os.getuid()}:{os.getgid()}", str(backup_file)], timeout=10)
                    except:
                        pass  # Ignore chown errors
                return True
            else:
                error_msg = stderr.strip() if stderr else "Unknown error"
                host.logger.error(f"Tar backup failed for {source_path}: {error_msg}")
                return False

    except Exception as e:
        host.logger.error(f"Failed to create tar backup: {e}")
        return False


def run_sudo_command(host: Any, command_args, timeout=10, check=False):
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
    sudo_password = host._get_sudo_password()
    if sudo_password:
        # Use subprocess with stdin to pass password to sudo -S (read from stdin)
        # -S makes sudo read password from stdin
        # We pass password + newline to stdin
        password_bytes = (sudo_password + '\n').encode('utf-8')

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
                host.logger.error(f"Sudo command failed - password may be incorrect or sudo prompt appeared: {error_msg}")
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
        host.logger.error(f"Sudo password required for command {command_args} but password not available")
        host.logger.error("This should not happen in web interface - password should be set from session")
        host.logger.error("If you see a sudo prompt, it means sudo was called directly without using _run_sudo_command")
        raise RuntimeError(f"Sudo password required but not available. Cannot execute: {' '.join(command_args)}")

    if check and returncode != 0:
        error_msg = stderr.decode('utf-8', errors='ignore').strip() if stderr else "Unknown error"
        raise subprocess.CalledProcessError(returncode, sudo_cmd, stdout, stderr)

    return subprocess.CompletedProcess(sudo_cmd, returncode, stdout, stderr)
