"""Container migration API resource."""

from __future__ import annotations

import os
import subprocess


def create_migration_resource(
    *,
    Resource,
    app,
    request,
    datetime_cls,
    migration_progress,
    migration_cancel_flags,
    load_servers_config,
    get_dockerpilot,
    execute_command_via_ssh,
    execute_docker_command_via_ssh,
    save_deployment_config,
    infer_port_mapping_for_host_network,
):
    """Return the container migration resource class with injected dependencies."""

    datetime = datetime_cls
    _migration_progress = migration_progress
    _migration_cancel_flags = migration_cancel_flags
    _infer_port_mapping_for_host_network = infer_port_mapping_for_host_network

    class ContainerMigrate(Resource):
        """Migrate container from one server to another"""
        def post(self):
            try:
                data = request.get_json()
                container_name = data.get('container_name')
                source_server_id = data.get('source_server_id', 'local')
                target_server_id = data.get('target_server_id')
                include_data = data.get('include_data', False)  # Whether to migrate volumes/data
                stop_source = data.get('stop_source', False)  # Whether to stop source container
                
                if not container_name or not target_server_id:
                    return {'error': 'container_name and target_server_id are required'}, 400
                
                if source_server_id == target_server_id:
                    return {'error': 'Source and target servers must be different'}, 400
                
                # Initialize progress tracking
                _migration_progress[container_name] = {
                    'stage': 'initializing',
                    'progress': 0,
                    'message': f'Inicjalizacja migracji {container_name}...',
                    'timestamp': datetime.now().isoformat(),
                    'logs': [f"[{datetime.now().strftime('%H:%M:%S')}] Initializing migration for {container_name}"],
                }
                _migration_cancel_flags[container_name] = False
                
                def check_cancel():
                    """Check if migration was cancelled"""
                    if _migration_cancel_flags.get(container_name, False):
                        raise Exception('Migration was cancelled by user')
                
                def update_progress(stage, progress, message):
                    """Update migration progress"""
                    if not _migration_cancel_flags.get(container_name, False):
                        prev = _migration_progress.get(container_name, {})
                        logs = list(prev.get('logs', []))
                        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
                        if not logs or logs[-1] != line:
                            logs.append(line)
                        logs = logs[-300:]  # keep recent lines only
                        _migration_progress[container_name] = {
                            'stage': stage,
                            'progress': progress,
                            'message': message,
                            'timestamp': datetime.now().isoformat(),
                            'logs': logs
                        }
                
                # Load server configs
                config = load_servers_config()
                target_server = None
                if target_server_id == 'local':
                    # Local server - create a dummy config for local operations
                    target_server = {'id': 'local', 'hostname': 'localhost'}
                else:
                    for server in config.get('servers', []):
                        if server.get('id') == target_server_id:
                            target_server = server
                            break
                    
                    if not target_server:
                        update_progress('failed', 0, 'Target server not found')
                        return {'error': 'Target server not found'}, 404
                
                # Get source server config
                source_server = None
                if source_server_id != 'local':
                    for server in config.get('servers', []):
                        if server.get('id') == source_server_id:
                            source_server = server
                            break
                    if not source_server:
                        update_progress('failed', 0, 'Source server not found')
                        return {'error': 'Source server not found'}, 404
                
                app.logger.info(f"Starting migration of {container_name} from {source_server_id} to {target_server_id}")
                
                # Step 1: Extract container configuration from source
                update_progress('extracting', 10, 'Extracting container configuration from source...')
                check_cancel()
                
                container_config = None
                image_tag = None
                export_image_tag = None  # Tag used for export/import
                
                # Create export image tag early (needed for deployment config)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                export_image_tag = f"{container_name}_migrated:{timestamp}"
                
                if source_server_id == 'local':
                    # Get from local Docker
                    pilot = get_dockerpilot()
                    try:
                        container = pilot.client.containers.get(container_name)
                        attrs = container.attrs
                        
                        # Extract image tag
                        image_tag = attrs.get('Config', {}).get('Image', '')
                        if not image_tag:
                            image_tag = container.image.tags[0] if container.image.tags else container.image.id
                        
                        # Extract full configuration
                        container_config = self._extract_container_config(container)
                    except Exception as e:
                        app.logger.error(f"Error extracting container config for {container_name}: {e}", exc_info=True)
                        update_progress('failed', 0, f'Error extracting container configuration: {str(e)}')
                        return {'error': f'Failed to get container from source: {str(e)}'}, 500
                else:
                    # Get from remote server via SSH
                    try:
                        # Get container inspect output
                        inspect_output = execute_docker_command_via_ssh(
                            source_server,
                            f"inspect {container_name} --format '{{{{json .}}}}'"
                        )
                        import json
                        # Clean up inspect output - remove any trailing whitespace/newlines
                        inspect_output = inspect_output.strip()
                        # Try to parse JSON
                        try:
                            attrs = json.loads(inspect_output)
                        except json.JSONDecodeError as e:
                            app.logger.error(f"Failed to parse docker inspect JSON for {container_name}")
                            app.logger.error(f"Inspect output (first 1000 chars): {inspect_output[:1000]}")
                            update_progress('failed', 0, f'Error parsing container configuration: {str(e)}')
                            raise Exception(f"Failed to parse container inspect output as JSON: {str(e)}")
                        
                        # Extract image tag
                        image_tag = attrs.get('Config', {}).get('Image', '')
                        if not image_tag:
                            image_id = attrs.get('Image', '')
                            # Try to get image tag from image ID
                            image_tag = image_id[:12] if image_id else 'unknown'
                        
                        # Extract configuration from inspect output
                        container_config = self._extract_container_config_from_inspect(attrs)
                    except json.JSONDecodeError as e:
                        app.logger.error(f"JSON decode error for container {container_name}: {e}")
                        app.logger.error(f"Inspect output: {inspect_output[:500]}")  # Log first 500 chars
                        update_progress('failed', 0, f'Błąd podczas parsowania konfiguracji kontenera: {str(e)}')
                        return {'error': f'Failed to parse container inspect output: {str(e)}'}, 500
                    except Exception as e:
                        app.logger.error(f"Error extracting container config from remote for {container_name}: {e}", exc_info=True)
                        update_progress('failed', 0, f'Error extracting container configuration: {str(e)}')
                        return {'error': f'Failed to get container from source server: {str(e)}'}, 500
    
                if container_config and container_config.get('skipped_bind_mounts'):
                    skipped = container_config['skipped_bind_mounts']
                    app.logger.warning(
                        f"Skipping {len(skipped)} bind mount(s) during migration for {container_name}: {skipped}"
                    )
                
                # Step 1.5: Save deployment config (YAML) for proper container recreation
                update_progress('saving_config', 15, 'Saving deployment configuration...')
                check_cancel()
                
                try:
                    # Create deployment config structure
                    deployment_config = {
                        'deployment': {
                            'image_tag': container_config.get('image_tag', image_tag),
                            'container_name': container_name,
                            'port_mapping': container_config.get('port_mapping', {}),
                            'environment': container_config.get('environment', {}),
                            'volumes': container_config.get('volumes', {}),
                            'restart_policy': container_config.get('restart_policy', 'unless-stopped'),
                            'network': container_config.get('network', 'bridge'),
                            'cpu_limit': container_config.get('cpu_limit'),
                            'memory_limit': container_config.get('memory_limit')
                        }
                    }
                    
                    # Add command if present
                    if container_config.get('command'):
                        # Convert list to string if needed
                        cmd = container_config['command']
                        if isinstance(cmd, list):
                            deployment_config['deployment']['command'] = ' '.join(cmd) if cmd else None
                        else:
                            deployment_config['deployment']['command'] = cmd
                    
                    # Save deployment config
                    config_path = save_deployment_config(
                        container_name, 
                        deployment_config, 
                        image_tag=export_image_tag
                    )
                    app.logger.info(f"Saved deployment config to: {config_path}")
                except Exception as e:
                    app.logger.warning(f"Failed to save deployment config: {e}. Continuing with migration...")
                    # Don't fail migration if config save fails
                
                # Step 2: Export image from source
                update_progress('exporting', 20, f'Exporting image {image_tag} from source...')
                check_cancel()
                
                app.logger.info(f"Exporting image {image_tag} from source...")
                image_export_path = None
                
                # export_image_tag was already created earlier (before saving deployment config)
                if source_server_id == 'local':
                    # Export image locally
                    import tempfile
                    image_export_path = tempfile.NamedTemporaryFile(delete=False, suffix='.tar')
                    image_export_path.close()
                    
                    try:
                        # Get the image object
                        source_image = pilot.client.images.get(image_tag)
                        image_id = source_image.id
                        app.logger.info(f"Source image ID: {image_id}, Tags: {source_image.tags}")
                        
                        # Tag the image with export tag
                        app.logger.info(f"Tagging image {image_tag} as {export_image_tag}...")
                        source_image.tag(export_image_tag)
                        
                        # Give Docker a moment to sync the tag
                        import time
                        time.sleep(0.5)
                        
                        # Verify the tag was created by checking if image can be retrieved by tag
                        try:
                            tagged_image = pilot.client.images.get(export_image_tag)
                            app.logger.info(f"Successfully tagged image. Image ID: {tagged_image.id}, Tags: {tagged_image.tags}")
                            
                            # Verify export_image_tag is in the tags list
                            if export_image_tag not in tagged_image.tags:
                                app.logger.warning(f"Tag {export_image_tag} not found in image tags: {tagged_image.tags}")
                                # Try to use docker tag command as fallback
                                app.logger.info(f"Trying docker tag command as fallback...")
                                result = subprocess.run(
                                    ['docker', 'tag', image_id, export_image_tag],
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                if result.returncode != 0:
                                    raise Exception(f"docker tag failed: {result.stderr}")
                                # Reload image after tagging
                                tagged_image = pilot.client.images.get(export_image_tag)
                                app.logger.info(f"Tagged via docker command. Tags: {tagged_image.tags}")
                        except Exception as e:
                            app.logger.error(f"Failed to verify tagged image: {e}")
                            # Try docker tag command as fallback
                            app.logger.info(f"Trying docker tag command as fallback...")
                            result = subprocess.run(
                                ['docker', 'tag', image_id, export_image_tag],
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                            if result.returncode != 0:
                                raise Exception(f"Image tag {export_image_tag} was not created successfully: {result.stderr}")
                            app.logger.info(f"Tagged via docker command successfully")
                        
                        # Save image to tar using image ID (more reliable than tag)
                        # But also include the tag so it's preserved
                        app.logger.info(f"Saving image {export_image_tag} (ID: {image_id}) to {image_export_path.name}...")
                        result = subprocess.run(
                            ['docker', 'save', '-o', image_export_path.name, export_image_tag],
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        if result.returncode != 0:
                            app.logger.error(f"docker save failed: stdout={result.stdout}, stderr={result.stderr}")
                            # Try with image ID as fallback
                            app.logger.info(f"Trying docker save with image ID as fallback...")
                            result = subprocess.run(
                                ['docker', 'save', '-o', image_export_path.name, image_id],
                                capture_output=True,
                                text=True,
                                timeout=300
                            )
                            if result.returncode != 0:
                                raise Exception(f"Failed to save image: {result.stderr}")
                            app.logger.warning(f"Saved using image ID instead of tag. Tag may not be preserved.")
                        
                        app.logger.info(f"Image saved successfully. File size: {os.path.getsize(image_export_path.name)} bytes")
                    except Exception as e:
                        error_msg = f'Failed to export image: {str(e)}'
                        app.logger.error(f"Migration error during image export (local): {error_msg}", exc_info=True)
                        if image_export_path and os.path.exists(image_export_path.name):
                            os.unlink(image_export_path.name)
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 500
                else:
                    # Export image from remote server
                    import tempfile
                    image_export_path = tempfile.NamedTemporaryFile(delete=False, suffix='.tar')
                    image_export_path.close()
                    
                    try:
                        # export_image_tag is already defined above
                        # Tag and save image on remote server
                        execute_docker_command_via_ssh(
                            source_server,
                            f"tag {image_tag} {export_image_tag}"
                        )
                        
                        app.logger.info(f"Tagged image on remote source with tag: {export_image_tag}")
                        
                        # Save image to tar on remote
                        remote_tar_path = f"/tmp/{container_name}_migrated_{datetime.now().strftime('%Y%m%d%H%M%S')}.tar"
                        execute_docker_command_via_ssh(
                            source_server,
                            f"save -o {remote_tar_path} {export_image_tag}"
                        )
                        
                        # Download tar file via SCP
                        import paramiko
                        from io import BytesIO
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        
                        # Connect and download
                        if source_server.get('auth_type') == 'password':
                            ssh.connect(
                                source_server.get('hostname'),
                                port=source_server.get('port', 22),
                                username=source_server.get('username'),
                                password=source_server.get('password'),
                                timeout=10
                            )
                        elif source_server.get('auth_type') == 'key':
                            from io import StringIO
                            key_file = StringIO(source_server.get('private_key'))
                            try:
                                key = paramiko.RSAKey.from_private_key(key_file, password=source_server.get('key_passphrase'))
                            except:
                                key_file.seek(0)
                                try:
                                    key = paramiko.DSSKey.from_private_key(key_file, password=source_server.get('key_passphrase'))
                                except:
                                    key_file.seek(0)
                                    key = paramiko.ECDSAKey.from_private_key(key_file, password=source_server.get('key_passphrase'))
                            
                            ssh.connect(
                                source_server.get('hostname'),
                                port=source_server.get('port', 22),
                                username=source_server.get('username'),
                                pkey=key,
                                timeout=10
                            )
                        
                        # Use SFTP to download
                        sftp = ssh.open_sftp()
                        last_download_logged_percent = -1
                        
                        # Add callback to check for cancellation during download
                        def download_progress(transferred, total):
                            # Check if migration was cancelled during download
                            if _migration_cancel_flags.get(container_name, False):
                                app.logger.info(f"Migration cancelled during download at {transferred / (1024*1024):.2f} MB")
                                raise Exception('Migration was cancelled by user')
                            if total > 0:
                                nonlocal last_download_logged_percent
                                percent = (transferred / total) * 100
                                current_bucket = int(percent) // 10
                                if current_bucket > last_download_logged_percent:
                                    update_progress(
                                        'exporting',
                                        25 + int(percent * 0.2),  # 25-45
                                        f"Downloading image from source: {percent:.1f}% ({transferred / (1024*1024):.2f} MB / {total / (1024*1024):.2f} MB)"
                                    )
                                    last_download_logged_percent = current_bucket
                        
                        # Check cancel before starting download
                        check_cancel()
                        
                        sftp.get(remote_tar_path, image_export_path.name, callback=download_progress)
                        sftp.close()
                        ssh.close()
                        
                        # Clean up remote tar
                        execute_command_via_ssh(source_server, f"rm -f {remote_tar_path}", check_exit_status=False)
                    except Exception as e:
                        error_msg = f'Failed to export image from remote: {str(e)}'
                        app.logger.error(f"Migration error during image export (remote): {error_msg}", exc_info=True)
                        if image_export_path and os.path.exists(image_export_path.name):
                            os.unlink(image_export_path.name)
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 500
                
                # Step 3: Transfer image to target server
                if target_server_id == 'local':
                    # Target is local server
                    if source_server_id == 'local':
                        # Both source and target are local - image was already tagged and saved to tar
                        # Now we need to load it from tar to ensure it's available with the correct tag
                        update_progress('loading', 70, 'Loading image on local server...')
                        check_cancel()
                        try:
                            # Load image from tar file (even though it's local, we need to ensure tag is correct)
                            if image_export_path and os.path.exists(image_export_path.name):
                                result = subprocess.run(
                                    ['docker', 'load', '-i', image_export_path.name],
                                    capture_output=True,
                                    text=True,
                                    timeout=300
                                )
                                if result.returncode != 0:
                                    raise Exception(f"Failed to load image: {result.stderr}")
                                
                                # docker load outputs to stderr, so combine both
                                load_output = (result.stdout or '') + (result.stderr or '')
                                app.logger.info(f"Image load stdout: {result.stdout}")
                                app.logger.info(f"Image load stderr: {result.stderr}")
                                app.logger.info(f"Image load combined output: {load_output}")
                                
                                # Verify image was loaded with correct tag
                                if 'Loaded image:' in load_output:
                                    for line in load_output.split('\n'):
                                        if 'Loaded image:' in line:
                                            loaded_tag = line.split('Loaded image:')[1].strip()
                                            if loaded_tag:
                                                app.logger.info(f"Image loaded with tag: {loaded_tag}")
                                                if export_image_tag not in loaded_tag and container_name in loaded_tag:
                                                    export_image_tag = loaded_tag
                                                break
                                
                                # Verify image exists
                                pilot = get_dockerpilot()
                                images = pilot.client.images.list()
                                image_found = False
                                for img in images:
                                    if export_image_tag in [tag for tag_list in img.tags for tag in tag_list]:
                                        image_found = True
                                        app.logger.info(f"Image {export_image_tag} verified locally")
                                        break
                                if not image_found:
                                    raise Exception(f"Image {export_image_tag} not found locally after loading")
                            else:
                                raise Exception(f"Image tar file not found: {image_export_path.name if image_export_path else 'None'}")
                        except Exception as e:
                            error_msg = f'Failed to load image on local server: {str(e)}'
                            app.logger.error(f"Migration error during image loading: {error_msg}", exc_info=True)
                            update_progress('failed', 0, error_msg)
                            return {'error': error_msg}, 500
                    else:
                        # Source is remote, target is local. Image was already downloaded in Step 2 into image_export_path.
                        # Just load it locally (no second download).
                        update_progress('loading', 70, 'Loading image on local server...')
                        check_cancel()
                        
                        try:
                            if not image_export_path or not os.path.exists(image_export_path.name):
                                raise Exception('Image tar not available (export from remote may have failed)')
                            
                            result = subprocess.run(
                                ['docker', 'load', '-i', image_export_path.name],
                                capture_output=True,
                                text=True,
                                timeout=300
                            )
                            if result.returncode != 0:
                                raise Exception(f"Failed to load image: {result.stderr}")
                            
                            load_output = result.stdout
                            app.logger.info(f"Image load output: {load_output}")
                            
                            # Verify image was loaded
                            if 'Loaded image:' in load_output:
                                for line in load_output.split('\n'):
                                    if 'Loaded image:' in line:
                                        loaded_tag = line.split('Loaded image:')[1].strip()
                                        if loaded_tag:
                                            app.logger.info(f"Image loaded with tag: {loaded_tag}")
                                            if export_image_tag not in loaded_tag and container_name in loaded_tag:
                                                export_image_tag = loaded_tag
                                            break
                        except Exception as e:
                            error_msg = f'Failed to load image from remote export: {str(e)}'
                            app.logger.error(f"Migration error during image load: {error_msg}", exc_info=True)
                            update_progress('failed', 0, error_msg)
                            return {'error': error_msg}, 500
                else:
                    # Target is remote server
                    update_progress('transferring', 50, f'Transferring image to target server {target_server.get("hostname")}...')
                    check_cancel()
                    
                    app.logger.info(f"Transferring image to target server {target_server.get('hostname')}...")
                    try:
                        import paramiko
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        
                        # Connect to target server
                        try:
                            if target_server.get('auth_type') == 'password':
                                ssh.connect(
                                    target_server.get('hostname'),
                                    port=target_server.get('port', 22),
                                    username=target_server.get('username'),
                                    password=target_server.get('password'),
                                    timeout=30  # Increased timeout for large file transfers
                                )
                            elif target_server.get('auth_type') == 'key':
                                from io import StringIO
                                key_file = StringIO(target_server.get('private_key'))
                                try:
                                    key = paramiko.RSAKey.from_private_key(key_file, password=target_server.get('key_passphrase'))
                                except:
                                    key_file.seek(0)
                                    try:
                                        key = paramiko.DSSKey.from_private_key(key_file, password=target_server.get('key_passphrase'))
                                    except:
                                        key_file.seek(0)
                                        key = paramiko.ECDSAKey.from_private_key(key_file, password=target_server.get('key_passphrase'))
                                
                                ssh.connect(
                                    target_server.get('hostname'),
                                    port=target_server.get('port', 22),
                                    username=target_server.get('username'),
                                    pkey=key,
                                    timeout=30  # Increased timeout for large file transfers
                                )
                            else:
                                raise Exception(f"Unsupported auth_type: {target_server.get('auth_type')}")
                        except Exception as e:
                            app.logger.error(f"Failed to connect to target server {target_server.get('hostname')}: {e}", exc_info=True)
                            update_progress('failed', 0, f'Error connecting to target server: {str(e)}')
                            raise Exception(f"Failed to connect to target server: {str(e)}")
                        
                        # Upload image tar via SFTP
                        remote_tar_path = f"/tmp/{container_name}_migrated_{datetime.now().strftime('%Y%m%d%H%M%S')}.tar"
                        
                        # Check if local file exists
                        if not os.path.exists(image_export_path.name):
                            raise Exception(f"Local image file does not exist: {image_export_path.name}")
                        
                        file_size = os.path.getsize(image_export_path.name)
                        file_size_mb = file_size / (1024*1024)
                        app.logger.info(f"Uploading image file {image_export_path.name} ({file_size_mb:.2f} MB) to {remote_tar_path}")
                        
                        # Check available disk space on target server before transfer
                        try:
                            df_output = execute_command_via_ssh(target_server, "df -m /tmp | tail -1 | awk '{print $4}'")
                            available_space_mb = int(df_output.strip())
                            app.logger.info(f"Available disk space on target server /tmp: {available_space_mb} MB")
                            
                            # Add 20% buffer for safety
                            required_space_mb = int(file_size_mb * 1.2)
                            if available_space_mb < required_space_mb:
                                raise Exception(f"Insufficient disk space on target server. Required: {required_space_mb} MB, Available: {available_space_mb} MB")
                        except ValueError:
                            app.logger.warning("Could not parse available disk space, continuing anyway...")
                        except Exception as e:
                            app.logger.warning(f"Could not check disk space: {e}, continuing anyway...")
                        
                        # Check write permissions in /tmp
                        try:
                            test_output = execute_command_via_ssh(target_server, f"touch {remote_tar_path}.test && rm -f {remote_tar_path}.test && echo 'OK'")
                            if 'OK' not in test_output:
                                raise Exception("Cannot write to /tmp directory on target server")
                            app.logger.info("Write permissions verified in /tmp")
                        except Exception as e:
                            app.logger.error(f"Cannot write to /tmp on target server: {e}")
                            update_progress('failed', 0, f'No write permissions to /tmp on target server: {str(e)}')
                            raise Exception(f"Cannot write to /tmp directory on target server: {str(e)}")
                        
                        try:
                            sftp = ssh.open_sftp()
                            
                            # Use callback to show progress during upload
                            last_logged_percent = -1
                            def upload_progress(transferred, total):
                                nonlocal last_logged_percent
                                # Check if migration was cancelled during upload
                                if _migration_cancel_flags.get(container_name, False):
                                    app.logger.info(f"Migration cancelled during upload at {transferred / (1024*1024):.2f} MB")
                                    raise Exception('Migration was cancelled by user')
                                
                                if total > 0:
                                    percent = (transferred / total) * 100
                                    if int(percent) // 10 > last_logged_percent:  # Log every 10%
                                        app.logger.info(f"Upload progress: {percent:.1f}% ({transferred / (1024*1024):.2f} MB / {total / (1024*1024):.2f} MB)")
                                        update_progress(
                                            'transferring',
                                            50 + int(percent * 0.2),  # 50-70
                                            f"Uploading image to target: {percent:.1f}% ({transferred / (1024*1024):.2f} MB / {total / (1024*1024):.2f} MB)"
                                        )
                                        last_logged_percent = int(percent) // 10
                            
                            try:
                                # Check cancel before starting upload
                                check_cancel()
                                
                                # Try to remove any existing file first
                                try:
                                    sftp.remove(remote_tar_path)
                                    app.logger.info(f"Removed existing file: {remote_tar_path}")
                                except:
                                    pass  # File doesn't exist, that's fine
                                
                                sftp.put(image_export_path.name, remote_tar_path, callback=upload_progress)
                                app.logger.info(f"Successfully uploaded image to {remote_tar_path}")
                                
                                # Verify file was uploaded correctly
                                try:
                                    remote_stat = sftp.stat(remote_tar_path)
                                    if remote_stat.st_size != file_size:
                                        raise Exception(f"File size mismatch. Local: {file_size} bytes, Remote: {remote_stat.st_size} bytes")
                                    app.logger.info(f"File verification successful. Size: {remote_stat.st_size} bytes")
                                except Exception as verify_error:
                                    app.logger.error(f"File verification failed: {verify_error}")
                                    raise Exception(f"Uploaded file verification failed: {str(verify_error)}")
                            except IOError as sftp_error:
                                # SFTP specific error - try to get more details
                                error_msg = str(sftp_error)
                                app.logger.error(f"SFTP put failed: {sftp_error}", exc_info=True)
                                
                                # Check if it's a disk space issue
                                if 'No space left' in error_msg or 'disk full' in error_msg.lower():
                                    raise Exception(f"Insufficient disk space on target server: {error_msg}")
                                
                                # Check if it's a permission issue
                                if 'Permission denied' in error_msg or 'permission' in error_msg.lower():
                                    raise Exception(f"Permission denied on target server: {error_msg}")
                                
                                # Generic SFTP error
                                raise Exception(f"SFTP upload failed: {error_msg}. This may be due to insufficient disk space, permission issues, or network problems.")
                            except Exception as sftp_error:
                                app.logger.error(f"SFTP put failed: {sftp_error}", exc_info=True)
                                raise Exception(f"SFTP upload failed: {str(sftp_error)}")
                            finally:
                                sftp.close()
                        except Exception as e:
                            app.logger.error(f"Failed to upload image file via SFTP: {e}", exc_info=True)
                            update_progress('failed', 0, f'Error transferring image: {str(e)}')
                            raise Exception(f"Failed to upload image file: {str(e)}")
                        finally:
                            ssh.close()
                        
                        # Load image on target server
                        update_progress('loading', 70, 'Loading image on target server...')
                        check_cancel()
                        
                        app.logger.info(f"Loading image from {remote_tar_path} on target server {target_server.get('hostname')}...")
                        app.logger.info(f"Expected image tag: {export_image_tag}")
                        
                        load_output, load_stderr = execute_docker_command_via_ssh(
                            target_server,
                            f"load -i {remote_tar_path}",
                            return_stderr=True
                        )
                        # docker load outputs to stderr, so combine both
                        combined_output = (load_output or '') + (load_stderr or '')
                        app.logger.info(f"Image load stdout: {load_output}")
                        app.logger.info(f"Image load stderr: {load_stderr}")
                        app.logger.info(f"Image load combined output: {combined_output}")
                        
                        # Use combined output for parsing
                        load_output = combined_output
                        
                        # If still no output, check images after load
                        if not load_output or 'Loaded image:' not in load_output:
                            app.logger.warning(f"docker load output seems incomplete or empty. Checking images after load...")
                            # Try to get more info by checking images after load
                            images_after_load = execute_docker_command_via_ssh(
                                target_server,
                                "images --format '{{.Repository}}:{{.Tag}}' --filter 'dangling=false'"
                            )
                            app.logger.info(f"Images on target server after load: {images_after_load}")
                        
                        # After load, verify the image tag exists
                        # docker load preserves tags, so export_image_tag should be available
                        loaded_tags = []
                        try:
                            # Check what images were loaded - docker load outputs "Loaded image: repo:tag"
                            if 'Loaded image:' in load_output:
                                # Extract all loaded image tags
                                for line in load_output.split('\n'):
                                    if 'Loaded image:' in line:
                                        loaded_tag = line.split('Loaded image:')[1].strip()
                                        if loaded_tag:
                                            loaded_tags.append(loaded_tag)
                                            app.logger.info(f"Image loaded with tag: {loaded_tag}")
                                
                                # Try to find matching tag
                                if loaded_tags:
                                    # First, try exact match
                                    if export_image_tag in loaded_tags:
                                        app.logger.info(f"Found exact match for export_image_tag: {export_image_tag}")
                                    else:
                                        # Try to find tag containing container_name
                                        matching_tag = None
                                        for tag in loaded_tags:
                                            if container_name in tag or export_image_tag.split(':')[0] in tag:
                                                matching_tag = tag
                                                break
                                        
                                        if matching_tag:
                                            app.logger.info(f"Using matching tag: {matching_tag} (instead of {export_image_tag})")
                                            export_image_tag = matching_tag
                                        else:
                                            # Use first loaded tag as fallback
                                            app.logger.warning(f"No matching tag found, using first loaded tag: {loaded_tags[0]}")
                                            export_image_tag = loaded_tags[0]
                            else:
                                app.logger.warning(f"No 'Loaded image:' found in docker load output: {load_output}")
                            
                            # Verify image exists on target with exact tag match
                            app.logger.info(f"Verifying image {export_image_tag} exists on target server...")
                            images_output = execute_docker_command_via_ssh(
                                target_server,
                                f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
                            )
                            
                            # Check if our tag exists in the output
                            image_found = False
                            for line in images_output.split('\n'):
                                line = line.strip()
                                if line == export_image_tag:
                                    image_found = True
                                    app.logger.info(f"Image {export_image_tag} successfully verified on target server")
                                    break
                            
                            if not image_found:
                                # Try without tag (just repository)
                                repo_name = export_image_tag.split(':')[0]
                                for line in images_output.split('\n'):
                                    line = line.strip()
                                    if line.startswith(repo_name + ':'):
                                        # Found image with same repo but different tag - use it
                                        app.logger.warning(f"Image {export_image_tag} not found, but found {line}. Using it instead.")
                                        export_image_tag = line
                                        image_found = True
                                        break
                            
                            if not image_found:
                                # Try to find and retag the loaded image
                                app.logger.warning(f"Image {export_image_tag} not found after load. Attempting to find and retag...")
                                
                                # Get all images with their IDs
                                images_with_ids = execute_docker_command_via_ssh(
                                    target_server,
                                    "images --format '{{.ID}} {{.Repository}}:{{.Tag}}' --no-trunc"
                                )
                                
                                # Find the most recently loaded image (should be one of the loaded_tags)
                                if loaded_tags:
                                    # Try to find image by matching repository name or by checking if it's untagged
                                    for line in images_with_ids.split('\n'):
                                        line = line.strip()
                                        if not line:
                                            continue
                                        parts = line.split(' ', 1)
                                        if len(parts) == 2:
                                            img_id = parts[0]
                                            img_tag = parts[1]
                                            # Check if this image matches any of our loaded tags or is untagged
                                            for loaded_tag in loaded_tags:
                                                if loaded_tag in img_tag or img_tag == '<none>:<none>' or (export_image_tag.split(':')[0] in img_tag and img_tag != '<none>:<none>'):
                                                    # This might be our image - try to tag it
                                                    app.logger.info(f"Found potential image ID {img_id[:12]} with tag {img_tag}, retagging as {export_image_tag}...")
                                                    try:
                                                        execute_docker_command_via_ssh(
                                                            target_server,
                                                            f"tag {img_id} {export_image_tag}"
                                                        )
                                                        # Verify the tag was created
                                                        images_after_retag = execute_docker_command_via_ssh(
                                                            target_server,
                                                            f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
                                                        )
                                                        if export_image_tag in images_after_retag:
                                                            app.logger.info(f"Successfully retagged image as {export_image_tag}")
                                                            image_found = True
                                                            break
                                                    except Exception as retag_error:
                                                        app.logger.warning(f"Failed to retag image: {retag_error}")
                                                        continue
                                        
                                        if image_found:
                                            break
                                
                                # If still not found, try to use the first loaded tag
                                if not image_found:
                                    if loaded_tags:
                                        # Use the first loaded tag
                                        export_image_tag = loaded_tags[0]
                                        app.logger.warning(f"Using first loaded tag as fallback: {export_image_tag}")
                                        image_found = True
                                    else:
                                        error_msg = f"Image {export_image_tag} was not found on target server after loading. Loaded tags: {loaded_tags}, Available images: {images_output[:500]}"
                                        app.logger.error(error_msg)
                                        raise Exception(error_msg)
                                
                        except Exception as e:
                            error_msg = f"Failed to verify loaded image tag: {str(e)}"
                            app.logger.error(error_msg)
                            raise Exception(error_msg)
                        
                        # Clean up remote tar
                        execute_command_via_ssh(target_server, f"rm -f {remote_tar_path}", check_exit_status=False)
                        
                    except Exception as e:
                        error_msg = f'Failed to transfer image to target: {str(e)}'
                        app.logger.error(f"Migration error during image transfer: {error_msg}", exc_info=True)
                        if image_export_path and os.path.exists(image_export_path.name):
                            os.unlink(image_export_path.name)
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 500
                
                # Clean up local tar
                if os.path.exists(image_export_path.name):
                    os.unlink(image_export_path.name)
                
                # Step 4: Check if container exists on target and remove it if needed
                update_progress('preparing', 80, 'Preparing target server...')
                check_cancel()
                
                app.logger.info(f"Checking if container exists on target server...")
                try:
                    # Check if container exists
                    check_output = execute_docker_command_via_ssh(
                        target_server,
                        f"ps -a --filter name={container_name} --format '{{{{.Names}}}}'"
                    )
                    if container_name in check_output:
                        app.logger.info(f"Container {container_name} exists on target, removing it...")
                    # Stop and remove existing container
                    execute_docker_command_via_ssh(
                        target_server,
                        f"stop {container_name}",
                        check_exit_status=False
                    )
                    execute_docker_command_via_ssh(
                        target_server,
                        f"rm -f {container_name}",
                        check_exit_status=False
                    )
                except Exception as e:
                    app.logger.warning(f"Failed to check/remove existing container: {e}")
    
                # Step 4.5: Migrate mounted data (bind mounts + named volumes) when requested
                migration_summary = {
                    'migrated': [],
                    'skipped': [],
                    'total_migrated': 0,
                    'total_skipped': 0,
                    'total_transferable': 0,
                }
                if include_data:
                    update_progress('migrating_data', 84, 'Migrating mount data to target server...')
                    check_cancel()
                    try:
                        migration_summary = self._migrate_mount_data_between_servers(
                            container_name=container_name,
                            container_config=container_config,
                            source_server=source_server if source_server_id != 'local' else None,
                            target_server=target_server if target_server_id != 'local' else None,
                            check_cancel=check_cancel,
                            update_progress=update_progress,
                        )
    
                        skipped_mounts = migration_summary.get('skipped', []) if isinstance(migration_summary, dict) else []
                        source_mounts = container_config.get('mounts') or []
                        volume_destinations = [
                            (m.get('destination') or '').rstrip('/')
                            for m in source_mounts
                            if (m.get('type') or '').strip() == 'volume' and (m.get('destination') or '').strip()
                        ]
    
                        # Remove skipped bind mounts from runtime config to avoid masking migrated volume data.
                        skipped_bind_sources = {
                            (item.get('source') or '').strip()
                            for item in skipped_mounts
                            if (item.get('type') or '').strip() == 'bind' and (item.get('source') or '').strip()
                        }
                        if skipped_bind_sources:
                            for skipped_source in skipped_bind_sources:
                                if skipped_source in (container_config.get('volumes') or {}):
                                    container_config['volumes'].pop(skipped_source, None)
                            container_config['mounts'] = [
                                m for m in source_mounts
                                if not (
                                    (m.get('type') or '').strip() == 'bind'
                                    and (m.get('source') or '').strip() in skipped_bind_sources
                                )
                            ]
    
                        def _starts_with_path(path_value: str, prefix: str) -> bool:
                            normalized = (path_value or '').rstrip('/')
                            base = (prefix or '').rstrip('/')
                            return normalized == base or normalized.startswith(base + '/')
    
                        critical_data_prefixes = (
                            '/database',
                            '/data',
                            '/var/lib',
                            '/hadr',
                            '/bitnami',
                            '/usr/share/elasticsearch/data',
                            '/kafka',
                        )
    
                        risky_skips = []
                        for skipped in skipped_mounts:
                            destination = (skipped.get('destination') or '').rstrip('/')
                            reason = skipped.get('reason') or 'unknown'
                            overlaps_volume = any(_starts_with_path(destination, vol_dest) for vol_dest in volume_destinations if vol_dest)
                            is_critical_destination = any(_starts_with_path(destination, critical) for critical in critical_data_prefixes)
                            if overlaps_volume or is_critical_destination or reason in ('placeholder_path', 'missing_source'):
                                risky_skips.append({
                                    'source': skipped.get('source', ''),
                                    'destination': destination,
                                    'reason': reason,
                                    'overlaps_volume': overlaps_volume,
                                })
    
                        if risky_skips:
                            preview = ', '.join(
                                f"{item.get('source') or '<unknown>'}->{item.get('destination') or '<unknown>'} ({item.get('reason')})"
                                for item in risky_skips[:4]
                            )
                            error_msg = (
                                "Stateful data migration blocked: one or more critical bind mounts were skipped or unresolved. "
                                f"Refusing to continue to avoid false-success empty container. Affected mounts: {preview}"
                            )
                            app.logger.error(error_msg)
                            update_progress('failed', 0, error_msg)
                            return {'error': error_msg, 'data_migration': migration_summary}, 400
    
                        if skipped_mounts:
                            app.logger.warning(
                                f"Data migration skipped {len(skipped_mounts)} non-critical mount(s) for {container_name}: {skipped_mounts}"
                            )
                            update_progress(
                                'migrating_data',
                                89,
                                f"Skipped {len(skipped_mounts)} non-critical mount(s); continuing migration."
                            )
                    except Exception as e:
                        error_msg = f"Data migration failed: {str(e)}"
                        app.logger.error(error_msg, exc_info=True)
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 500
                
                # Step 5: Pre-flight checks before creating container
                update_progress('validating', 89, 'Validating target server compatibility...')
                check_cancel()
                
                try:
                    # Check CPU architecture compatibility
                    source_arch = self._get_server_architecture(source_server if source_server_id != 'local' else None)
                    target_arch = self._get_server_architecture(target_server)
                    
                    app.logger.info(f"Source server architecture: {source_arch}, Target server architecture: {target_arch}")
                    
                    # Check if architectures differ
                    if source_arch != target_arch:
                        app.logger.warning(f"Architecture mismatch: source={source_arch}, target={target_arch}")
                        # Will add --platform flag to docker run command
                    
                    # Check port availability on target server
                    port_conflicts = self._check_port_availability(target_server, container_config.get('port_mapping', {}))
                    if port_conflicts:
                        conflict_ports = ', '.join(port_conflicts)
                        error_msg = f"Port(s) already in use on target server: {conflict_ports}. Please stop the containers using these ports or choose different ports."
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 400
                    
                except Exception as e:
                    app.logger.warning(f"Pre-flight checks failed (continuing anyway): {e}")
                    # Don't fail migration on pre-flight check errors, but log them
                
                # Step 6: Create and run container on target server
                update_progress('creating', 90, 'Creating and starting container on target server...')
                check_cancel()
                
                # Use export_image_tag if available (the one we just loaded), otherwise fallback to original image_tag
                target_image_tag = export_image_tag if export_image_tag else image_tag
                
                # Final verification: Check if image exists on target before creating container
                app.logger.info(f"Final verification: Checking if image {target_image_tag} exists on target server...")
                try:
                    if target_server_id == 'local':
                        # Local server - use Docker client
                        pilot = get_dockerpilot()
                        try:
                            pilot.client.images.get(target_image_tag)
                            app.logger.info(f"Image {target_image_tag} verified on local server")
                        except Exception as e:
                            # Try to find image by repository name
                            repo_name = target_image_tag.split(':')[0]
                            images = pilot.client.images.list(name=repo_name)
                            if images:
                                # Use first matching image
                                found_tag = images[0].tags[0] if images[0].tags else images[0].id
                                app.logger.warning(f"Image {target_image_tag} not found, but found {found_tag}. Using it instead.")
                                target_image_tag = found_tag
                            else:
                                raise Exception(f"Image {target_image_tag} not found on local server: {str(e)}")
                    else:
                        # Remote server - use docker images command
                        images_output = execute_docker_command_via_ssh(
                            target_server,
                            f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
                        )
                        image_found = False
                        for line in images_output.split('\n'):
                            line = line.strip()
                            if line == target_image_tag:
                                image_found = True
                                app.logger.info(f"Image {target_image_tag} verified on remote server")
                                break
                        
                        if not image_found:
                            # Try to find by repository name
                            repo_name = target_image_tag.split(':')[0]
                            for line in images_output.split('\n'):
                                line = line.strip()
                                if line.startswith(repo_name + ':'):
                                    app.logger.warning(f"Image {target_image_tag} not found, but found {line}. Using it instead.")
                                    target_image_tag = line
                                    image_found = True
                                    break
                            
                            if not image_found:
                                raise Exception(f"Image {target_image_tag} not found on target server. Available images: {images_output[:500]}")
                except Exception as e:
                    error_msg = f"Image verification failed before container creation: {str(e)}"
                    app.logger.error(error_msg)
                    update_progress('failed', 0, error_msg)
                    return {'error': error_msg}, 500
                
                app.logger.info(f"Creating container on target server using image tag: {target_image_tag}...")
                try:
                    
                    # Validate architecture compatibility and get platform flag
                    # This function detects both target server and image platform, and determines
                    # if --platform flag is needed for cross-architecture execution
                    arch_validation = self._validate_architecture_compatibility(
                        target_server,
                        target_image_tag
                    )
                    
                    # Get source server architecture for logging
                    source_arch = self._get_server_architecture(source_server if source_server_id != 'local' else None)
                    
                    # Use platform flag from validation (this is the image's platform)
                    # This ensures Docker uses the correct architecture or attempts emulation
                    # ALWAYS prefer image platform over target server architecture
                    image_platform = arch_validation.get('image_platform')
                    platform_flag = arch_validation.get('platform_flag')
                    target_server_arch = arch_validation.get('target_arch')
                    
                    app.logger.info(
                        f"Architecture validation results: "
                        f"target_server={target_server_arch}, "
                        f"image_platform={image_platform}, "
                        f"platform_flag={platform_flag}, "
                        f"compatible={arch_validation.get('compatible')}, "
                        f"needs_emulation={arch_validation.get('needs_emulation')}"
                    )
                    
                    # Use image platform if available, otherwise use target arch
                    # This is critical: we MUST use image's platform, not target server's
                    target_arch_for_run = image_platform if image_platform else (platform_flag if platform_flag else target_server_arch)
                    
                    # If we still don't have a platform, try to detect it directly
                    if not target_arch_for_run:
                        app.logger.warning("Could not determine platform from validation, attempting direct detection...")
                        detected_platform = self._get_image_platform(target_server, target_image_tag)
                        if detected_platform:
                            target_arch_for_run = detected_platform
                            app.logger.info(f"Directly detected image platform: {detected_platform}")
                        else:
                            # Last resort: try to get platform from docker inspect on remote server
                            app.logger.warning("Direct detection failed, trying docker inspect on remote server...")
                            try:
                                if target_server_id != 'local':
                                    # Try to get ImageManifestDescriptor.platform from docker inspect JSON
                                    import json
                                    inspect_output = execute_docker_command_via_ssh(
                                        target_server,
                                        f"inspect {target_image_tag}",
                                        check_exit_status=False
                                    )
                                    if inspect_output:
                                        inspect_data = json.loads(inspect_output)
                                        if isinstance(inspect_data, list) and len(inspect_data) > 0:
                                            manifest_descriptor = inspect_data[0].get('ImageManifestDescriptor', {})
                                            if manifest_descriptor:
                                                platform_info = manifest_descriptor.get('platform', {})
                                                if platform_info:
                                                    arch = platform_info.get('architecture', '').lower()
                                                    os_type = platform_info.get('os', 'linux').lower()
                                                    variant = platform_info.get('variant', '').lower()
                                                    
                                                    if variant:
                                                        target_arch_for_run = f'{os_type}/{arch}/{variant}'
                                                    else:
                                                        target_arch_for_run = f'{os_type}/{arch}'
                                                    
                                                    app.logger.info(f"Detected platform from ImageManifestDescriptor on remote: {target_arch_for_run}")
                            except Exception as e:
                                app.logger.warning(f"Failed to get platform from remote docker inspect: {e}")
                            
                            # Final fallback: use target server architecture (but this is wrong for cross-arch!)
                            if not target_arch_for_run:
                                target_arch_for_run = target_server_arch
                                app.logger.error(
                                    f"⚠️  CRITICAL: Could not detect image platform! "
                                    f"Using target server architecture as fallback: {target_arch_for_run}. "
                                    f"This may cause 'exec format error' if image architecture differs!"
                                )
                    
                    # Final check: if image platform is different from target server, we MUST use image platform
                    if image_platform and target_server_arch and image_platform != target_server_arch:
                        if target_arch_for_run != image_platform:
                            app.logger.warning(
                                f"⚠️  Platform mismatch detected! "
                                f"Image platform ({image_platform}) differs from target server ({target_server_arch}). "
                                f"Overriding target_arch_for_run to use image platform: {image_platform}"
                            )
                            target_arch_for_run = image_platform
                    
                    app.logger.info(f"Final target_arch_for_run: {target_arch_for_run} (will be used for --platform flag)")
                    
                    # Check if migration is possible
                    migration_possible = arch_validation.get('migration_possible', True)
                    app.logger.info(
                        f"🔍 Migration possibility check: "
                        f"migration_possible={migration_possible}, "
                        f"compatible={arch_validation.get('compatible')}, "
                        f"needs_emulation={arch_validation.get('needs_emulation')}, "
                        f"emulation_supported={arch_validation.get('emulation_supported')}"
                    )
                    
                    if not migration_possible:
                        error_msg = (
                            f"Cannot migrate container: Image architecture ({arch_validation.get('image_platform')}) "
                            f"does not match target server ({arch_validation.get('target_arch')}), "
                            f"and emulation is not available. {arch_validation.get('emulation_message', '')} "
                            f"To enable emulation on Raspberry Pi, install: "
                            f"sudo apt-get update && sudo apt-get install -y qemu-user-static binfmt-support"
                        )
                        app.logger.error(f"❌ BLOCKING MIGRATION: {error_msg}")
                        update_progress('failed', 0, error_msg)
                        
                        # Explicit error payload for frontend to display a hard-blocking message
                        return {
                            'success': False,
                            'error': error_msg,
                            'code': 'EMULATION_UNAVAILABLE',
                            'details': {
                                'image_platform': arch_validation.get('image_platform'),
                                'target_arch': arch_validation.get('target_arch'),
                                'needs_emulation': arch_validation.get('needs_emulation'),
                                'emulation_supported': arch_validation.get('emulation_supported'),
                                'emulation_message': arch_validation.get('emulation_message')
                            }
                        }, 400
                    
                    # Extra hard guard: if image != target arch and emulation not supported, abort (defensive)
                    if arch_validation.get('needs_emulation') and not arch_validation.get('emulation_supported'):
                        error_msg = (
                            f"Architecture mismatch (image={arch_validation.get('image_platform')}, "
                            f"target={arch_validation.get('target_arch')}) and emulation not supported. "
                            f"Migration aborted to avoid exec format error."
                        )
                        app.logger.error(f"❌ HARD BLOCK: {error_msg}")
                        update_progress('failed', 0, error_msg)
                        return {
                            'success': False,
                            'error': error_msg,
                            'code': 'EMULATION_UNAVAILABLE',
                            'details': {
                                'image_platform': arch_validation.get('image_platform'),
                                'target_arch': arch_validation.get('target_arch'),
                                'needs_emulation': arch_validation.get('needs_emulation'),
                                'emulation_supported': arch_validation.get('emulation_supported'),
                                'emulation_message': arch_validation.get('emulation_message')
                            }
                        }, 400
                    
                    if arch_validation.get('needs_emulation'):
                        if arch_validation.get('emulation_supported'):
                            app.logger.warning(
                                f"⚠️  Cross-architecture execution detected. "
                                f"Image ({arch_validation.get('image_platform')}) will run on "
                                f"target server ({arch_validation.get('target_arch')}) with emulation. "
                                f"{arch_validation.get('emulation_message', '')}"
                            )
                        else:
                            # This should not happen if migration_possible check above works, but just in case
                            error_msg = (
                                f"Cannot migrate: Emulation required but not available. "
                                f"{arch_validation.get('emulation_message', '')}"
                            )
                            app.logger.error(f"❌ {error_msg}")
                            update_progress('failed', 0, error_msg)
                            return {'error': error_msg}, 400
                    
                    # Log container config before building docker run command
                    app.logger.info(f"Container config for docker run: image={target_image_tag}, ports={container_config.get('port_mapping', {})}, env={len(container_config.get('environment', {}))} vars, volumes={len(container_config.get('volumes', {}))} mounts")
                    
                    # Final safety check: ensure target_arch_for_run is set
                    if not target_arch_for_run:
                        app.logger.error(
                            f"❌ CRITICAL: target_arch_for_run is None! "
                            f"This will cause 'exec format error'. "
                            f"Attempting emergency platform detection..."
                        )
                        # Emergency fallback: try to get platform from ImageManifestDescriptor
                        try:
                            if target_server_id == 'local':
                                pilot = get_dockerpilot()
                                image = pilot.client.images.get(target_image_tag)
                                manifest = image.attrs.get('ImageManifestDescriptor', {})
                                if manifest:
                                    platform_info = manifest.get('platform', {})
                                    if platform_info:
                                        arch = platform_info.get('architecture', '').lower()
                                        os_type = platform_info.get('os', 'linux').lower()
                                        target_arch_for_run = f'{os_type}/{arch}'
                                        app.logger.info(f"Emergency detection: {target_arch_for_run}")
                            else:
                                # Remote server - use docker inspect
                                import json
                                inspect_output = execute_docker_command_via_ssh(
                                    target_server,
                                    f"inspect {target_image_tag}",
                                    check_exit_status=False
                                )
                                if inspect_output:
                                    inspect_data = json.loads(inspect_output)
                                    if isinstance(inspect_data, list) and len(inspect_data) > 0:
                                        manifest = inspect_data[0].get('ImageManifestDescriptor', {})
                                        if manifest:
                                            platform_info = manifest.get('platform', {})
                                            if platform_info:
                                                arch = platform_info.get('architecture', '').lower()
                                                os_type = platform_info.get('os', 'linux').lower()
                                                target_arch_for_run = f'{os_type}/{arch}'
                                                app.logger.info(f"Emergency detection (remote): {target_arch_for_run}")
                        except Exception as e:
                            app.logger.error(f"Emergency platform detection failed: {e}")
                    
                    # Build docker run command from config
                    # Use platform_flag (image platform) for target_arch parameter
                    docker_run_cmd = self._build_docker_run_command(
                        container_config, 
                        container_name, 
                        target_image_tag,
                        target_arch=target_arch_for_run,
                        source_arch=source_arch
                    )
                    
                    # Verify that --platform flag is in the command
                    if target_arch_for_run and f'--platform {target_arch_for_run}' not in docker_run_cmd:
                        app.logger.error(
                            f"❌ CRITICAL: --platform flag is missing from docker run command! "
                            f"target_arch_for_run={target_arch_for_run}, "
                            f"command={docker_run_cmd[:200]}..."
                        )
                        # Force add the platform flag
                        # Find where 'run' is and add --platform after it
                        parts = docker_run_cmd.split()
                        if 'run' in parts:
                            run_idx = parts.index('run')
                            parts.insert(run_idx + 1, '--platform')
                            parts.insert(run_idx + 2, target_arch_for_run)
                            docker_run_cmd = ' '.join(parts)
                            app.logger.info(f"Fixed command: docker {docker_run_cmd[:200]}...")
                    
                    app.logger.info(f"Final docker run command: docker {docker_run_cmd}")
                    
                    # Final check: Verify image exists right before creating container
                    app.logger.info(f"Final check: Verifying image {target_image_tag} exists on target server before docker run...")
                    try:
                        if target_server_id == 'local':
                            pilot = get_dockerpilot()
                            pilot.client.images.get(target_image_tag)
                            app.logger.info(f"Image {target_image_tag} confirmed on local server")
                        else:
                            # Quick check on remote server
                            repo_name = target_image_tag.split(':')[0]
                            grep_pattern = f'^{target_image_tag}$|^{repo_name}:'
                            images_check = execute_docker_command_via_ssh(
                                target_server,
                                f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep -E '{grep_pattern}'"
                            )
                            if target_image_tag not in images_check:
                                # Try to find the image by repository
                                all_images = execute_docker_command_via_ssh(
                                    target_server,
                                    f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
                                )
                                app.logger.warning(f"Image {target_image_tag} not found in final check. Available images: {all_images[:500]}")
                                
                                # Also get images with IDs to find untagged images
                                images_with_ids = execute_docker_command_via_ssh(
                                    target_server,
                                    "images --format '{{.ID}} {{.Repository}}:{{.Tag}}' --no-trunc"
                                )
                                app.logger.info(f"All images with IDs: {images_with_ids[:1000]}")
                                
                                # Try to retag if we can find the repository
                                repo_name = target_image_tag.split(':')[0]
                                image_found_for_retag = False
                                
                                # First, try to find by repository name
                                for line in all_images.split('\n'):
                                    line = line.strip()
                                    if line and line.startswith(repo_name + ':'):
                                        app.logger.info(f"Retagging {line} to {target_image_tag}...")
                                        execute_docker_command_via_ssh(
                                            target_server,
                                            f"tag {line} {target_image_tag}"
                                        )
                                        image_found_for_retag = True
                                        break
                                
                                # If not found, try to find untagged image (might be the one we just loaded)
                                if not image_found_for_retag:
                                    for line in images_with_ids.split('\n'):
                                        line = line.strip()
                                        if not line:
                                            continue
                                        parts = line.split(' ', 1)
                                        if len(parts) == 2:
                                            img_id = parts[0]
                                            img_tag = parts[1]
                                            # Check if it's an untagged image or matches our repository
                                            if img_tag == '<none>:<none>' or (repo_name in img_tag and img_tag != '<none>:<none>'):
                                                app.logger.info(f"Found potential image ID {img_id[:12]} with tag {img_tag}, retagging to {target_image_tag}...")
                                                try:
                                                    execute_docker_command_via_ssh(
                                                        target_server,
                                                        f"tag {img_id} {target_image_tag}"
                                                    )
                                                    # Verify the tag was created
                                                    verify_output = execute_docker_command_via_ssh(
                                                        target_server,
                                                        f"images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep '^{target_image_tag}$'"
                                                    )
                                                    if target_image_tag in verify_output:
                                                        app.logger.info(f"Successfully retagged image as {target_image_tag}")
                                                        image_found_for_retag = True
                                                        break
                                                except Exception as retag_error:
                                                    app.logger.warning(f"Failed to retag image {img_id[:12]}: {retag_error}")
                                                    continue
                                
                                if not image_found_for_retag:
                                    raise Exception(f"Could not find or retag image {target_image_tag} on target server. Available images: {all_images[:500]}")
                            else:
                                app.logger.info(f"Image {target_image_tag} confirmed on remote server")
                    except Exception as verify_error:
                        app.logger.error(f"Final image verification failed: {verify_error}")
                        # Don't fail here - try to create container anyway, but log the error
                    
                    # Execute on target server
                    if target_server_id == 'local':
                        # Local server - use subprocess directly (subprocess is already imported at top)
                        full_command = f"docker {docker_run_cmd}"
                        app.logger.info(f"🔧 EXECUTING LOCAL COMMAND: {full_command}")
                        result = subprocess.run(
                            full_command,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        app.logger.info(f"Command exit code: {result.returncode}")
                        if result.stdout:
                            app.logger.info(f"Command stdout: {result.stdout}")
                        if result.stderr:
                            app.logger.warning(f"Command stderr: {result.stderr}")
                        if result.returncode != 0:
                            raise Exception(f"Command failed (exit {result.returncode}): {result.stderr}")
                    else:
                        # Remote server - use SSH
                        full_command = f"docker {docker_run_cmd}"
                        app.logger.info(f"🔧 EXECUTING REMOTE COMMAND on {target_server.get('hostname')}: {full_command}")
                        try:
                            output = execute_docker_command_via_ssh(target_server, docker_run_cmd)
                            app.logger.info(f"Remote command output: {output}")
                        except Exception as ssh_error:
                            app.logger.error(f"Remote command failed: {ssh_error}", exc_info=True)
                            raise
                    
                    update_progress('completed', 100, f'Migration completed successfully! Container {container_name} is running on target server.')
                    
                except Exception as e:
                    check_cancel()  # Check if it was cancelled
                    error_msg = str(e)
                    
                    # Provide helpful error messages for common issues
                    if 'platform' in error_msg.lower() and ('does not match' in error_msg.lower() or 'no specific platform' in error_msg.lower()):
                        error_msg = f"Architecture mismatch detected. The image platform ({source_arch if source_arch else 'unknown'}) does not match target server ({target_arch}). " \
                                   f"Try using a multi-arch image or specify --platform flag. Original error: {error_msg}"
                    elif 'port is already allocated' in error_msg.lower() or 'bind' in error_msg.lower() and 'failed' in error_msg.lower():
                        error_msg = f"Port conflict detected. One or more ports are already in use on the target server. " \
                                   f"Please stop the containers using these ports or modify port mappings. Original error: {error_msg}"
                    
                    update_progress('failed', 0, f'Error creating container: {error_msg}')
                    return {'error': f'Failed to create container on target: {error_msg}'}, 500
                
                # Step 7: Optionally stop source container
                if stop_source:
                    update_progress('stopping_source', 95, 'Stopping source container...')
                    check_cancel()
                    try:
                        if source_server_id == 'local':
                            pilot.client.containers.get(container_name).stop()
                        else:
                            execute_docker_command_via_ssh(source_server, f"stop {container_name}")
                    except Exception as e:
                        app.logger.warning(f"Failed to stop source container: {e}")
                
                # Clean up progress after success
                if container_name in _migration_progress:
                    # Keep progress for a short time to show success message
                    import threading
                    def cleanup_progress():
                        import time
                        time.sleep(5)  # Keep for 5 seconds
                        if container_name in _migration_progress:
                            del _migration_progress[container_name]
                        if container_name in _migration_cancel_flags:
                            del _migration_cancel_flags[container_name]
                    threading.Thread(target=cleanup_progress, daemon=True).start()
                
                return {
                    'success': True,
                    'message': f'Container {container_name} migrated successfully from {source_server_id} to {target_server_id}',
                    'container_name': container_name,
                    'source_server': source_server_id,
                    'target_server': target_server_id,
                    'data_migration': migration_summary if include_data else None,
                }
                
            except Exception as e:
                error_msg = str(e)
                app.logger.error(f"Migration failed: {error_msg}", exc_info=True)
                
                # Update progress with error (only if container_name is defined)
                if container_name and container_name in _migration_progress:
                    if 'cancelled' in error_msg.lower():
                        _migration_progress[container_name] = {
                            'stage': 'cancelled',
                            'progress': _migration_progress[container_name].get('progress', 0),
                            'message': 'Migration was cancelled',
                            'timestamp': datetime.now().isoformat()
                        }
                    else:
                        _migration_progress[container_name] = {
                            'stage': 'failed',
                            'progress': _migration_progress[container_name].get('progress', 0),
                            'message': f'Migration error: {error_msg}',
                            'timestamp': datetime.now().isoformat()
                        }
                
                # Ensure we always return a proper error response
                return {'error': error_msg}, 500
        
        def _extract_container_config(self, container):
            """Extract container configuration from Docker container object"""
            attrs = container.attrs
            host_config = attrs.get('HostConfig', {})  # used early for PortBindings fallback
            network_mode = host_config.get('NetworkMode', 'bridge') if isinstance(host_config, dict) else 'bridge'
            if network_mode == 'default':
                network_mode = 'bridge'
            
            config = {
                'image_tag': attrs.get('Config', {}).get('Image', ''),
                'port_mapping': {},
                'environment': {},
                'volumes': {},
                'mounts': [],
                'restart_policy': 'no',
                'network': 'bridge',
                'cpu_limit': None,
                'memory_limit': None,
                'privileged': False,
                'command': None,
                'entrypoint': None,
                'skipped_bind_mounts': []
            }
            
            # Extract command and entrypoint
            container_config = attrs.get('Config', {})
            if container_config.get('Cmd'):
                config['command'] = container_config['Cmd']
            if container_config.get('Entrypoint'):
                config['entrypoint'] = container_config['Entrypoint']
            
            # Extract ports
            if 'NetworkSettings' in attrs:
                ports = attrs['NetworkSettings'].get('Ports', {})
                app.logger.debug(f"Extracting ports from NetworkSettings.Ports: {ports}")
                for container_port, host_bindings in ports.items():
                    if host_bindings:
                        port_num = container_port.split('/')[0]
                        host_port = host_bindings[0].get('HostPort', '')
                        if host_port:
                            config['port_mapping'][port_num] = host_port
                            app.logger.debug(f"Extracted port mapping: {port_num} -> {host_port}")
                
                # NOTE:
                # Do not auto-map Config.ExposedPorts to host ports.
                # Exposed port means "container can listen", not "publish on host".
                # Auto-mapping (e.g. 22 -> 22) creates false conflicts during migrations.
                # Fallback: HostConfig.PortBindings (e.g. when NetworkSettings.Ports is empty or container uses host network)
                if not config['port_mapping'] and attrs.get('HostConfig', {}).get('PortBindings'):
                    for key, bindings in attrs['HostConfig']['PortBindings'].items():
                        if bindings and isinstance(bindings, list):
                            host_port = bindings[0].get('HostPort', '') if isinstance(bindings[0], dict) else ''
                            if host_port:
                                port_num = key.split('/')[0]
                                config['port_mapping'][port_num] = host_port
                                app.logger.debug(f"Extracted port from PortBindings: {port_num} -> {host_port}")
                
                if not config['port_mapping'] and network_mode == 'host':
                    inferred = _infer_port_mapping_for_host_network(attrs, config.get('image_tag', ''))
                    if inferred:
                        config['port_mapping'].update(inferred)
                        app.logger.info(
                            f"Inferred host-network port mapping for migration: {config['port_mapping']}"
                        )
    
                app.logger.info(f"Final port_mapping: {config['port_mapping']}")
            
            # Extract environment
            env_list = attrs.get('Config', {}).get('Env', [])
            for env_var in env_list:
                if '=' in env_var:
                    key, value = env_var.split('=', 1)
                    config['environment'][key] = value
            
            # Extract volumes and mount metadata (both bind mounts and named volumes)
            mounts = attrs.get('Mounts', [])
            for mount in mounts:
                mount_type = mount.get('Type', '')
                source = mount.get('Source', '')
                destination = mount.get('Destination', '')
                volume_name = mount.get('Name', '')
                if not destination:
                    continue
                config['mounts'].append({
                    'type': mount_type,
                    'source': source,
                    'destination': destination,
                    'name': volume_name,
                    'mode': mount.get('Mode', ''),
                })
                if mount_type == 'bind':
                    if source:
                        config['volumes'][source] = destination
                    continue
                if mount_type == 'volume':
                    volume_source = volume_name or source
                    if volume_source:
                        config['volumes'][volume_source] = destination
            
            # Extract restart policy
            restart_policy_config = host_config.get('RestartPolicy', {})
            if restart_policy_config:
                config['restart_policy'] = restart_policy_config.get('Name', 'no')
            
            # Extract network
            config['network'] = network_mode
            
            # Extract resource limits
            if 'NanoCpus' in host_config:
                try:
                    nano_cpus = int(host_config.get('NanoCpus', 0) or 0)
                    if nano_cpus > 0:
                        config['cpu_limit'] = str(nano_cpus / 1000000000)
                except Exception:
                    pass
            if 'Memory' in host_config and host_config['Memory'] > 0:
                memory_mb = host_config['Memory'] / (1024 * 1024)
                if memory_mb >= 1024:
                    config['memory_limit'] = f"{int(memory_mb / 1024)}Gi"
                else:
                    config['memory_limit'] = f"{int(memory_mb)}Mi"
            
            # Privileged flag
            config['privileged'] = host_config.get('Privileged', False)
            
            return config
        
        def _extract_container_config_from_inspect(self, attrs):
            """Extract container configuration from docker inspect JSON"""
            config = {
                'image_tag': attrs.get('Config', {}).get('Image', ''),
                'port_mapping': {},
                'environment': {},
                'volumes': {},
                'mounts': [],
                'restart_policy': 'no',
                'network': 'bridge',
                'cpu_limit': None,
                'memory_limit': None,
                'privileged': False,
                'command': None,
                'entrypoint': None,
                'skipped_bind_mounts': []
            }
            host_config = attrs.get('HostConfig', {})
            network_mode = host_config.get('NetworkMode', 'bridge') if isinstance(host_config, dict) else 'bridge'
            if network_mode == 'default':
                network_mode = 'bridge'
            
            # Extract command and entrypoint
            container_config = attrs.get('Config', {})
            if container_config.get('Cmd'):
                config['command'] = container_config['Cmd']
            if container_config.get('Entrypoint'):
                config['entrypoint'] = container_config['Entrypoint']
            
            # Extract ports
            if 'NetworkSettings' in attrs:
                ports = attrs['NetworkSettings'].get('Ports', {})
                app.logger.debug(f"Extracting ports from NetworkSettings.Ports: {ports}")
                for container_port, host_bindings in ports.items():
                    if host_bindings:
                        port_num = container_port.split('/')[0]
                        host_port = host_bindings[0].get('HostPort', '')
                        if host_port:
                            config['port_mapping'][port_num] = host_port
                            app.logger.debug(f"Extracted port mapping: {port_num} -> {host_port}")
                
                # NOTE:
                # Do not auto-map Config.ExposedPorts to host ports.
                # Exposed port means "container can listen", not "publish on host".
                # Auto-mapping (e.g. 22 -> 22) creates false conflicts during migrations.
                # Fallback: HostConfig.PortBindings (e.g. when NetworkSettings.Ports is empty or container uses host network)
                if not config['port_mapping'] and attrs.get('HostConfig', {}).get('PortBindings'):
                    for key, bindings in attrs['HostConfig']['PortBindings'].items():
                        if bindings and isinstance(bindings, list):
                            host_port = bindings[0].get('HostPort', '') if isinstance(bindings[0], dict) else ''
                            if host_port:
                                port_num = key.split('/')[0]
                                config['port_mapping'][port_num] = host_port
                                app.logger.debug(f"Extracted port from PortBindings: {port_num} -> {host_port}")
                
                if not config['port_mapping'] and network_mode == 'host':
                    inferred = _infer_port_mapping_for_host_network(attrs, config.get('image_tag', ''))
                    if inferred:
                        config['port_mapping'].update(inferred)
                        app.logger.info(
                            f"Inferred host-network port mapping from inspect: {config['port_mapping']}"
                        )
    
                app.logger.info(f"Final port_mapping: {config['port_mapping']}")
            
            # Extract environment
            env_list = attrs.get('Config', {}).get('Env', [])
            for env_var in env_list:
                if '=' in env_var:
                    key, value = env_var.split('=', 1)
                    config['environment'][key] = value
            
            # Extract volumes and mount metadata (both bind mounts and named volumes)
            mounts = attrs.get('Mounts', [])
            for mount in mounts:
                mount_type = mount.get('Type', '')
                source = mount.get('Source', '')
                destination = mount.get('Destination', '')
                volume_name = mount.get('Name', '')
                if not destination:
                    continue
                config['mounts'].append({
                    'type': mount_type,
                    'source': source,
                    'destination': destination,
                    'name': volume_name,
                    'mode': mount.get('Mode', ''),
                })
                if mount_type == 'bind':
                    if source:
                        config['volumes'][source] = destination
                    continue
                if mount_type == 'volume':
                    volume_source = volume_name or source
                    if volume_source:
                        config['volumes'][volume_source] = destination
            
            # Extract restart policy
            restart_policy_config = host_config.get('RestartPolicy', {})
            if restart_policy_config:
                config['restart_policy'] = restart_policy_config.get('Name', 'no')
            
            # Extract network
            config['network'] = network_mode
            
            # Extract resource limits
            if 'NanoCpus' in host_config:
                try:
                    nano_cpus = int(host_config.get('NanoCpus', 0) or 0)
                    if nano_cpus > 0:
                        config['cpu_limit'] = str(nano_cpus / 1000000000)
                except Exception:
                    pass
            if 'Memory' in host_config and host_config['Memory'] > 0:
                memory_mb = host_config['Memory'] / (1024 * 1024)
                if memory_mb >= 1024:
                    config['memory_limit'] = f"{int(memory_mb / 1024)}Gi"
                else:
                    config['memory_limit'] = f"{int(memory_mb)}Mi"
            
            # Privileged flag
            config['privileged'] = host_config.get('Privileged', False)
            
            return config
    
        def _open_ssh_client_for_transfer(self, server_config):
            """Open SSH client for SFTP transfers."""
            import paramiko
            from io import StringIO
    
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
            hostname = server_config.get('hostname')
            port = server_config.get('port', 22)
            username = server_config.get('username')
            auth_type = server_config.get('auth_type', 'password')
    
            if auth_type == 'password':
                ssh.connect(
                    hostname,
                    port=port,
                    username=username,
                    password=server_config.get('password'),
                    timeout=30,
                )
            elif auth_type == 'key':
                key_content = server_config.get('private_key')
                if not key_content:
                    raise ValueError('Private key required for key authentication')
                key_passphrase = server_config.get('key_passphrase')
                key_file = StringIO(key_content)
                try:
                    key = paramiko.RSAKey.from_private_key(
                        key_file, password=key_passphrase if key_passphrase else None
                    )
                except Exception:
                    key_file.seek(0)
                    try:
                        key = paramiko.DSSKey.from_private_key(
                            key_file, password=key_passphrase if key_passphrase else None
                        )
                    except Exception:
                        key_file.seek(0)
                        key = paramiko.ECDSAKey.from_private_key(
                            key_file, password=key_passphrase if key_passphrase else None
                        )
                ssh.connect(hostname, port=port, username=username, pkey=key, timeout=30)
            elif auth_type == '2fa':
                password = server_config.get('password', '')
                totp_code = server_config.get('totp_code', '')
                ssh.connect(
                    hostname,
                    port=port,
                    username=username,
                    password=password + totp_code,
                    timeout=30,
                )
            else:
                raise ValueError(f"Unsupported auth_type: {auth_type}")
    
            return ssh
    
        def _download_file_from_server(self, server_config, remote_path: str, local_path: str, progress_callback=None):
            """Download file from remote server via SFTP."""
            ssh = self._open_ssh_client_for_transfer(server_config)
            try:
                sftp = ssh.open_sftp()
                try:
                    sftp.get(remote_path, local_path, callback=progress_callback)
                finally:
                    sftp.close()
            finally:
                ssh.close()
    
        def _upload_file_to_server(self, server_config, local_path: str, remote_path: str, progress_callback=None):
            """Upload file to remote server via SFTP."""
            ssh = self._open_ssh_client_for_transfer(server_config)
            try:
                sftp = ssh.open_sftp()
                try:
                    sftp.put(local_path, remote_path, callback=progress_callback)
                finally:
                    sftp.close()
            finally:
                ssh.close()
    
        def _migrate_mount_data_between_servers(
            self,
            container_name: str,
            container_config: dict,
            source_server,
            target_server,
            check_cancel,
            update_progress,
        ):
            """Migrate mount data (bind mounts + named volumes) from source to target server."""
            import shlex
            import tempfile
            import time
    
            summary = {
                'migrated': [],
                'skipped': [],
                'total_migrated': 0,
                'total_skipped': 0,
                'total_transferable': 0,
            }
    
            def record_skip(mount: dict, reason: str):
                summary['skipped'].append({
                    'type': (mount.get('type') or '').strip(),
                    'source': (mount.get('source') or '').strip(),
                    'name': (mount.get('name') or '').strip(),
                    'destination': (mount.get('destination') or '').strip(),
                    'reason': reason,
                })
    
            mounts = container_config.get('mounts') or []
            if not mounts:
                # Fallback for older configs without mount metadata.
                mounts = []
                for source, destination in (container_config.get('volumes') or {}).items():
                    mount_type = 'bind' if str(source).startswith('/') else 'volume'
                    mounts.append(
                        {
                            'type': mount_type,
                            'source': source,
                            'destination': destination,
                            'name': source if mount_type == 'volume' else '',
                        }
                    )
    
            if not mounts:
                app.logger.info("No mounts detected for data migration")
                return summary
    
            # Skip dangerous/system bind mounts.
            bind_skip_prefixes = (
                '/proc',
                '/sys',
                '/dev',
                '/run',
                '/var/run',
                '/etc/hosts',
                '/etc/hostname',
                '/etc/resolv.conf',
                '/etc/localtime',
                '/etc/timezone',
                '/var/run/docker.sock',
            )
    
            transferable = []
            for mount in mounts:
                mount_type = (mount.get('type') or '').strip()
                source = (mount.get('source') or '').strip()
                name = (mount.get('name') or '').strip()
                destination = (mount.get('destination') or '').strip()
                if not destination:
                    record_skip(mount, 'missing_destination')
                    continue
                if mount_type == 'bind':
                    if not source:
                        record_skip(mount, 'missing_source')
                        continue
                    if source == '/':
                        app.logger.info(f"Skipping root bind mount during data migration: {source} -> {destination}")
                        container_config.setdefault('skipped_bind_mounts', []).append(source)
                        record_skip(mount, 'root_path')
                        continue
                    if any(source.startswith(prefix) for prefix in bind_skip_prefixes):
                        app.logger.info(f"Skipping system bind mount during data migration: {source} -> {destination}")
                        container_config.setdefault('skipped_bind_mounts', []).append(source)
                        record_skip(mount, 'system_path')
                        continue
                    transferable.append(mount)
                elif mount_type == 'volume':
                    if name or source:
                        transferable.append(mount)
                    else:
                        record_skip(mount, 'missing_volume_name')
                else:
                    record_skip(mount, f"unsupported_type:{mount_type or 'unknown'}")
    
            if not transferable:
                app.logger.info("No transferable mounts for data migration")
                summary['total_skipped'] = len(summary['skipped'])
                return summary
    
            total = len(transferable)
            summary['total_transferable'] = total
            app.logger.info(f"Migrating data for {total} mount(s) of container {container_name}")
    
            for idx, mount in enumerate(transferable, start=1):
                check_cancel()
                mount_type = (mount.get('type') or '').strip()
                mount_source = (mount.get('source') or '').strip()
                mount_name = (mount.get('name') or '').strip()
                destination = (mount.get('destination') or '').strip()
                effective_volume = mount_name or mount_source
    
                stage_msg = f"Migrating data [{idx}/{total}] {mount_type}:{destination}"
                stage_progress = 84 + int((idx - 1) * 5 / max(total, 1))
                update_progress('migrating_data', stage_progress, stage_msg)
    
                def make_transfer_callback(step_name: str, progress_start: int, progress_span: int):
                    """Create throttled transfer callback with progress updates + cancel checks."""
                    last_bucket = {'value': -1}
    
                    def _callback(transferred: int, total_bytes: int):
                        check_cancel()
                        if total_bytes <= 0:
                            return
    
                        percent = min(100.0, (float(transferred) / float(total_bytes)) * 100.0)
                        bucket = int(percent // 5)
                        if bucket <= last_bucket['value']:
                            return
                        last_bucket['value'] = bucket
    
                        progress_value = min(
                            progress_start + int((percent / 100.0) * progress_span),
                            89,
                        )
                        transferred_mb = transferred / (1024 * 1024)
                        total_mb = total_bytes / (1024 * 1024)
                        update_progress(
                            'migrating_data',
                            progress_value,
                            f"[{idx}/{total}] {step_name}: {percent:.1f}% "
                            f"({transferred_mb:.2f} MB / {total_mb:.2f} MB)",
                        )
    
                    return _callback
    
                ts = int(time.time())
                safe_container = re.sub(r'[^a-zA-Z0-9_.-]', '_', container_name)
                local_archive = tempfile.NamedTemporaryFile(
                    delete=False, suffix=f"_{safe_container}_{idx}_{ts}.tar"
                )
                local_archive_path = local_archive.name
                local_archive.close()
    
                source_remote_archive = f"/tmp/dockerpilot_mount_{safe_container}_{idx}_{ts}.tar"
                target_remote_archive = f"/tmp/dockerpilot_mount_{safe_container}_{idx}_{ts}.tar"
                archive_size_bytes = 0
    
                try:
                    # 1) Create archive on source side
                    update_progress(
                        'migrating_data',
                        stage_progress,
                        f"[{idx}/{total}] Archiving mount data on source ({mount_type}:{destination})..."
                    )
                    if source_server is None:
                        source_archive_path = local_archive_path
                    else:
                        source_archive_path = source_remote_archive
    
                    if mount_type == 'bind':
                        src_q = shlex.quote(mount_source)
                        archive_q = shlex.quote(source_archive_path)
                        create_cmd = (
                            f"src={src_q}; archive={archive_q}; "
                            "if [ -d \"$src\" ]; then "
                            "tar -cpf \"$archive\" -C \"$src\" .; "
                            "echo '__MOUNT_KIND__:dir'; "
                            "elif [ -f \"$src\" ]; then "
                            "tar -cpf \"$archive\" -C \"$(dirname \"$src\")\" \"$(basename \"$src\")\"; "
                            "echo '__MOUNT_KIND__:file'; "
                            "else "
                            "echo \"Bind source path not found: $src\" >&2; exit 1; "
                            "fi"
                        )
                        create_output = execute_command_via_ssh(source_server, create_cmd)
                        bind_kind = 'file' if '__MOUNT_KIND__:file' in (create_output or '') else 'dir'
                    else:
                        if not effective_volume:
                            raise Exception(f"Missing volume name/source for mount {mount}")
                        vol_q = shlex.quote(effective_volume)
                        archive_q = shlex.quote(source_archive_path)
                        create_cmd = (
                            f"vol={vol_q}; archive={archive_q}; "
                            "docker run --rm "
                            "-v \"$vol\":/from:ro "
                            "alpine sh -c 'cd /from && tar -cpf - .' > \"$archive\""
                        )
                        execute_command_via_ssh(source_server, create_cmd)
    
                    # 2) Ensure archive is local (download from source if needed)
                    if source_server is not None:
                        update_progress(
                            'migrating_data',
                            min(stage_progress + 1, 88),
                            f"[{idx}/{total}] Downloading mount archive from source server..."
                        )
                        download_callback = make_transfer_callback(
                            "Downloading mount archive from source server",
                            stage_progress,
                            2,
                        )
                        self._download_file_from_server(
                            source_server,
                            source_archive_path,
                            local_archive_path,
                            progress_callback=download_callback,
                        )
                        execute_command_via_ssh(
                            source_server,
                            f"rm -f {shlex.quote(source_archive_path)}",
                            check_exit_status=False,
                        )
    
                    if os.path.exists(local_archive_path):
                        try:
                            archive_size_bytes = os.path.getsize(local_archive_path)
                        except Exception:
                            archive_size_bytes = 0
    
                    # 3) Place archive on target (upload if remote)
                    if target_server is None:
                        target_archive_path = local_archive_path
                    else:
                        update_progress(
                            'migrating_data',
                            min(stage_progress + 2, 89),
                            f"[{idx}/{total}] Uploading mount archive to target server..."
                        )
                        target_archive_path = target_remote_archive
                        upload_callback = make_transfer_callback(
                            "Uploading mount archive to target server",
                            min(stage_progress + 2, 89),
                            2,
                        )
                        self._upload_file_to_server(
                            target_server,
                            local_archive_path,
                            target_archive_path,
                            progress_callback=upload_callback,
                        )
    
                    # 4) Restore archive on target mount
                    update_progress(
                        'migrating_data',
                        min(stage_progress + 4, 89),
                        f"[{idx}/{total}] Restoring data into target mount..."
                    )
                    if mount_type == 'bind':
                        target_source = mount_source
                        if bind_kind == 'file':
                            restore_host_path = os.path.dirname(target_source) or '/'
                        else:
                            restore_host_path = target_source
                        restore_host_q = shlex.quote(restore_host_path)
                        archive_q = shlex.quote(target_archive_path)
                        # Restore bind mounts through docker to avoid direct host FS permission issues
                        # (docker daemon can create/access host path as needed).
                        restore_cmd = (
                            "run --rm -i "
                            f"-v {restore_host_q}:/to "
                            "alpine sh -c 'cd /to && tar -xpf -' "
                            f"< {archive_q}"
                        )
                        try:
                            execute_docker_command_via_ssh(target_server, restore_cmd)
                        except Exception as docker_restore_err:
                            app.logger.warning(
                                "Bind mount docker-restore failed, trying legacy restore path "
                                f"for {restore_host_path}: {docker_restore_err}"
                            )
                            target_q = shlex.quote(restore_host_path)
                            legacy_restore_cmd = (
                                f"mkdir -p {target_q} && "
                                f"tar -xpf {archive_q} -C {target_q}"
                            )
                            execute_command_via_ssh(target_server, legacy_restore_cmd)
                    else:
                        vol_q = shlex.quote(effective_volume)
                        archive_q = shlex.quote(target_archive_path)
                        restore_cmd = (
                            f"docker volume create {vol_q} >/dev/null 2>&1 || true; "
                            f"cat {archive_q} | "
                            "docker run --rm -i "
                            f"-v {vol_q}:/to "
                            "alpine sh -c 'cd /to && tar -xpf -'"
                        )
                        execute_command_via_ssh(target_server, restore_cmd)
    
                    app.logger.info(
                        f"Data migration succeeded for mount {idx}/{total}: "
                        f"type={mount_type}, source={mount_source or effective_volume}, destination={destination}"
                    )
                    summary['migrated'].append({
                        'type': mount_type,
                        'source': mount_source,
                        'name': mount_name,
                        'effective_volume': effective_volume,
                        'destination': destination,
                        'archive_size_bytes': archive_size_bytes,
                    })
                finally:
                    try:
                        if os.path.exists(local_archive_path):
                            os.unlink(local_archive_path)
                    except Exception:
                        pass
                    if target_server is not None:
                        try:
                            execute_command_via_ssh(
                                target_server,
                                f"rm -f {shlex.quote(target_remote_archive)}",
                                check_exit_status=False,
                            )
                        except Exception:
                            pass
            summary['total_migrated'] = len(summary['migrated'])
            summary['total_skipped'] = len(summary['skipped'])
            return summary
        
        def _get_server_architecture(self, server_config=None):
            """Get CPU architecture of a server (local or remote)"""
            try:
                # Check if this is local server (None or id == 'local')
                is_local = server_config is None or server_config.get('id') == 'local'
                
                if is_local:
                    # Local server
                    import platform
                    machine = platform.machine().lower()
                    if 'arm64' in machine or 'aarch64' in machine:
                        return 'linux/arm64'
                    elif 'amd64' in machine or 'x86_64' in machine:
                        return 'linux/amd64'
                    elif 'arm' in machine:
                        return 'linux/arm/v7'
                    return f'linux/{machine}'
                else:
                    # Remote server - check via SSH
                    arch_output = execute_command_via_ssh(server_config, "uname -m", check_exit_status=False)
                    if arch_output:
                        arch = arch_output.strip().lower()
                        # Normalize architecture names
                        if 'arm64' in arch or 'aarch64' in arch:
                            return 'linux/arm64'
                        elif 'amd64' in arch or 'x86_64' in arch:
                            return 'linux/amd64'
                        elif 'arm' in arch:
                            return 'linux/arm/v7'
                        return f'linux/{arch}'
            except Exception as e:
                app.logger.warning(f"Could not determine server architecture: {e}")
                return None
        
        def _get_image_platform(self, server_config, image_tag):
            """Get platform/architecture of a Docker image
            
            Tries multiple methods to detect platform:
            1. ImageManifestDescriptor.platform (most reliable for loaded images)
            2. Architecture/Os/Variant from image attributes
            3. Fallback to docker inspect with format
            
            Returns:
                str: Platform in format 'linux/amd64', 'linux/arm64', etc. or None if cannot determine
            """
            try:
                is_local = server_config is None or server_config.get('id') == 'local'
                
                if is_local:
                    # Local server - use Docker SDK
                    try:
                        from docker import get_docker_client
                        client = get_docker_client()
                        image = client.images.get(image_tag)
                        
                        # Method 1: Try ImageManifestDescriptor.platform (most reliable)
                        # This is especially important for images loaded via docker load
                        try:
                            # Get full inspect JSON
                            import json
                            inspect_json = image.attrs
                            
                            # Check ImageManifestDescriptor.platform
                            manifest_descriptor = inspect_json.get('ImageManifestDescriptor', {})
                            if manifest_descriptor:
                                platform_info = manifest_descriptor.get('platform', {})
                                if platform_info:
                                    arch = platform_info.get('architecture', '').lower()
                                    os_type = platform_info.get('os', 'linux').lower()
                                    variant = platform_info.get('variant', '').lower()
                                    
                                    # Build platform string
                                    if variant:
                                        platform = f'{os_type}/{arch}/{variant}'
                                    else:
                                        platform = f'{os_type}/{arch}'
                                    
                                    app.logger.info(f"Detected image platform from ImageManifestDescriptor: {platform}")
                                    return platform
                        except Exception as e:
                            app.logger.debug(f"Could not get platform from ImageManifestDescriptor: {e}")
                        
                        # Method 2: Get architecture from image attributes
                        arch = image.attrs.get('Architecture', '').lower()
                        os_type = image.attrs.get('Os', 'linux').lower()
                        variant = image.attrs.get('Variant', '').lower()
                        
                        if arch:
                            # Normalize to Docker platform format
                            if 'arm64' in arch or 'aarch64' in arch:
                                platform = f'{os_type}/arm64'
                            elif 'amd64' in arch or 'x86_64' in arch:
                                platform = f'{os_type}/amd64'
                            elif 'arm' in arch:
                                if variant and ('v7' in variant or 'v6' in variant):
                                    platform = f'{os_type}/arm/v7'
                                else:
                                    platform = f'{os_type}/arm64'
                            else:
                                platform = f'{os_type}/{arch}'
                            
                            app.logger.info(f"Detected image platform from attributes: {platform}")
                            return platform
                        
                        return None
                    except Exception as e:
                        app.logger.debug(f"Could not get image platform via Docker SDK: {e}")
                        return None
                else:
                    # Remote server - use docker inspect via SSH
                    try:
                        # Method 1: Try to get ImageManifestDescriptor.platform from full JSON
                        try:
                            import json
                            inspect_json_output = execute_docker_command_via_ssh(
                                server_config,
                                f"inspect {image_tag}",
                                check_exit_status=False
                            )
                            if inspect_json_output:
                                inspect_data = json.loads(inspect_json_output)
                                if isinstance(inspect_data, list) and len(inspect_data) > 0:
                                    manifest_descriptor = inspect_data[0].get('ImageManifestDescriptor', {})
                                    if manifest_descriptor:
                                        platform_info = manifest_descriptor.get('platform', {})
                                        if platform_info:
                                            arch = platform_info.get('architecture', '').lower()
                                            os_type = platform_info.get('os', 'linux').lower()
                                            variant = platform_info.get('variant', '').lower()
                                            
                                            if variant:
                                                platform = f'{os_type}/{arch}/{variant}'
                                            else:
                                                platform = f'{os_type}/{arch}'
                                            
                                            app.logger.info(f"Detected image platform from ImageManifestDescriptor (remote): {platform}")
                                            return platform
                        except json.JSONDecodeError:
                            app.logger.debug("Could not parse docker inspect JSON")
                        except Exception as e:
                            app.logger.debug(f"Could not get platform from ImageManifestDescriptor (remote): {e}")
                        
                        # Method 2: Use docker inspect with format
                        inspect_output = execute_docker_command_via_ssh(
                            server_config,
                            f"inspect {image_tag} --format '{{{{.Architecture}}}}|{{{{.Os}}}}|{{{{.Variant}}}}'",
                            check_exit_status=False
                        )
                        if inspect_output:
                            parts = inspect_output.strip().split('|')
                            arch = parts[0].lower() if len(parts) > 0 else ''
                            os_type = (parts[1].lower() if len(parts) > 1 else 'linux')
                            variant = (parts[2].lower() if len(parts) > 2 else '')
                            
                            if arch:
                                # Normalize to Docker platform format
                                if 'arm64' in arch or 'aarch64' in arch:
                                    platform = f'{os_type}/arm64'
                                elif 'amd64' in arch or 'x86_64' in arch:
                                    platform = f'{os_type}/amd64'
                                elif 'arm' in arch:
                                    if 'v7' in variant or 'v6' in variant:
                                        platform = f'{os_type}/arm/v7'
                                    else:
                                        platform = f'{os_type}/arm64'
                                else:
                                    platform = f'{os_type}/{arch}'
                                
                                app.logger.info(f"Detected image platform from inspect format (remote): {platform}")
                                return platform
                        
                        return None
                    except Exception as e:
                        app.logger.debug(f"Could not get image platform via docker inspect: {e}")
                        return None
            except Exception as e:
                app.logger.warning(f"Could not determine image platform: {e}")
                return None
        
        def _check_emulation_support(self, server_config):
            """Check if target server supports cross-architecture emulation (QEMU/binfmt_misc)
            
            Returns:
                dict: {
                    'supported': bool,
                    'qemu_available': bool,
                    'binfmt_misc_available': bool,
                    'message': str
                }
            """
            try:
                is_local = server_config is None or server_config.get('id') == 'local'
                
                qemu_available = False
                binfmt_misc_available = False
                
                if is_local:
                    # Local server - check directly
                    import os
                    # Check for binfmt_misc
                    binfmt_misc_path = '/proc/sys/fs/binfmt_misc'
                    if os.path.exists(binfmt_misc_path):
                        try:
                            entries = os.listdir(binfmt_misc_path)
                            binfmt_misc_available = len(entries) > 0
                            # Check for qemu entries
                            qemu_available = any('qemu' in entry.lower() for entry in entries)
                        except:
                            pass
                    
                    # Check for qemu-x86_64-static
                    import shutil
                    if shutil.which('qemu-x86_64-static'):
                        qemu_available = True
                else:
                    # Remote server - check via SSH
                    app.logger.info(f"Checking emulation support on remote server {server_config.get('hostname')}...")
                    
                    # Check binfmt_misc
                    try:
                        binfmt_check = execute_command_via_ssh(
                            server_config,
                            "ls -la /proc/sys/fs/binfmt_misc/ 2>/dev/null | grep -q qemu && echo 'yes' || echo 'no'",
                            check_exit_status=False
                        )
                        app.logger.debug(f"binfmt_misc check output: {binfmt_check}")
                        if 'yes' in binfmt_check.lower().strip():
                            binfmt_misc_available = True
                            qemu_available = True
                            app.logger.info("✓ binfmt_misc with QEMU found on remote server")
                        else:
                            app.logger.info("✗ binfmt_misc with QEMU NOT found on remote server")
                    except Exception as e:
                        app.logger.warning(f"Could not check binfmt_misc on remote server: {e}")
                    
                    # Check for qemu-x86_64-static
                    try:
                        qemu_check = execute_command_via_ssh(
                            server_config,
                            "which qemu-x86_64-static 2>/dev/null && echo 'yes' || echo 'no'",
                            check_exit_status=False
                        )
                        app.logger.debug(f"qemu-x86_64-static check output: {qemu_check}")
                        if 'yes' in qemu_check.lower().strip():
                            qemu_available = True
                            app.logger.info("✓ qemu-x86_64-static found on remote server")
                        else:
                            app.logger.info("✗ qemu-x86_64-static NOT found on remote server")
                    except Exception as e:
                        app.logger.warning(f"Could not check qemu-x86_64-static on remote server: {e}")
                
                supported = qemu_available or binfmt_misc_available
                
                message = ""
                if supported:
                    if qemu_available and binfmt_misc_available:
                        message = "QEMU emulation is available (binfmt_misc + qemu-x86_64-static)"
                    elif qemu_available:
                        message = "QEMU emulation is available (qemu-x86_64-static found)"
                    else:
                        message = "QEMU emulation is available (binfmt_misc found)"
                else:
                    message = "QEMU emulation is NOT available. Cross-architecture containers will fail with 'exec format error'."
                
                return {
                    'supported': supported,
                    'qemu_available': qemu_available,
                    'binfmt_misc_available': binfmt_misc_available,
                    'message': message
                }
            except Exception as e:
                app.logger.warning(f"Could not check emulation support: {e}")
                return {
                    'supported': False,
                    'qemu_available': False,
                    'binfmt_misc_available': False,
                    'message': f"Could not check emulation support: {str(e)}"
                }
        
        def _validate_architecture_compatibility(self, server_config, image_tag, image_platform=None):
            """Validate architecture compatibility between image and target server
            
            This function:
            1. Detects target server architecture
            2. Detects image platform (if not provided)
            3. Determines if --platform flag is needed
            4. Returns recommended platform flag value
            
            Args:
                server_config: Target server configuration (None for local)
                image_tag: Docker image tag to check
                image_platform: Optional pre-detected image platform (if None, will be detected)
                
            Returns:
                dict: {
                    'target_arch': str,  # Target server architecture (e.g., 'linux/arm64')
                    'image_platform': str,  # Image platform (e.g., 'linux/amd64')
                    'platform_flag': str or None,  # Recommended --platform flag value
                    'compatible': bool,  # True if architectures match
                    'needs_emulation': bool  # True if emulation will be needed
                }
            """
            try:
                # Get target server architecture
                target_arch = self._get_server_architecture(server_config)
                if not target_arch:
                    app.logger.warning("Could not determine target server architecture")
                    target_arch = 'linux/amd64'  # Default fallback
                
                # Get image platform if not provided
                if not image_platform:
                    image_platform = self._get_image_platform(server_config, image_tag)
                
                if not image_platform:
                    app.logger.warning(f"Could not determine image platform for {image_tag}, assuming target architecture")
                    image_platform = target_arch
                
                # Normalize platform strings for comparison
                def normalize_platform(platform_str):
                    """Normalize platform string for comparison"""
                    if not platform_str:
                        return None
                    # Remove variant if present (e.g., 'linux/arm/v7' -> 'linux/arm')
                    parts = platform_str.split('/')
                    if len(parts) >= 2:
                        # Keep os and arch, ignore variant
                        return f"{parts[0]}/{parts[1]}"
                    return platform_str
                
                target_normalized = normalize_platform(target_arch)
                image_normalized = normalize_platform(image_platform)
                
                # Check if architectures match
                compatible = (target_normalized == image_normalized)
                needs_emulation = not compatible
                
                # If emulation is needed, check if it's available
                emulation_support = None
                if needs_emulation:
                    app.logger.info(f"🔍 Checking emulation support for cross-architecture migration (image: {image_platform}, target: {target_arch})...")
                    emulation_support = self._check_emulation_support(server_config)
                    app.logger.info(
                        f"Emulation check results: supported={emulation_support.get('supported')}, "
                        f"qemu={emulation_support.get('qemu_available')}, "
                        f"binfmt_misc={emulation_support.get('binfmt_misc_available')}, "
                        f"message={emulation_support.get('message')}"
                    )
                    if not emulation_support.get('supported'):
                        app.logger.error(
                            f"❌ CRITICAL: Cross-architecture migration requires emulation, but it's NOT available on target server. "
                            f"Image platform: {image_platform}, Target server: {target_arch}. "
                            f"Message: {emulation_support.get('message')}"
                        )
                
                # Determine platform flag value
                # ALWAYS use image platform for --platform flag
                # This ensures Docker uses the correct architecture (or attempts emulation)
                platform_flag = image_platform if image_platform else None
                
                # Determine if migration is possible
                # Migration is possible if:
                # 1. Architectures are compatible (no emulation needed), OR
                # 2. Emulation is needed AND emulation is supported
                if compatible:
                    migration_possible = True
                elif needs_emulation:
                    if emulation_support and emulation_support.get('supported'):
                        migration_possible = True
                    else:
                        migration_possible = False
                else:
                    # Should not happen, but default to True for safety
                    migration_possible = True
                
                result = {
                    'target_arch': target_arch,
                    'image_platform': image_platform,
                    'platform_flag': platform_flag,
                    'compatible': compatible,
                    'needs_emulation': needs_emulation,
                    'emulation_supported': emulation_support.get('supported') if emulation_support else (True if not needs_emulation else False),
                    'emulation_message': emulation_support.get('message') if emulation_support else None,
                    'migration_possible': migration_possible
                }
                
                app.logger.info(
                    f"Migration possibility calculation: "
                    f"compatible={compatible}, "
                    f"needs_emulation={needs_emulation}, "
                    f"emulation_support={emulation_support is not None}, "
                    f"emulation_supported={result.get('emulation_supported')}, "
                    f"migration_possible={migration_possible}"
                )
                
                # Log detailed information
                if compatible:
                    app.logger.info(f"Architecture compatibility: ✓ Image ({image_platform}) matches target server ({target_arch})")
                else:
                    if emulation_support and emulation_support.get('supported'):
                        app.logger.warning(
                            f"Architecture mismatch: ✗ Image ({image_platform}) does not match target server ({target_arch}). "
                            f"Will use --platform {image_platform} with emulation. {emulation_support.get('message')}"
                        )
                    else:
                        app.logger.error(
                            f"❌ Architecture mismatch: ✗ Image ({image_platform}) does not match target server ({target_arch}). "
                            f"Emulation is NOT available: {emulation_support.get('message') if emulation_support else 'unknown'}. "
                            f"Migration will FAIL with 'exec format error'!"
                        )
                
                return result
                
            except Exception as e:
                app.logger.error(f"Error validating architecture compatibility: {e}", exc_info=True)
                # Return safe defaults
                return {
                    'target_arch': 'linux/amd64',
                    'image_platform': 'linux/amd64',
                    'platform_flag': None,
                    'compatible': True,
                    'needs_emulation': False
                }
        
        def _check_port_availability(self, server_config, port_mapping):
            """Check if ports are available on target server"""
            if not port_mapping:
                return []
            
            # Check if this is local server
            is_local = server_config is None or server_config.get('id') == 'local'
            
            conflicts = []
            try:
                # Get list of used ports on target server using multiple methods
                used_ports = set()
                
                # Method 1: Check docker ps output for port mappings
                try:
                    if is_local:
                        # Local server - use Docker client directly
                        import docker
                        client = docker.from_env()
                        containers = client.containers.list(all=True)
                        import re
                        for container in containers:
                            ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})
                            for container_port, host_bindings in ports.items():
                                if host_bindings:
                                    for binding in host_bindings:
                                        host_port = binding.get('HostPort', '')
                                        if host_port:
                                            used_ports.add(host_port)
                    else:
                        # Remote server - check via SSH
                        ps_output = execute_docker_command_via_ssh(server_config, "ps --format '{{.Ports}}'", check_exit_status=False)
                        if ps_output:
                            import re
                            # Parse formats like:
                            # "0.0.0.0:61208->61208/tcp"
                            # "::61208->61208/tcp"
                            # "0.0.0.0:61208->61208/tcp, 0.0.0.0:61209->61209/tcp"
                            for line in ps_output.strip().split('\n'):
                                if line.strip():
                                    # Match host ports (before ->)
                                    port_matches = re.findall(r':(\d+)->', line)
                                    for port in port_matches:
                                        used_ports.add(port)
                except Exception as e:
                    app.logger.debug(f"Could not check ports via docker ps: {e}")
                
                # Method 2: Check system ports using netstat or ss (more reliable)
                try:
                    if is_local:
                        # Local server - use socket to check ports
                        import socket
                        for container_port, host_port in port_mapping.items():
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(1)
                            result = sock.connect_ex(('127.0.0.1', int(host_port)))
                            sock.close()
                            if result == 0:
                                conflicts.append(str(host_port))
                    else:
                        # Remote server - check via SSH
                        # Try ss first (more modern), fallback to netstat
                        port_check_cmd = "ss -tln | grep -E ':[0-9]+' | sed 's/.*:\([0-9]*\).*/\\1/' || netstat -tln | grep -E ':[0-9]+' | sed 's/.*:\([0-9]*\).*/\\1/'"
                        port_output = execute_command_via_ssh(server_config, port_check_cmd, check_exit_status=False)
                        if port_output:
                            for line in port_output.strip().split('\n'):
                                port = line.strip()
                                if port.isdigit():
                                    used_ports.add(port)
                        
                        # Check if any of our ports are in use
                        for container_port, host_port in port_mapping.items():
                            if str(host_port) in used_ports:
                                conflicts.append(str(host_port))
                except Exception as e:
                    app.logger.debug(f"Could not check ports via netstat/ss: {e}")
                        
            except Exception as e:
                app.logger.warning(f"Could not check port availability: {e}")
                # Don't fail on port check errors - let Docker handle it
            
            return conflicts
        
        def _build_docker_run_command(self, config, container_name, image_tag, target_arch=None, source_arch=None):
            """Build docker run command from configuration"""
            import shlex
    
            cmd_parts = ['run', '-d', '--name', container_name]
            
            # Helper to normalize memory limits for docker CLI (expects m/g, not Mi/Gi)
            def _normalize_memory_limit(mem_value):
                if not mem_value:
                    return mem_value
                val = str(mem_value).strip()
                lower = val.lower()
                replacements = {
                    'mib': 'm',
                    'gib': 'g',
                    'mi': 'm',
                    'gi': 'g'
                }
                for suffix, repl in replacements.items():
                    if lower.endswith(suffix):
                        # keep the numeric part, replace suffix
                        return val[: -len(suffix)] + repl
                return val
            
            # Add --pull=never to prevent Docker from trying to pull image from registry
            # We've already loaded the image, so we don't want Docker to try pulling it
            cmd_parts.extend(['--pull', 'never'])
            
            # ALWAYS add platform flag if target_arch is set
            # This is critical for cross-architecture migration (e.g., amd64 image on arm64 server)
            # The target_arch parameter should be the IMAGE's platform, not the target server's architecture
            if target_arch:
                app.logger.info(
                    f"✓ Adding --platform flag to docker run: {target_arch} "
                    f"(source server: {source_arch or 'unknown'})"
                )
                cmd_parts.extend(['--platform', target_arch])
            else:
                app.logger.error(
                    "❌ CRITICAL ERROR: No --platform flag will be added! "
                    "target_arch is None. This WILL cause 'exec format error' if image architecture differs from target server. "
                    f"image_tag={image_tag}, container_name={container_name}"
                )
            
            # Add restart policy
            if config.get('restart_policy') and config['restart_policy'] != 'no':
                cmd_parts.extend(['--restart', config['restart_policy']])
            
            # Add port mappings
            for container_port, host_port in config.get('port_mapping', {}).items():
                cmd_parts.extend(['-p', f"{host_port}:{container_port}"])
            
            # Add environment variables
            for key, value in config.get('environment', {}).items():
                cmd_parts.extend(['-e', f"{key}={value}"])
            
            # Add volumes
            for source, destination in config.get('volumes', {}).items():
                # Validate source/destination; skip invalid to avoid "invalid mode" errors
                if not source or not destination:
                    app.logger.warning(f"Skipping volume with missing path: source='{source}', dest='{destination}'")
                    continue
                # Basic safety: disallow mistaken mode-only entries (e.g., '/rootfs' treated as mode)
                if source.startswith(':') or destination.startswith(':'):
                    app.logger.warning(f"Skipping volume with leading colon (likely malformed): {source}:{destination}")
                    continue
                
                # Handle accidental mode embedded in destination (e.g., "/rootfs:ro" or "/:/rootfs")
                dest_part = destination
                mode_part = None
                if ':' in destination:
                    split_dest = destination.split(':', 1)
                    dest_part = split_dest[0]
                    mode_part = split_dest[1].strip() or None
                
                # Destination cannot be '/' (Docker rejects binding to root)
                if dest_part == '/':
                    app.logger.warning(f"Skipping volume because destination cannot be '/': {source}:{destination}")
                    continue
                
                # If mode_part looks invalid (e.g., '/rootfs'), drop it
                valid_modes = {'ro', 'rw', 'z', 'Z', 'shared', 'rshared', 'slave', 'rslave', 'private', 'rprivate', 'delegated', 'cached'}
                if mode_part and mode_part not in valid_modes:
                    app.logger.warning(f"Dropping invalid volume mode '{mode_part}' for {source}:{destination}; using destination '{dest_part}' only")
                    mode_part = None
                
                if mode_part:
                    cmd_parts.extend(['-v', f"{source}:{dest_part}:{mode_part}"])
                else:
                    cmd_parts.extend(['-v', f"{source}:{dest_part}"])
            
            # Add network. If source had --network host but we have port_mapping, use bridge on target
            # so that -p is applied (Docker ignores -p when using host network).
            network = config.get('network') or 'bridge'
            if config.get('port_mapping') and network == 'host':
                app.logger.info("Using bridge network on target so port mappings apply (source had host network)")
                network = 'bridge'
            if network and network != 'bridge':
                cmd_parts.extend(['--network', network])
            
            # Add privileged if required (cadvisor/infrastructure often needs this)
            privileged_flag = config.get('privileged', False)
            # Heuristic: if image name contains cadvisor and not already privileged, enable it
            if not privileged_flag and ('cadvisor' in image_tag.lower() or 'cadvisor' in container_name.lower()):
                privileged_flag = True
                app.logger.info("Enabling --privileged for cadvisor container")
            if privileged_flag:
                cmd_parts.append('--privileged')
            
            # Add resource limits
            if config.get('cpu_limit'):
                try:
                    cpu_value = float(str(config['cpu_limit']).strip())
                    if cpu_value > 0:
                        cmd_parts.extend(['--cpus', str(cpu_value)])
                    else:
                        app.logger.warning(
                            f"Skipping invalid/non-positive cpu_limit during docker run build: {config['cpu_limit']}"
                        )
                except (TypeError, ValueError):
                    app.logger.warning(
                        f"Skipping non-numeric cpu_limit during docker run build: {config['cpu_limit']}"
                    )
            if config.get('memory_limit'):
                normalized_mem = _normalize_memory_limit(config['memory_limit'])
                if normalized_mem != config['memory_limit']:
                    app.logger.info(f"Normalized memory limit for docker run: {config['memory_limit']} -> {normalized_mem}")
                cmd_parts.extend(['--memory', normalized_mem])
            
            # Add entrypoint if present. Docker allows only ONE executable for --entrypoint;
            # if the image has Entrypoint=["tini", "--", "/docker-entrypoint.sh"], we must pass
            # only "tini" to --entrypoint and put the rest ("--", "/docker-entrypoint.sh") in the
            # command after the image, otherwise " -- " is parsed as end-of-options and breaks the run.
            entrypoint_args_to_append = []  # rest of entrypoint list to pass as command prefix
            if config.get('entrypoint'):
                entrypoint = config['entrypoint']
                if isinstance(entrypoint, list):
                    if len(entrypoint) > 1:
                        entrypoint_str = entrypoint[0]
                        entrypoint_args_to_append = entrypoint[1:]
                    else:
                        entrypoint_str = entrypoint[0] if entrypoint else None
                else:
                    entrypoint_str = entrypoint
                if entrypoint_str:
                    cmd_parts.extend(['--entrypoint', entrypoint_str])
            
            # Add image (must come right after options; nothing between --entrypoint and IMAGE)
            cmd_parts.append(image_tag)
            
            # Add command: first any extra entrypoint args (e.g. "--", "/docker-entrypoint.sh"), then Cmd
            for arg in entrypoint_args_to_append:
                cmd_parts.append(str(arg))
            if config.get('command'):
                command = config['command']
                if isinstance(command, list):
                    for cmd_part in command:
                        cmd_parts.append(str(cmd_part))
                else:
                    cmd_parts.append(str(command))
            
            # Join all parts with spaces
            # Note: This creates a shell command string, so proper quoting is important
            return ' '.join(shlex.quote(str(part)) for part in cmd_parts)
    

    return ContainerMigrate
