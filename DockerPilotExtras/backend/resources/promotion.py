"""Environment promotion API resources."""

from __future__ import annotations

import threading

from backend.security import safe_error_message
from backend.services.migration_execution import DockerPilotExecutionContext
from backend.services.migration_runner import MigrationSpec


def create_promotion_resources(
    *,
    Resource,
    app,
    request,
    session,
    datetime_cls,
    deployment_progress,
    get_dockerpilot,
    consume_elevation_token,
    find_all_deployment_configs_for_env,
    resolve_server_id_for_env,
    promote_config_to_server,
    move_many_container_bindings,
    move_container_binding,
    format_env_name,
    find_active_deployment_dir,
    migration_runner,
    execution_context_factory=DockerPilotExecutionContext,
):
    """Return environment promotion resource classes with injected dependencies."""

    datetime = datetime_cls
    _deployment_progress = deployment_progress
    _consume_elevation_token = consume_elevation_token
    _migration_runner = migration_runner
    _execution_context_factory = execution_context_factory

    class HealthCheck(Resource):
        """Health check endpoint"""
        def get(self):
            return {'status': 'ok', 'timestamp': datetime.now().isoformat()}

    class EnvironmentPromote(Resource):
        """Promote environment using DockerPilot"""
        def post(self):
            try:
                data = request.get_json()
                from_env = data.get('from_env')
                to_env = data.get('to_env')
                
                if not from_env or not to_env:
                    return {'error': 'Missing environment names'}, 400
                
                # Find ALL deployment configs for the source environment
                # Each container MUST have a deployment-{env}.yml file
                configs_to_promote = find_all_deployment_configs_for_env(from_env)
                
                if not configs_to_promote:
                    return {
                        'success': False,
                        'error': f'No deployment configurations found for {from_env} environment. Please ensure containers have deployment-{from_env}.yml configs.'
                    }, 404
                
                app.logger.info(f"Found {len(configs_to_promote)} deployment config(s) for {from_env} environment")
                
                # Resolve target server for environment (env -> server mapping)
                target_server_id = resolve_server_id_for_env(to_env)
                results = {
                    'success': [],
                    'failed': []
                }
                
                for config_item in configs_to_promote:
                    container_name = config_item['container_name']
                    config_path_str = config_item['path']
                    
                    try:
                        app.logger.info(f"Promoting {container_name} from {from_env} to {to_env} using config: {config_path_str}")
                        skip_backup = data.get('skip_backup', False)
                        success = promote_config_to_server(target_server_id, config_path_str, from_env, to_env, skip_backup)
                        
                        if success:
                            results['success'].append(container_name)
                            app.logger.info(f"Successfully promoted {container_name}")
                        else:
                            results['failed'].append(container_name)
                            app.logger.error(f"Failed to promote {container_name}")
                    except Exception as e:
                        app.logger.error(f"Error promoting {container_name}: {e}")
                        results['failed'].append(container_name)
    
                if results['success']:
                    try:
                        move_many_container_bindings(results['success'], from_env, to_env)
                    except Exception as binding_err:
                        app.logger.warning(f"Failed to update env container bindings after promotion: {binding_err}")
                
                # Return summary
                if results['failed'] and results['success']:
                    return {
                        'success': False,
                        'partial': True,
                        'message': f'Promoted {len(results["success"])}/{len(configs_to_promote)} containers',
                        'successful': results['success'],
                        'failed': results['failed'],
                        'error': f'Some promotions failed: {", ".join(results["failed"])}'
                    }, 207
                if results['failed']:
                    return {
                        'success': False,
                        'partial': False,
                        'message': f'Promoted 0/{len(configs_to_promote)} containers',
                        'successful': results['success'],
                        'failed': results['failed'],
                        'error': f'All promotions failed: {", ".join(results["failed"])}'
                    }, 500
                return {
                    'success': True,
                    'message': f'Successfully promoted {len(results["success"])} container(s) from {format_env_name(from_env)} to {format_env_name(to_env)}',
                    'promoted_containers': results['success']
                }
                        
            except Exception as e:
                app.logger.error(f"Promotion request error: {e}")
                return {'error': str(e)}, 500
    
    
    class CancelPromotion(Resource):
        """Cancel ongoing container promotion"""
        def post(self):
            try:
                data = request.get_json()
                container_name = data.get('container_name')
                
                if not container_name:
                    return {'error': 'container_name is required'}, 400
                
                # Create cancel flag file
                cancel_flag_path = app.config['CONFIG_DIR'] / f'cancel_{container_name}.flag'
                cancel_flag_path.touch()
                
                # Update deployment progress to show cancellation
                if container_name in _deployment_progress:
                    _deployment_progress[container_name] = {
                        'stage': 'cancelled',
                        'progress': _deployment_progress[container_name].get('progress', 0),
                        'message': f'Container promotion cancelled: {container_name}',
                        'timestamp': datetime.now().isoformat()
                    }
                
                app.logger.info(f"Cancel flag created for {container_name} and progress updated")
                
                return {
                    'success': True,
                    'message': f'Cancelling container promotion {container_name}. Deployment will be stopped at the next checkpoint.'
                }
                
            except Exception as e:
                app.logger.error(f"Cancel promotion failed: {e}")
                return {'error': str(e)}, 500

    class EnvironmentPromoteSingle(Resource):
        """Promote single container from one environment to another"""
        def post(self):
            container_name = None
            execution_context = None
            try:
                data = request.get_json()
                from_env = data.get('from_env')
                to_env = data.get('to_env')
                container_name = data.get('container_name')
                skip_backup = data.get('skip_backup', False)
                include_data = data.get('include_data', True)
                stop_source = data.get('stop_source', False)
                elevation_token = (data.get('elevation_token') or '').strip()
                
                if not from_env or not to_env or not container_name:
                    return {'error': 'Missing required parameters'}, 400
                
                app.logger.info(f"Promoting single container {container_name} from {from_env} to {to_env}")

                def initialize_promotion_progress():
                    _deployment_progress[container_name] = {
                        'stage': 'initializing',
                        'progress': 0,
                        'message': f'Inicjalizacja promocji {container_name}...',
                        'timestamp': datetime.now().isoformat()
                    }
                    _deployment_progress[container_name] = {
                        'stage': 'preparing',
                        'progress': 10,
                        'message': 'Preparing promotion...',
                        'timestamp': datetime.now().isoformat()
                    }
                
                sudo_password = None
                if elevation_token:
                    token_ok, token_message, token_password = _consume_elevation_token(
                        elevation_token,
                        expected_action='environment.promote_single',
                        expected_scope={
                            'container_name': container_name,
                            'from_env': from_env,
                            'to_env': to_env,
                        },
                    )
                    # Compatibility: the deprecated /sudo-password endpoint
                    # issues a session-bound, one-time legacy token. Strict
                    # validation always runs first. If it fails, legacy action
                    # validation may succeed; otherwise preserve the strict error.
                    if not token_ok:
                        strict_message = token_message
                        legacy_ok, _legacy_message, legacy_password = _consume_elevation_token(
                            elevation_token,
                            expected_action='legacy.sudo_password',
                        )
                        if legacy_ok:
                            token_ok = True
                            token_password = legacy_password
                        else:
                            token_message = strict_message
                    if not token_ok:
                        return {'error': token_message}, 403
                    if session.get('legacy_elevation_token') == elevation_token:
                        session.pop('legacy_elevation_token', None)
                    sudo_password = token_password
                    app.logger.info("Using elevation token for privileged promotion flow")
                else:
                    # Older clients call /sudo-password and then promote without
                    # forwarding the returned token. Consume only the opaque
                    # compatibility token; the sudo password never lives in the
                    # Flask session.
                    legacy_token = session.pop('legacy_elevation_token', None)
                    if legacy_token:
                        token_ok, token_message, token_password = _consume_elevation_token(
                            legacy_token,
                            expected_action='legacy.sudo_password',
                        )
                        if not token_ok:
                            return {'error': token_message}, 403
                        sudo_password = token_password
                        app.logger.info("Using legacy elevation token for privileged promotion flow")
    
                execution_context = _execution_context_factory(
                    get_dockerpilot,
                    sudo_password=sudo_password,
                )
                
                try:
                    # Promotion is implemented as a server-to-server migration (enterprise-style env isolation).
                    # Source/target servers are resolved from env->server mapping.
                    source_server_id = resolve_server_id_for_env(from_env)
                    target_server_id = resolve_server_id_for_env(to_env)
    
                    if source_server_id == target_server_id:
                        def promote_on_shared_server():
                            initialize_promotion_progress()
                            _deployment_progress[container_name] = {
                                'stage': 'deploying',
                                'progress': 30,
                                'message': (
                                    f'Promoting {container_name} on shared server '
                                    f'({format_env_name(from_env)} -> {format_env_name(to_env)})...'
                                ),
                                'timestamp': datetime.now().isoformat()
                            }
                            deployment_dir = find_active_deployment_dir(container_name)
                            if not deployment_dir:
                                raise FileNotFoundError(
                                    f"No active deployment directory found for {container_name}"
                                )
                            config_path = deployment_dir / f'deployment-{from_env}.yml'
                            if not config_path.exists():
                                config_path = deployment_dir / 'deployment.yml'
                            if not config_path.exists():
                                raise FileNotFoundError(
                                    f"Deployment config not found for {container_name} and env {from_env}"
                                )
                            details = {
                                'mode': 'same-server',
                                'config_path': str(config_path),
                                'source_server_id': source_server_id,
                                'target_server_id': target_server_id,
                            }
                            promoted = promote_config_to_server(
                                target_server_id,
                                str(config_path),
                                from_env,
                                to_env,
                                bool(skip_backup),
                            )
                            return {
                                'success': bool(promoted),
                                'details': details,
                            }

                        promotion_result = _migration_runner.run_operation_inline(
                            container_name,
                            promote_on_shared_server,
                            execution_context=execution_context,
                        )
                        if promotion_result.status in {409, 503}:
                            return promotion_result.to_response()
                        success = promotion_result.completed_successfully
                        body = promotion_result.body
                        if (
                            success
                            and isinstance(promotion_result.body, dict)
                            and isinstance(promotion_result.body.get('details'), dict)
                        ):
                            body = promotion_result.body['details']
                    else:
                        def mark_cross_server_migration_started():
                            initialize_promotion_progress()
                            _deployment_progress[container_name] = {
                                'stage': 'migrating',
                                'progress': 20,
                                'message': f'Migrating {container_name} from {format_env_name(from_env)} to {format_env_name(to_env)}...',
                                'timestamp': datetime.now().isoformat()
                            }
    
                        # Promotion must wait for the real synchronous migration
                        # outcome; an accepted async job is not a completed promotion.
                        migrate_result = _migration_runner.run_inline(
                            MigrationSpec(
                                container_name=container_name,
                                source_server_id=source_server_id,
                                target_server_id=target_server_id,
                                include_data=bool(include_data),
                                stop_source=bool(stop_source),
                            ),
                            execution_context=execution_context,
                            on_reserved=mark_cross_server_migration_started,
                        )
                        if migrate_result.status in {409, 503}:
                            return migrate_result.to_response()
                        body = migrate_result.body
                        success = migrate_result.completed_successfully
                        if not migrate_result.is_terminal:
                            body = {
                                'error': 'Migration was accepted but has not completed',
                                'details': migrate_result.body,
                            }
                    
                    # Wait a moment for final progress callback from pilot
                    import time
                    time.sleep(0.5)
                    
                    # Check if pilot already set 'completed' via callback
                    current_progress = _deployment_progress.get(container_name, {})
                    current_stage = current_progress.get('stage', '')
                    
                    if success:
                        try:
                            move_container_binding(container_name, from_env, to_env)
                        except Exception as binding_err:
                            app.logger.error(
                                "Failed to update env container binding for %s: %s",
                                container_name,
                                safe_error_message(binding_err),
                            )
                            _deployment_progress[container_name] = {
                                'stage': 'failed',
                                'progress': 99,
                                'message': 'Migration completed, but environment binding update failed',
                                'timestamp': datetime.now().isoformat(),
                            }
                            return {
                                'success': False,
                                'partial': True,
                                'reconciliation_required': True,
                                'error': 'Migration completed, but environment binding update failed',
                                'container_name': container_name,
                                'details': body,
                            }, 500
                        # Only set 'completed' if pilot didn't already do it via callback
                        if current_stage != 'completed':
                            _deployment_progress[container_name] = {
                                'stage': 'completed',
                                'progress': 100,
                                'message': f'Promotion completed successfully!',
                                'timestamp': datetime.now().isoformat()
                            }
                        app.logger.info(f"Successfully promoted {container_name}")
                        return {
                            'success': True,
                            'message': f'Container {container_name} promoted (migrated) from {format_env_name(from_env)} to {format_env_name(to_env)}',
                            'container_name': container_name,
                            'details': body
                        }
                    else:
                        # Only set 'failed' if not already set by pilot callback
                        if current_stage not in ['failed', 'error', 'completed']:
                            _deployment_progress[container_name] = {
                                'stage': 'failed',
                                'progress': 0,
                                'message': f'Promotion failed',
                                'timestamp': datetime.now().isoformat()
                            }
                        app.logger.error(f"Failed to promote {container_name}")
                        error = (
                            body.get('error')
                            if isinstance(body, dict)
                            else None
                        ) or f'Failed to promote {container_name}'
                        return {
                            'success': False,
                            'error': error,
                        }, 500
                        
                except Exception as e:
                    # Only set 'error' if not already set by pilot callback
                    current_progress = _deployment_progress.get(container_name, {})
                    current_stage = current_progress.get('stage', '')
                    if current_stage not in ['failed', 'error', 'completed']:
                        _deployment_progress[container_name] = {
                            'stage': 'error',
                            'progress': 0,
                            'message': f'Error: {str(e)}',
                            'timestamp': datetime.now().isoformat()
                        }
                    app.logger.error(f"Error promoting {container_name}: {e}")
                    return {'error': str(e)}, 500
                finally:
                    # Clean up progress after 2 minutes (reduced from 5)
                    def cleanup_progress():
                        import time
                        time.sleep(120)  # 2 minutes
                        if container_name in _deployment_progress:
                            progress = _deployment_progress.get(container_name, {})
                            # Only cleanup if still in completed/failed/error state
                            if progress.get('stage') in ['completed', 'failed', 'error', 'cancelled']:
                                del _deployment_progress[container_name]
                    threading.Thread(target=cleanup_progress, daemon=True).start()
                    
            except Exception as e:
                if container_name in _deployment_progress:
                    del _deployment_progress[container_name]
                app.logger.error(f"Promotion request error: {e}")
                return {'error': str(e)}, 500
    

    return HealthCheck, EnvironmentPromote, CancelPromotion, EnvironmentPromoteSingle
