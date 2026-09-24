"""Blue/green deployment strategy extracted from DeploymentServiceMixin."""

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import time

import docker
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..deployment_runtime import (
    apply_container_command as _apply_container_command_impl,
    offset_port_mapping as _offset_port_mapping_impl,
    requires_privileged_mode as _requires_privileged_mode_impl,
)
from ..models import DeploymentConfig


def blue_green_deploy(
    host: Any,
    config: DeploymentConfig,
    build_config: dict,
    skip_backup: bool = False,
) -> bool:
    """Enhanced Blue-Green deployment with advanced features

    Args:
        config: Deployment configuration
        build_config: Build configuration
        skip_backup: Skip data backup (faster but risky for production)
    """
    host.console.print(f"\n[bold cyan]🔵🟢 BLUE-GREEN DEPLOYMENT STARTED[/bold cyan]")

    deployment_start = datetime.now()
    deployment_id = f"bg_deploy_{int(deployment_start.timestamp())}"

    # Track current deployment for cancellation support
    host._current_deployment_container = config.container_name

    # Auto-detect health check endpoint based on image type
    detected_endpoint = host._detect_health_check_endpoint(config.image_tag)
    if detected_endpoint != config.health_check_endpoint:
        host.logger.info(f"Auto-detected health check endpoint: {detected_endpoint} (was: {config.health_check_endpoint})")
        config.health_check_endpoint = detected_endpoint
    runtime_network = host._resolve_runtime_network(config.network)

    blue_name = f"{config.container_name}_blue"
    green_name = f"{config.container_name}_green"

    # Determine current active container
    active_container = None
    active_name = None

    # Check for blue-green containers first
    try:
        blue_container = host.client.containers.get(blue_name)
        if blue_container.status == "running":
            active_container = blue_container
            active_name = "blue"
    except docker.errors.NotFound:
        pass

    if not active_container:
        try:
            green_container = host.client.containers.get(green_name)
            if green_container.status == "running":
                active_container = green_container
                active_name = "green"
        except docker.errors.NotFound:
            pass

    # If no blue-green container found, check for main container (without suffix)
    # This handles migration from old deployment to blue-green
    if not active_container:
        try:
            main_container = host.client.containers.get(config.container_name)
            if main_container.status == "running":
                active_container = main_container
                active_name = "main"
                host.console.print(f"[yellow]Found existing container '{config.container_name}', will migrate to blue-green[/yellow]")
        except docker.errors.NotFound:
            pass

    target_name = "green" if active_name == "blue" else "blue"
    target_container_name = green_name if target_name == "green" else blue_name

    host.console.print(f"[cyan]Current active: {active_name or 'none'} | Deploying to: {target_name}[/cyan]")

    # CHECKPOINT 1: Check for cancellation before backup
    if host._check_cancel_flag():
        host.console.print("[yellow]🛑 Deployment cancelled by user (before backup)[/yellow]")
        host._current_deployment_container = None
        # Clean up any orphaned backup containers
        host._cleanup_backup_containers()
        return False

    # Backup OUTSIDE Progress context to avoid "Only one live display" error
    backup_path = None
    if active_container and not skip_backup:
        host._update_progress('backup', 5, '💾 Creating data backup...')
        host.console.print(f"[cyan]💾 Backing up container data...[/cyan]")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"backup_{config.container_name}_pre_deploy_{timestamp}"

        # Try to reuse existing backup first (reuse_existing=True by default, max age 24 hours)
        if host.backup_container_data(active_container.name, backup_path, reuse_existing=True, max_backup_age_hours=24):
            host._update_progress('backup', 20, '✅ Backup completed')
            # Check if backup was reused or newly created
            backup_dir = Path(backup_path)
            if backup_dir.exists() and (backup_dir / 'backup_metadata.json').exists():
                try:
                    with open(backup_dir / 'backup_metadata.json', 'r') as f:
                        metadata = json.load(f)
                    backup_time_str = metadata.get('backup_time', '')
                    if backup_time_str:
                        backup_time = datetime.fromisoformat(backup_time_str.replace('Z', '+00:00'))
                        if backup_time.tzinfo is None:
                            backup_time = backup_time.replace(tzinfo=datetime.now().astimezone().tzinfo)
                        age_seconds = (datetime.now(backup_time.tzinfo) - backup_time).total_seconds()
                        if age_seconds > 60:  # More than 1 minute old, it was reused
                            host.console.print(f"[green]💾 Using existing backup: {backup_path}[/green]")
                        else:
                            host.console.print(f"[green]💾 Data backup saved to: {backup_path}[/green]")
                except:
                    host.console.print(f"[green]💾 Data backup saved to: {backup_path}[/green]")
            else:
                host.console.print(f"[green]💾 Using existing backup[/green]")
        else:
            host.console.print(f"[yellow]⚠️ Data backup failed, but continuing deployment...[/yellow]")
            host.logger.warning("Data backup failed before deployment - this is risky for production!")
    elif skip_backup and active_container:
        host.console.print("[yellow]⚠️ Skipping data backup (--skip-backup flag)[/yellow]")
    else:
        host.console.print("[cyan]ℹ️ No active container to backup[/cyan]")

    # CHECKPOINT 2: Check for cancellation after backup
    if host._check_cancel_flag():
        host.console.print("[yellow]🛑 Deployment cancelled by user (after backup)[/yellow]")
        host._current_deployment_container = None
        # Clean up any orphaned backup containers
        host._cleanup_backup_containers()
        return False

    # Clean up any orphaned backup containers from previous interrupted deployments
    # This ensures we start with a clean state
    host._cleanup_backup_containers()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:

        # Add backup status to progress display
        if backup_path:
            progress.add_task(f"✅ Data backed up to {backup_path}", total=None)

        # Build or pull image
        build_task = progress.add_task("🔨 Preparing image...", total=None)
        try:
            success, message = host._prepare_image(config.image_tag, build_config, config.container_name)
            if not success:
                progress.update(build_task, description=f"❌ {message}")
                host.console.print(f"[bold red]❌ {message}[/bold red]")
                return False
            progress.update(build_task, description=f"✅ {message}")
        except Exception as e:
            progress.update(build_task, description="❌ Image preparation failed")
            host.logger.error(f"Image preparation failed: {e}")
            host.console.print(f"[bold red]❌ Image preparation failed: {e}[/bold red]")
            return False

        # Clean up existing target container
        cleanup_task = progress.add_task(f"🧹 Cleaning up {target_name} slot...", total=None)
        try:
            old_target = host.client.containers.get(target_container_name)
            old_target.stop()
            old_target.remove()
        except docker.errors.NotFound:
            pass
        progress.update(cleanup_task, description=f"✅ {target_name.title()} slot cleaned")

        # Deploy to target slot
        host._update_progress('deploy', 50, f'🚀 Deploying to slot {target_name}...')
        deploy_task = progress.add_task(f"🚀 Deploying to {target_name} slot...", total=None)

        # Prepare container creation parameters
        normalized_volumes = host._normalize_volumes(config.volumes)
        host.logger.debug(f"Normalized volumes: {normalized_volumes}")

        container_kwargs = {
            'image': config.image_tag,
            'name': target_container_name,
            'detach': True,
            'environment': config.environment,
            'volumes': normalized_volumes,
            'restart_policy': {"Name": config.restart_policy},
        }

        # Handle network mode
        if runtime_network == 'host':
            # With host network, ports are directly mapped - no port mapping needed
            container_kwargs['network_mode'] = 'host'
            # For host network, we can't use different ports for testing
            # So we need to stop the active container first to free up the port
            if active_container:
                # Stop the active container to free up the port for host network
                container_to_stop_name = active_container.name
                host.console.print(f"[yellow]Stopping active container '{container_to_stop_name}' to free port for host network...[/yellow]")
                try:
                    active_container.stop(timeout=10)
                    host.console.print(f"[green]Active container stopped[/green]")
                    # Wait a moment for port to be released
                    time.sleep(2)
                except Exception as e:
                    host.logger.warning(f"Failed to stop active container: {e}")
                    # Try to continue anyway - the new container might fail with port conflict
            temp_port_mapping = None
        else:
            # Use different port for parallel testing when not using host network
            temp_port_mapping = None  # Initialize before conditional
            if config.port_mapping and len(config.port_mapping) > 0:
                temp_port_mapping = _offset_port_mapping_impl(config.port_mapping, 1000)
                container_kwargs['ports'] = temp_port_mapping
            if runtime_network and runtime_network != 'bridge':
                container_kwargs['network'] = runtime_network

        # Add resource limits
        container_kwargs.update(host._get_resource_limits(config))

        # Add privileged mode if requested (needed for DB2 with bind mounts to support setuid)
        # Also auto-detect for infrastructure containers (minikube, kubernetes, etc.)
        requires_privileged = _requires_privileged_mode_impl(
            config,
            active_container,
            log_info=host.logger.info,
            log_debug=host.logger.debug,
            active_copy_description="new container",
        )

        if requires_privileged:
            container_kwargs['privileged'] = True
            host.logger.info(f"Container {target_container_name} will run in privileged mode")

        # Add command if provided in config (for images that exit immediately without command)
        _apply_container_command_impl(container_kwargs, config)

        try:
            target_container = host.client.containers.run(**container_kwargs)

            progress.update(deploy_task, description=f"✅ {target_name.title()} container deployed")
            host._update_progress('deploy', 60, f'✅ Kontener {target_name} wdrożony')

            # Longer startup grace period for databases and services with slow startup
            startup_grace = 5
            db_config = host._get_database_config(config.image_tag)
            if db_config:
                startup_grace = db_config.get('startup_grace_period', 15)
                db_name = host._get_database_name(config.image_tag) or 'database'
                host.logger.info(f"Extended startup grace period: {startup_grace}s for {db_name} service")

            time.sleep(startup_grace)

            # CHECKPOINT 3: Check for cancellation after container creation
            if host._check_cancel_flag():
                host.console.print("[yellow]🛑 Deployment cancelled by user (after container creation)[/yellow]")
                # Cleanup new container
                try:
                    target_container.stop()
                    target_container.remove()
                except:
                    pass
                host._current_deployment_container = None
                # Clean up any orphaned backup containers
                host._cleanup_backup_containers()
                return False

            # Migrate data from active container to new container
            if active_container and active_container.status == "running":
                migrate_task = progress.add_task("📦 Migrating data to new container...", total=None)
                try:
                    migration_success = host._migrate_container_data(active_container, target_container, config)
                    if migration_success:
                        progress.update(migrate_task, description="✅ Data migration completed")
                    else:
                        progress.update(migrate_task, description="⚠️ Data migration had issues (continuing...)")
                        host.logger.warning("Data migration completed with warnings, continuing deployment")
                except Exception as e:
                    host.logger.error(f"Data migration failed: {e}")
                    progress.update(migrate_task, description="⚠️ Data migration failed (continuing...)")
                    # Don't fail deployment if migration fails - just log warning

        except Exception as e:
            progress.update(deploy_task, description=f"❌ {target_name.title()} deployment failed")
            host.logger.error(f"Container creation failed: {e}")
            host.logger.error(f"Container kwargs: {container_kwargs}")
            # Try to get more details about the error
            if hasattr(e, 'explanation'):
                host.logger.error(f"Error explanation: {e.explanation}")
            return False

        # Comprehensive validation of new deployment
        host._update_progress('health_check', 70, f'🩺 Checking container health {target_name}...')
        health_task = progress.add_task(f"🔍 Comprehensive validation of {target_name} deployment...", total=None)

        # Determine port for validation
        validation_port = None
        if runtime_network == 'host':
            # With host network, use the original port directly
            validation_port = list(config.port_mapping.values())[0] if config.port_mapping else '3000'
        elif 'temp_port_mapping' in locals() and temp_port_mapping and len(temp_port_mapping) > 0:
            validation_port = list(temp_port_mapping.values())[0]
        else:
            validation_port = list(config.port_mapping.values())[0] if config.port_mapping else None

        if validation_port:
            # Check if this is a non-HTTP service (endpoint is None)
            if config.health_check_endpoint is None:
                # Skip HTTP health check for non-HTTP services (SSH, Redis, infrastructure, etc.)
                progress.update(health_task, description=f"ℹ️ Skipping HTTP health check (non-HTTP service)")
                host.logger.info(f"Skipping HTTP health check for {target_name} (non-HTTP service)")
            else:
                # First, do basic health check to ensure service is responding
                progress.update(health_task, description=f"🩺 Basic health check ({target_name})...")

                # Increase retries for slow-starting services (databases, etc.)
                health_retries = config.health_check_retries
                db_config = host._get_database_config(config.image_tag)
                if db_config:
                    health_retries = max(health_retries, db_config.get('health_check_retries', 20))
                    db_name = host._get_database_name(config.image_tag) or 'database'
                    host.logger.info(f"Extended health check retries: {health_retries} for {db_name} service")

                    # Add extra wait time before validation if configured
                    additional_wait = db_config.get('additional_wait_before_validation', 0)
                    if additional_wait > 0:
                        host.logger.info(f"Waiting additional {additional_wait}s for {db_name} to finish initialization...")
                        time.sleep(additional_wait)

                if not host._advanced_health_check(
                    validation_port,
                    config.health_check_endpoint,
                    config.health_check_timeout,
                    health_retries
                ):
                    progress.update(health_task, description=f"❌ {target_name.title()} basic health check failed")
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    return False

            # Then, comprehensive validation
            progress.update(health_task, description=f"🔍 Comprehensive validation ({target_name})...")
            is_valid, error_msg = host._comprehensive_container_validation(
                target_container, config, validation_port, target_name
            )

            if not is_valid:
                progress.update(health_task, description=f"❌ {target_name.title()} validation failed")
                host.logger.error(f"Container validation failed: {error_msg}")

                # Get container logs for debugging
                try:
                    logs = target_container.logs(tail=50).decode('utf-8', errors='ignore')
                    host.logger.error(f"Container logs (last 50 lines):\n{logs}")
                except:
                    pass

                # Cleanup failed container
                try:
                    target_container.stop()
                    target_container.remove()
                except:
                    pass

                return False

            progress.update(health_task, description=f"✅ {target_name.title()} validation passed")
            host._update_progress('health_check', 80, f'✅ Validation of {target_name} completed successfully')
        else:
            host.logger.warning("No ports mapped for validation, skipping comprehensive check")
            progress.update(health_task, description=f"⚠️ {target_name.title()} no ports to validate")

            # Still do basic container status check
            try:
                target_container.reload()
                if target_container.status != "running":
                    progress.update(health_task, description=f"❌ {target_name.title()} container not running")
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    return False
            except Exception as e:
                host.logger.warning(f"Could not verify container status: {e}")

        # Parallel testing phase (optional)
        if host._should_run_parallel_tests():
            test_task = progress.add_task("🧪 Running parallel tests...", total=None)
            # Check if container has ports for testing
            has_ports = config.port_mapping and len(config.port_mapping) > 0

            if not has_ports:
                host.logger.warning("No ports mapped, skipping parallel tests")
                progress.update(test_task, description="⚠️ No ports to test")
            else:
                # Determine test port based on network mode
                if runtime_network == 'host':
                    test_port = list(config.port_mapping.values())[0]
                elif 'temp_port_mapping' in locals() and temp_port_mapping and len(temp_port_mapping) > 0:
                    test_port = list(temp_port_mapping.values())[0]
                else:
                    test_port = list(config.port_mapping.values())[0]

                if not host._run_parallel_tests(test_port, config):
                    progress.update(test_task, description="❌ Parallel tests failed")
                    # Cleanup and abort
                    try:
                        target_container.stop()
                        target_container.remove()
                    except:
                        pass
                    return False
                progress.update(test_task, description="✅ Parallel tests passed")

        # CHECKPOINT 4: Check for cancellation before traffic switch
        if host._check_cancel_flag():
            host.console.print("[yellow]🛑 Deployment cancelled by user (before traffic switch)[/yellow]")
            # Cleanup target container
            try:
                target_container.stop()
                target_container.remove()
            except:
                pass
            host._current_deployment_container = None
            return False

        # Traffic switch with zero-downtime
        host._update_progress('traffic_switch', 90, '🔄 Switching traffic (zero-downtime)...')
        switch_task = progress.add_task("🔄 Zero-downtime traffic switch...", total=None)

        try:
            # Stop target container temporarily
            target_container.stop()
            target_container.remove()

            # CRITICAL: Stop old container BEFORE creating final container with original ports
            # Otherwise we'll get "port is already allocated" error
            if active_container and active_container.status == "running":
                host.console.print(f"[cyan]🛑 Stopping old container '{active_container.name}' to free ports...[/cyan]")
                try:
                    active_container.stop(timeout=10)
                    host.console.print(f"[green]✅ Old container '{active_container.name}' stopped[/green]")
                    # Wait for ports to be released
                    time.sleep(2)
                except Exception as e:
                    host.logger.warning(f"Failed to stop old container: {e}")
                    raise Exception(f"Cannot proceed: failed to stop old container: {e}")

            # Create final container with correct configuration
            final_normalized_volumes = host._normalize_volumes(config.volumes)
            host.logger.debug(f"Final normalized volumes: {final_normalized_volumes}")

            final_container_kwargs = {
                'image': config.image_tag,
                'name': target_container_name,
                'detach': True,
                'environment': config.environment,
                'volumes': final_normalized_volumes,
                'restart_policy': {"Name": config.restart_policy},
            }

            # Handle network and ports
            if runtime_network == 'host':
                final_container_kwargs['network_mode'] = 'host'
            else:
                if config.port_mapping and len(config.port_mapping) > 0:
                    final_container_kwargs['ports'] = config.port_mapping  # Final ports
                if runtime_network and runtime_network != 'bridge':
                    final_container_kwargs['network'] = runtime_network

            # Add resource limits
            final_container_kwargs.update(host._get_resource_limits(config))

            # Add privileged mode if requested (needed for DB2 with bind mounts to support setuid)
            # Also auto-detect for infrastructure containers (minikube, kubernetes, etc.)
            requires_privileged = _requires_privileged_mode_impl(
                config,
                active_container,
                log_info=host.logger.info,
                log_debug=host.logger.debug,
                active_copy_description="final container",
            )

            if requires_privileged:
                final_container_kwargs['privileged'] = True
                host.logger.info(f"Final container {target_container_name} will run in privileged mode")

            # Add command if provided in config (for images that exit immediately without command)
            _apply_container_command_impl(final_container_kwargs, config)

            # Create final container with retry on port conflict
            # Note: Final container uses the same volumes from config, so data migrated to target_container
            # will be available in final_container since they share the same volume definitions
            try:
                final_container = host.client.containers.run(**final_container_kwargs)
                host.logger.info("Final container created with migrated data (shares volumes with target)")
            except Exception as create_error:
                error_msg = str(create_error)
                # If port conflict and we have an active container, try to stop it and retry
                if ('port is already allocated' in error_msg.lower() or 'bind for' in error_msg.lower()) and active_container:
                    host.console.print(f"[yellow]⚠️ Port conflict detected, stopping old container '{active_container.name}' and retrying...[/yellow]")
                    try:
                        # Force stop old container
                        active_container.stop(timeout=5)
                        active_container.remove()
                        time.sleep(3)  # Wait for port to be released
                        # Retry creating final container
                        final_container = host.client.containers.run(**final_container_kwargs)
                        host.console.print(f"[green]✅ Final container created after stopping old container[/green]")
                    except Exception as retry_error:
                        host.logger.error(f"Failed to stop old container and retry: {retry_error}")
                        raise Exception(f"Port conflict: {error_msg}. Failed to resolve by stopping old container: {retry_error}")
                else:
                    raise

            # Wait for final container to be ready
            time.sleep(3)

            # Final comprehensive validation before traffic switch
            # Check if container has port mapping for health checks
            has_ports = config.port_mapping and len(config.port_mapping) > 0

            if has_ports:
                if runtime_network == 'host':
                    # With host network, use the original port directly
                    final_port = list(config.port_mapping.values())[0]
                else:
                    final_port = list(config.port_mapping.values())[0]

                # Final health check
                if not host._advanced_health_check(final_port, config.health_check_endpoint, 10, 5):
                    raise Exception("Final health check failed")

                # Final comprehensive validation - critical check before traffic switch
                host.console.print(f"[yellow]🔍 Final validation before traffic switch...[/yellow]")
                is_valid, error_msg = host._comprehensive_container_validation(
                    final_container, config, final_port, target_name
                )
            else:
                # No ports - just verify container is running
                host.console.print(f"[yellow]🔍 Final validation before traffic switch (no ports)...[/yellow]")
                try:
                    final_container.reload()
                    if final_container.status != "running":
                        raise Exception(f"Container is not running: {final_container.status}")
                    is_valid = True
                    error_msg = None
                except Exception as e:
                    is_valid = False
                    error_msg = str(e)

            if not is_valid:
                error_msg_full = f"Final validation failed before traffic switch: {error_msg}"
                host.logger.error(error_msg_full)

                # Get detailed logs
                try:
                    logs = final_container.logs(tail=100).decode('utf-8', errors='ignore')
                    host.logger.error(f"Final container logs:\n{logs}")
                except:
                    pass

                # Cleanup and rollback
                try:
                    final_container.stop()
                    final_container.remove()
                except:
                    pass

                # Restart old container if it exists (rollback)
                if active_container:
                    try:
                        host.console.print(f"[yellow]🔄 Rolling back to previous container...[/yellow]")
                        active_container.start()
                        host.console.print(f"[green]✅ Rollback successful - previous container restarted[/green]")
                    except Exception as e:
                        host.logger.error(f"Rollback failed: {e}")

                raise Exception(error_msg_full)

            host.console.print(f"[green]✅ Final validation passed - deployment successful[/green]")

            # Old container was already stopped before creating final container
            # Now just remove it if it still exists
            if active_container:
                try:
                    if active_container.status != 'exited':
                        active_container.stop(timeout=10)
                    active_container.remove()
                    host.console.print(f"[green]✅ Old container '{active_container.name}' removed[/green]")
                except docker.errors.NotFound:
                    # Already removed, that's fine
                    pass
                except Exception as e:
                    host.logger.warning(f"Failed to remove old container: {e}")

            progress.update(switch_task, description="✅ Traffic switched successfully")
            host._update_progress('traffic_switch', 95, '✅ Traffic switch completed')

        except Exception as e:
            progress.update(switch_task, description="❌ Traffic switch failed")
            host._update_progress('traffic_switch', 0, f'❌ Traffic switch failed: {e}')
            host.logger.error(f"Traffic switch failed: {e}")
            return False

    deployment_end = datetime.now()
    duration = deployment_end - deployment_start

    host._record_deployment(deployment_id, config, "blue-green", True, duration)

    # Clear deployment tracking
    host._current_deployment_container = None

    host._update_progress('completed', 100, '🎉 Deployment completed successfully!')
    host.console.print(f"\n[bold green]🎉 BLUE-GREEN DEPLOYMENT COMPLETED![/bold green]")
    host.console.print(f"[green]Active slot: {target_name}[/green]")
    host.console.print(f"[green]Duration: {duration.total_seconds():.1f}s[/green]")

    return True
