"""Deployment and promotion services extracted from DockerPilotEnhanced."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
import time

import requests
import yaml

from .build_source import (
    discover_dockerfile_candidates as _discover_dockerfile_candidates_impl,
    inspect_build_source as _inspect_build_source_impl,
    is_dockerfile_candidate as _is_dockerfile_candidate_impl,
)
from .deployment_build import (
    build_image_enhanced as _build_image_enhanced_impl,
    build_image_standalone as _build_image_standalone_impl,
)
from .deployment_config_io import (
    create_deployment_config as _create_deployment_config_impl,
    create_dockerfile_template as _create_dockerfile_template_impl,
)
from .deployment_helpers import (
    deployment_config_from_dict as _deployment_config_from_dict_impl,
    get_resource_limits as _get_resource_limits_impl,
    normalize_volumes as _normalize_volumes_impl,
    resolve_runtime_network as _resolve_runtime_network_impl,
)
from .deployment_history import (
    record_deployment as _record_deployment_impl,
    show_deployment_history as _show_deployment_history_impl,
)
from .deployment_promotion import (
    environment_promotion as _environment_promotion_impl,
    run_post_promotion_validation as _run_post_promotion_validation_impl,
    run_pre_promotion_checks as _run_pre_promotion_checks_impl,
)
from .deployment_strategies.blue_green import blue_green_deploy as _blue_green_deploy_strategy
from .deployment_strategies.canary import canary_deploy as _canary_deploy_strategy
from .deployment_strategies.quick import quick_deploy as _quick_deploy_strategy
from .deployment_strategies.rolling import rolling_deploy as _rolling_deploy_strategy
from .deployment_validation import (
    comprehensive_container_validation as _comprehensive_container_validation_impl,
)
from .image_preparation import (
    ensure_image_from_existing_container as _ensure_image_from_existing_container_impl,
    prepare_image as _prepare_image_impl,
)
from .health_checks import (
    advanced_health_check as _advanced_health_check_impl,
    detect_health_check_endpoint as _detect_health_check_endpoint_impl,
    monitor_canary_performance as _monitor_canary_performance_impl,
    run_parallel_tests as _run_parallel_tests_impl,
    should_run_parallel_tests as _should_run_parallel_tests_impl,
)
from .models import DeploymentConfig


def _load_dockerfile_template_bodies() -> dict[str, str]:
    """Load embedded Dockerfile starter bodies shipped next to this module."""
    path = Path(__file__).with_name("dockerfile_template_bodies.yaml")
    if not path.is_file():
        raise FileNotFoundError(f"Dockerfile templates data file not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping in {path.name}, got {type(raw).__name__}"
        )
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ValueError(
                f"Template {key!r} in {path.name} must be a string, got {type(value).__name__}"
            )
        out[str(key)] = value
    return out


class DeploymentServiceMixin:
    """Mixin containing deployment/build/promotion logic for DockerPilot."""

    _DOCKERFILE_TEMPLATE_BODIES = _load_dockerfile_template_bodies()

    def get_build_template_choices(self) -> list[str]:
        """Return the supported Dockerfile template names."""
        return sorted(self._DOCKERFILE_TEMPLATE_BODIES.keys())

    def inspect_build_source(self, dockerfile_path: str) -> dict[str, Any]:
        """Inspect a build source path and report how Dockerfile resolution will behave."""
        return _inspect_build_source_impl(
            dockerfile_path,
            is_candidate=self._is_dockerfile_candidate,
            discover_candidates=self._discover_dockerfile_candidates,
        )

    def _is_dockerfile_candidate(self, candidate: Path) -> bool:
        """Return True if the filename matches a Dockerfile-style pattern."""
        return _is_dockerfile_candidate_impl(candidate)

    def _discover_dockerfile_candidates(self, search_root: Path) -> list[Path]:
        """Search for Dockerfile-like files in the directory and one level below it."""
        return _discover_dockerfile_candidates_impl(
            search_root,
            is_candidate=self._is_dockerfile_candidate,
        )

    def create_dockerfile_template(self, destination: str, template_name: str) -> bool:
        """Create a starter Dockerfile in the requested directory."""
        return _create_dockerfile_template_impl(self, destination, template_name)

    def create_deployment_config(self, config_path: str = "deployment.yml") -> bool:
        """Create deployment configuration template from file."""
        return _create_deployment_config_impl(self, config_path)

    def _deployment_config_from_dict(self, deployment: dict) -> DeploymentConfig:
        """Build DeploymentConfig from raw dict while safely ignoring unknown keys."""
        return _deployment_config_from_dict_impl(deployment, self.logger)

    def _resolve_runtime_network(self, requested_network: Optional[str]) -> Optional[str]:
        """Return safe network name for container start, falling back to bridge if missing."""
        return _resolve_runtime_network_impl(
            requested_network,
            get_network=lambda network: self.client.networks.get(network),
            warn=lambda message: self.logger.warning(message),
        )

    def _ensure_image_from_existing_container(self, image_tag: str, container_name: Optional[str]) -> bool:
        """Try to satisfy image requirement by aliasing image used by existing container."""
        return _ensure_image_from_existing_container_impl(
            image_tag,
            container_name,
            client=self.client,
            logger=self.logger,
        )

    def deploy_from_config(self, config_path: str, deployment_type: str = "rolling") -> bool:
        """Deploy using configuration file"""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            deployment = config['deployment']
            deployment_config = self._deployment_config_from_dict(deployment)
            build_config = config.get('build', {})
            
            self.logger.info(f"Starting {deployment_type} deployment from config: {config_path}")
            
            if deployment_type == "blue-green":
                return self._blue_green_deploy_enhanced(deployment_config, build_config)
            elif deployment_type == "canary":
                return self._canary_deploy(deployment_config, build_config)
            else:  # rolling deployment
                return self._rolling_deploy(deployment_config, build_config)
                
        except Exception as e:
            self.logger.error(f"Deployment from config failed: {e}")
            return False

    def _rolling_deploy(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Enhanced rolling deployment with zero-downtime and full logging"""
        return _rolling_deploy_strategy(self, config, build_config)

    def view_container_logs(self, container_name: str = None, tail: int = 50):
        """View container logs."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return None
        return self.container_manager.view_container_logs(container_name, tail)
    
    def view_container_json(self, container_name: str):
        """Display container information in JSON format."""
        if not self.container_manager:
            self.logger.error("Container manager not initialized - Docker client not available")
            return None
        return self.container_manager.view_container_json(container_name)


    def _blue_green_deploy_enhanced(self, config: DeploymentConfig, build_config: dict, skip_backup: bool = False) -> bool:
        """Enhanced Blue-Green deployment with advanced features."""
        return _blue_green_deploy_strategy(
            self,
            config,
            build_config,
            skip_backup=skip_backup,
        )

    def quick_deploy(self, dockerfile_path: str = ".", image_tag: str = None, 
                    container_name: str = None, port_mapping: dict = None, 
                    environment: dict = None, volumes: dict = None,
                    yaml_config: str = None, cleanup_old_image: bool = True) -> bool:
        """Quick deployment: build and replace the target container."""
        return _quick_deploy_strategy(
            self,
            dockerfile_path=dockerfile_path,
            image_tag=image_tag,
            container_name=container_name,
            port_mapping=port_mapping,
            environment=environment,
            volumes=volumes,
            yaml_config=yaml_config,
            cleanup_old_image=cleanup_old_image,
        )

    def _canary_deploy(self, config: DeploymentConfig, build_config: dict) -> bool:
        """Canary deployment with gradual traffic shifting."""
        return _canary_deploy_strategy(self, config, build_config)

    def _prepare_image(self, image_tag: str, build_config: dict = None, container_name: Optional[str] = None):
        """Prepare image for deployment - check if exists, pull, or build."""
        return _prepare_image_impl(
            image_tag,
            build_config,
            container_name,
            client=self.client,
            logger=self.logger,
            ensure_existing_image=self._ensure_image_from_existing_container,
            build_image=self._build_image_enhanced,
        )

    def _build_image_enhanced(self, image_tag: str, build_config: dict) -> bool:
        """Enhanced image building with advanced features."""
        return _build_image_enhanced_impl(self, image_tag, build_config)
        
    def build_image_standalone(
        self,
        dockerfile_path: str,
        tag: str,
        no_cache: bool = False,
        pull: bool = True,
        pull_if_missing: bool = False,
        generate_template: Optional[str] = None,
    ) -> bool:
        """Standalone image building function."""
        return _build_image_standalone_impl(
            self,
            dockerfile_path,
            tag,
            no_cache=no_cache,
            pull=pull,
            pull_if_missing=pull_if_missing,
            generate_template=generate_template,
        )

    def _detect_health_check_endpoint(self, image_tag: str) -> Optional[str]:
        """Select a health-check endpoint for an image."""
        return _detect_health_check_endpoint_impl(
            image_tag,
            defaults=self._load_health_check_defaults(),
            config=self.config,
            logger=self.logger,
        )
    
    def _advanced_health_check(self, port: str, endpoint: Optional[str], timeout: int, max_retries: int) -> bool:
        """Run the retrying HTTP health check."""
        return _advanced_health_check_impl(
            port,
            endpoint,
            timeout,
            max_retries,
            logger=self.logger,
        )

    def _comprehensive_container_validation(self, container, config: DeploymentConfig, 
                                          port: str, target_name: str) -> tuple:
        """Run the comprehensive pre-traffic-switch validation."""
        return _comprehensive_container_validation_impl(
            container,
            config,
            port,
            target_name,
            get_database_config=self._get_database_config,
            get_database_name=self._get_database_name,
            logger=self.logger,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            clock=lambda: time.time(),
            sleep=lambda seconds: time.sleep(seconds),
        )

    def _get_resource_limits(self, config: DeploymentConfig) -> dict:
        """Convert resource limits to Docker API format"""
        return _get_resource_limits_impl(config)

    def _normalize_volumes(self, volumes: Dict[str, str]) -> list:
        """Convert volumes from config format to Docker API format.
        
        Docker Python API's containers.run() expects volumes as a list of strings
        in the format: ['/host/path:/container/path', 'volume_name:/container/path']
        
        Supports:
        - Named volumes: 'volume_name': '/container/path' -> ['volume_name:/container/path']
        - Bind mounts: '/host/path': '/container/path' -> ['/host/path:/container/path']
        - Already formatted as list: ['volume:/path'] -> unchanged
        - Already formatted as dict with bind/mode: {'/host': {'bind': '/container', 'mode': 'rw'}} -> ['/host:/container:rw']
        """
        return _normalize_volumes_impl(volumes, self.logger)

    def _should_run_parallel_tests(self) -> bool:
        """Determine if parallel tests should be run"""
        return _should_run_parallel_tests_impl(self.config)

    def _run_parallel_tests(self, port: str, config: DeploymentConfig) -> bool:
        """Run parallel tests against new deployment"""
        return _run_parallel_tests_impl(
            port,
            self.config,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            log_error=lambda message: self.logger.error(message),
        )

    def _monitor_canary_performance(self, port: str, duration: int) -> bool:
        """Monitor canary deployment performance"""
        return _monitor_canary_performance_impl(
            port,
            duration,
            request_get=lambda url, **kwargs: requests.get(url, **kwargs),
            clock=lambda: time.time(),
            sleep=lambda seconds: time.sleep(seconds),
            log_error=lambda message: self.logger.error(message),
            log_info=lambda message: self.logger.info(message),
        )

    def _record_deployment(self, deployment_id: str, config: DeploymentConfig, 
                          deployment_type: str, success: bool, duration: timedelta, target_env: str = None):
        """Record deployment in history"""
        return _record_deployment_impl(
            deployment_id,
            config,
            deployment_type,
            success,
            duration,
            target_env,
            append_record=lambda record: self.deployment_history.append(record),
            log_error=lambda message: self.logger.error(message),
            now=datetime.now,
            history_file="deployment_history.json",
        )

    def show_deployment_history(self, limit: int = 10):
        """Show deployment history."""
        return _show_deployment_history_impl(self.console, self.logger, limit)

    def environment_promotion(self, source_env: str, target_env: str, 
                            config_path: str = None, skip_backup: bool = False) -> bool:
        """Promote deployment between environments (dev -> staging -> prod)."""
        return _environment_promotion_impl(
            self,
            source_env,
            target_env,
            config_path=config_path,
            skip_backup=skip_backup,
        )

    def _run_pre_promotion_checks(self, source_env: str, target_env: str) -> bool:
        """Run checks before promoting between environments."""
        return _run_pre_promotion_checks_impl(self, source_env, target_env)

    def _run_post_promotion_validation(self, environment: str, config: DeploymentConfig) -> bool:
        """Validate deployment after promotion."""
        return _run_post_promotion_validation_impl(self, environment, config)
