"""
DockerPilot Extras - Flask Backend API
Backend for CI/CD Pipeline Management
"""

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_restful import Api, Resource
import os
import json
import yaml
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
import sys
import hashlib
import re
import importlib.util
import time
import secrets
import threading
import base64
import hmac
import struct
import binascii

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent.parent))
# Add src directory to path for DockerPilot import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from utils.pipeline_generator import (
    PipelineGenerator, 
    parse_env_vars,
    generate_deployment_config_for_environment
)
from backend.api import register_api_routes
from backend.preflight import run_preflight_checks
from backend.resources.auth import create_auth_resources
from backend.resources.commands import create_command_resources
from backend.resources.environment import create_environment_resources
from backend.resources.migration import create_migration_resource
from backend.resources.pipeline import create_pipeline_resources
from backend.resources.promotion import create_promotion_resources
from backend.resources.progress import create_progress_resources
from backend.resources.servers import create_server_resources
from backend.resources.storage import create_storage_resources
from backend.resources.status import create_status_resources
from backend.services.environment_status import build_environment_status as _svc_build_environment_status
from backend.services.host_network import (
    extract_port_from_string as _svc_extract_port_from_string,
    infer_port_mapping_for_host_network as _svc_infer_port_mapping_for_host_network,
)
from backend.services.remote_probe import (
    build_status_context as _svc_build_status_context,
    probe_remote_binary_version as _svc_probe_remote_binary_version,
    run_remote_probe as _svc_run_remote_probe,
)
from backend.services.server_config import (
    convert_putty_key_to_openssh as _svc_convert_putty_key_to_openssh,
    get_servers_config_path as _svc_get_servers_config_path,
    load_servers_config as _svc_load_servers_config,
    save_servers_config as _svc_save_servers_config,
    test_ssh_connection as _svc_test_ssh_connection,
)
from backend.storage import (
    DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
    DEFAULT_POSTGRES_SCHEMA,
    DEFAULT_POSTGRES_TABLE_PREFIX,
    FileStateStore,
    StorageError,
    build_postgres_dsn,
    create_store,
    resolve_storage_config,
    save_storage_config,
    sanitize_postgres_config,
    test_postgres_connection,
)
from dockerpilot.pilot import DockerPilotEnhanced
from dockerpilot.models import LogLevel

# ==================== DEPLOYMENT MANAGEMENT HELPERS ====================

def format_env_name(env: str) -> str:
    """Format environment name for user-friendly display
    
    Args:
        env: Environment name (dev/staging/prod)
    
    Returns:
        Formatted name (DEV/Pre-Prod/PROD)
    """
    env_labels = {
        'dev': 'DEV',
        'staging': 'Pre-Prod',
        'prod': 'PROD'
    }
    return env_labels.get(env.lower(), env.upper())

def generate_deployment_id(container_name: str, image_tag: str = None) -> str:
    """Generate unique deployment identifier
    
    Format: {container_name}_{hash}
    Hash is based on container_name + image_tag + timestamp
    """
    timestamp = datetime.now().isoformat()
    hash_input = f"{container_name}_{image_tag or 'latest'}_{timestamp}"
    # Generate short hash (first 12 chars of sha256)
    hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
    # Create safe directory name (replace special chars)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', container_name.lower())
    # Create unique ID with special chars for uniqueness
    unique_id = f"{hash_value[:4]}!{hash_value[4:8]}{hash_value[8:12]}"
    return f"{safe_name}_{unique_id}"

def _detect_health_check_endpoint_from_containers(image_tag: str) -> str:
    """Dynamically detect health check endpoint from running containers
    
    Uses docker ps to get list of running containers and their images,
    then builds a dynamic mapping of services to health check endpoints.
    
    Args:
        image_tag: Docker image tag (e.g., 'qdrant/qdrant:latest', 'ollama/ollama:latest')
        
    Returns:
        Health check endpoint path, or None for non-HTTP services
    """
    image_lower = image_tag.lower()
    
    # Try to get running containers to build dynamic service list
    try:
        pilot = get_dockerpilot()
        if pilot and pilot.client:
            # Get all running containers
            running_containers = pilot.client.containers.list(filters={'status': 'running'})
            
            # Extract unique service names from running containers
            running_services = set()
            for container in running_containers:
                # Get image name (without tag)
                container_image = container.image.tags[0] if container.image.tags else container.image.id
                # Extract service name (part before first slash or colon)
                service_name = container_image.split('/')[-1].split(':')[0].lower()
                running_services.add(service_name)
            
            app.logger.debug(f"Detected running services from containers: {sorted(running_services)}")
            
            # Build dynamic non-HTTP services list from running containers
            # Check if any running container matches known non-HTTP patterns
            non_http_keywords = ['ssh', 'redis', 'mariadb', 'mysql', 'postgres', 'postgresql', 
                                'mongo', 'mongodb', 'db2', 'memcached', 'rabbitmq', 'kafka', 
                                'zookeeper', 'minikube', 'kicbase', 'kubernetes', 'k8s', 'kind', 
                                'k3s', 'k3d']
            
            # Check if image matches any non-HTTP service pattern
            for keyword in non_http_keywords:
                if keyword in image_lower:
                    # Also check if this service is actually running
                    if any(keyword in service for service in running_services):
                        app.logger.info(f"Detected non-HTTP service '{keyword}' from running containers")
                        return None
            
            # Build dynamic endpoint mappings from running containers
            # Common patterns based on service names found in running containers
            endpoint_mappings = {
                'homeassistant': '/',
                'home-assistant': '/',
                'glances': '/',
                'grafana': '/api/health',
                'qdrant': '/healthz',
                'ollama': '/api/version',
                'prometheus': '/-/healthy',
                'influxdb': '/ready',
                'nextcloud': '/status.php',
                'elasticsearch': '/_cluster/health',
                'nginx': '/',
                'apache': '/',
                'traefik': '/ping',
                'portainer': '/api/status'
            }
            
            # Check if any running service matches our image
            for service_name in running_services:
                if service_name in image_lower:
                    # Try to find endpoint mapping for this service
                    for pattern, endpoint in endpoint_mappings.items():
                        if pattern in service_name or service_name in pattern:
                            app.logger.info(f"Detected service '{service_name}' from running containers -> endpoint '{endpoint}'")
                            return endpoint
            
            # If service is running but no specific mapping, check generic patterns
            for pattern, endpoint in endpoint_mappings.items():
                if pattern in image_lower:
                    app.logger.info(f"Detected image pattern '{pattern}' -> endpoint '{endpoint}'")
                    return endpoint
            
    except Exception as e:
        app.logger.debug(f"Could not get running containers for dynamic detection: {e}")
    
    # Final fallback: check for non-HTTP services even if we couldn't get containers
    non_http_keywords = ['ssh', 'redis', 'mariadb', 'mysql', 'postgres', 'mongo', 'db2']
    if any(keyword in image_lower for keyword in non_http_keywords):
        return None
    
    # Default endpoint for HTTP services
    return '/health'


def _detect_health_check_endpoint(image_tag: str) -> str:
    """Detect appropriate health check endpoint based on image name
    
    Uses DockerPilot's centralized health check detection with JSON config.
    Falls back to dynamic detection from running containers.
    
    Args:
        image_tag: Docker image tag (e.g., 'qdrant/qdrant:latest', 'ollama/ollama:latest')
        
    Returns:
        Health check endpoint path, or None for non-HTTP services
    """
    try:
        # Use DockerPilot's centralized function (loads from health-checks-defaults.json)
        pilot = get_dockerpilot()
        return pilot._detect_health_check_endpoint(image_tag)
    except Exception as e:
        app.logger.warning(f"Could not use pilot health check detection: {e}, using dynamic fallback")
        # Fallback: dynamically detect services from running containers
        return _detect_health_check_endpoint_from_containers(image_tag)


def _extract_port_from_string(value: str):
    """Extract valid TCP/UDP port number from text value."""
    return _svc_extract_port_from_string(value)


def _infer_port_mapping_for_host_network(attrs: dict, image_tag: str = "") -> dict:
    """Infer minimal port mapping for host-network containers."""
    return _svc_infer_port_mapping_for_host_network(attrs, image_tag)


def get_or_create_deployment_dir(container_name: str, image_tag: str = None, deployment_id: str = None) -> Path:
    """Get or create deployment directory with unique identifier
    
    Returns:
        Path to deployment directory
    """
    deployments_dir = app.config['DEPLOYMENTS_DIR']
    
    if deployment_id:
        # Use provided deployment ID
        deployment_dir = deployments_dir / deployment_id
    else:
        # Find existing deployment for this container or create new
        deployment_dir = find_active_deployment_dir(container_name)
        
        if not deployment_dir:
            # Create new deployment directory
            deployment_id = generate_deployment_id(container_name, image_tag)
            deployment_dir = deployments_dir / deployment_id
            deployment_dir.mkdir(exist_ok=True, parents=True)
            
            # Create metadata file
            metadata = {
                'container_name': container_name,
                'image_tag': image_tag,
                'created_at': datetime.now().isoformat(),
                'deployment_id': deployment_id
            }
            metadata_path = deployment_dir / 'metadata.json'
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
    
    return deployment_dir

def find_active_deployment_dir(container_name: str) -> Path:
    """Find active deployment directory for container
    
    Returns:
        Path to deployment directory or None if not found
    """
    deployments_dir = app.config['DEPLOYMENTS_DIR']
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', container_name.lower())
    
    # Look for directories matching container name pattern
    if not deployments_dir.exists():
        return None
    
    # Find most recent deployment for this container
    matching_dirs = []
    for deployment_dir in deployments_dir.iterdir():
        if not deployment_dir.is_dir():
            continue
        
        # Check if directory name starts with container name
        if deployment_dir.name.startswith(safe_name + '_'):
            metadata_path = deployment_dir / 'metadata.json'
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        if metadata.get('container_name', '').lower() == container_name.lower():
                            matching_dirs.append((deployment_dir, metadata.get('created_at', '')))
                except:
                    pass
    
    # Return most recent deployment
    if matching_dirs:
        matching_dirs.sort(key=lambda x: x[1], reverse=True)
        return matching_dirs[0][0]
    
    return None

def find_all_deployment_dirs(container_name: str = None) -> list:
    """Find all deployment directories, optionally filtered by container name
    
    Returns:
        List of tuples (deployment_dir, metadata)
    """
    deployments_dir = app.config['DEPLOYMENTS_DIR']
    deployments = []
    
    if not deployments_dir.exists():
        return deployments
    
    for deployment_dir in deployments_dir.iterdir():
        if not deployment_dir.is_dir():
            continue
        
        metadata_path = deployment_dir / 'metadata.json'
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                    if not container_name or metadata.get('container_name', '').lower() == container_name.lower():
                        deployments.append((deployment_dir, metadata))
            except:
                pass
    
    # Sort by creation date, most recent first
    deployments.sort(key=lambda x: x[1].get('created_at', ''), reverse=True)
    return deployments

def save_deployment_config(container_name: str, config: dict, env: str = None, image_tag: str = None) -> Path:
    """Save deployment configuration to unique deployment directory
    
    Args:
        container_name: Name of the container
        config: Deployment configuration dict
        env: Environment name (dev/staging/prod) - if None, saves as deployment.yml
        image_tag: Image tag for deployment
    
    Returns:
        Path to saved config file
    """
    deployment_dir = get_or_create_deployment_dir(container_name, image_tag)
    
    if env:
        config_filename = f'deployment-{env}.yml'
    else:
        config_filename = 'deployment.yml'
    
    config_path = deployment_dir / config_filename
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    
    # Update metadata
    metadata_path = deployment_dir / 'metadata.json'
    if metadata_path.exists():
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {
            'container_name': container_name,
            'image_tag': image_tag,
            'created_at': datetime.now().isoformat(),
            'deployment_id': deployment_dir.name
        }
    
    metadata['last_updated'] = datetime.now().isoformat()
    if env:
        metadata[f'env_{env}_config'] = str(config_path)
    
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)
    
    return config_path

def load_deployment_config(container_name: str, env: str = None) -> dict:
    """Load deployment configuration from deployment directory
    
    Args:
        container_name: Name of the container
        env: Environment name (dev/staging/prod) - if None, loads deployment.yml
    
    Returns:
        Deployment configuration dict or None if not found
    """
    deployment_dir = find_active_deployment_dir(container_name)
    if not deployment_dir:
        return None
    
    if env:
        config_filename = f'deployment-{env}.yml'
    else:
        config_filename = 'deployment.yml'
    
    config_path = deployment_dir / config_filename
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except:
        return None

app = Flask(__name__, static_folder='../frontend/build', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get(
    'SESSION_COOKIE_SECURE',
    'true' if os.environ.get('FLASK_ENV') == 'production' else 'false'
).lower() == 'true'

APP_SESSION_IDLE_MINUTES = int(os.environ.get('APP_SESSION_IDLE_MINUTES', '45'))
WEB_AUTH_ENABLED = os.environ.get('WEB_AUTH_ENABLED', 'false').lower() == 'true'
WEB_AUTH_USERNAME = os.environ.get('WEB_AUTH_USERNAME', 'admin')
WEB_AUTH_PASSWORD = os.environ.get('WEB_AUTH_PASSWORD', 'admin')
WEB_AUTH_PASSWORD_HASH = os.environ.get('WEB_AUTH_PASSWORD_HASH', '')
WEB_AUTH_TOTP_SECRET = os.environ.get('WEB_AUTH_TOTP_SECRET', '').strip()
WEB_AUTH_TOTP_WINDOW = int(os.environ.get('WEB_AUTH_TOTP_WINDOW', '1'))

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=APP_SESSION_IDLE_MINUTES)

# Short-lived elevation token settings (for privileged operations)
ELEVATION_TOKEN_TTL_SECONDS = int(os.environ.get('ELEVATION_TOKEN_TTL_SECONDS', '120'))
ELEVATION_TOKEN_MAX_TTL_SECONDS = int(os.environ.get('ELEVATION_TOKEN_MAX_TTL_SECONDS', '600'))
ELEVATION_TOKEN_MAX_PER_SESSION = int(os.environ.get('ELEVATION_TOKEN_MAX_PER_SESSION', '16'))

cors_origins_env = os.environ.get('CORS_ORIGINS')
if cors_origins_env:
    cors_origins = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
else:
    cors_origins = [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:5000',
        'http://127.0.0.1:5000'
    ]

CORS(app, supports_credentials=True, origins=cors_origins)
api = Api(app)

if WEB_AUTH_ENABLED and not WEB_AUTH_PASSWORD_HASH and WEB_AUTH_PASSWORD == 'admin':
    app.logger.warning(
        "WEB_AUTH is enabled with default credentials (admin/admin). "
        "Set WEB_AUTH_PASSWORD_HASH or WEB_AUTH_PASSWORD in production."
    )


def _parse_pbkdf2_hash(password_hash: str):
    """Parse pbkdf2 hash in format: pbkdf2_sha256$iterations$salt$hexdigest."""
    if not password_hash:
        return None
    parts = password_hash.split('$')
    if len(parts) != 4 or parts[0] != 'pbkdf2_sha256':
        return None
    try:
        iterations = int(parts[1])
    except (TypeError, ValueError):
        return None
    return {
        'iterations': iterations,
        'salt': parts[2],
        'hexdigest': parts[3],
    }


def _verify_password(candidate_password: str) -> bool:
    """Verify web-panel password (PBKDF2 hash preferred, plain fallback for compatibility)."""
    candidate = candidate_password or ''
    parsed = _parse_pbkdf2_hash(WEB_AUTH_PASSWORD_HASH)
    if parsed:
        dk = hashlib.pbkdf2_hmac(
            'sha256',
            candidate.encode('utf-8'),
            parsed['salt'].encode('utf-8'),
            parsed['iterations'],
        )
        computed = dk.hex()
        return hmac.compare_digest(computed, parsed['hexdigest'])
    return hmac.compare_digest(candidate, WEB_AUTH_PASSWORD)


def _normalize_totp_secret(secret: str) -> str:
    return (secret or '').replace(' ', '').strip().upper()


def _verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    """Validate RFC6238 TOTP code (Google/Microsoft Authenticator compatible)."""
    normalized_secret = _normalize_totp_secret(secret)
    if not normalized_secret:
        return True
    token = (code or '').strip()
    if not token.isdigit() or len(token) not in {6, 8}:
        return False
    digits = len(token)
    try:
        secret_bytes = base64.b32decode(normalized_secret, casefold=True)
    except (binascii.Error, ValueError):
        return False

    now_counter = int(time.time() // 30)
    for offset in range(-max(0, window), max(0, window) + 1):
        counter = now_counter + offset
        if counter < 0:
            continue
        msg = struct.pack(">Q", counter)
        digest = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
        idx = digest[-1] & 0x0F
        truncated = digest[idx:idx + 4]
        otp_int = struct.unpack(">I", truncated)[0] & 0x7FFFFFFF
        expected = str(otp_int % (10 ** digits)).zfill(digits)
        if hmac.compare_digest(expected, token):
            return True
    return False


def _clear_auth_session():
    for key in (
        'auth_authenticated',
        'auth_username',
        'auth_last_activity_ts',
        'auth_mfa_verified',
    ):
        session.pop(key, None)
    _revoke_elevation_tokens_for_current_session()


def _is_authenticated_session() -> bool:
    if not WEB_AUTH_ENABLED:
        return True
    if not session.get('auth_authenticated'):
        return False
    username = session.get('auth_username')
    if username != WEB_AUTH_USERNAME:
        return False
    last_ts = session.get('auth_last_activity_ts')
    if last_ts is None:
        return False
    try:
        last_ts = float(last_ts)
    except (TypeError, ValueError):
        return False

    idle_seconds = max(1, APP_SESSION_IDLE_MINUTES) * 60
    now_ts = time.time()
    if now_ts - last_ts > idle_seconds:
        _clear_auth_session()
        return False

    # Touch activity for sliding inactivity window.
    session['auth_last_activity_ts'] = now_ts
    session.permanent = True
    return True


def _auth_status_payload():
    authed = _is_authenticated_session()
    last_ts = session.get('auth_last_activity_ts')
    expires_in = None
    if authed and last_ts is not None:
        try:
            remaining = max(0, int(max(1, APP_SESSION_IDLE_MINUTES) * 60 - (time.time() - float(last_ts))))
            expires_in = remaining
        except (TypeError, ValueError):
            expires_in = None

    return {
        'success': True,
        'auth_enabled': WEB_AUTH_ENABLED,
        'authenticated': bool(authed),
        'username': session.get('auth_username') if authed else None,
        'mfa_required': bool(WEB_AUTH_TOTP_SECRET),
        'session_idle_minutes': APP_SESSION_IDLE_MINUTES,
        'session_expires_in_seconds': expires_in,
    }


PUBLIC_API_PATHS = {
    '/api/health',
    '/api/auth/login',
    '/api/auth/logout',
    '/api/auth/status',
}


@app.before_request
def require_auth_for_api():
    """Require authenticated app session for API endpoints (except public/auth endpoints)."""
    if not WEB_AUTH_ENABLED:
        return None
    if request.method == 'OPTIONS':
        return None

    path = request.path or ''
    if not path.startswith('/api/'):
        return None
    if path in PUBLIC_API_PATHS:
        return None

    if not _is_authenticated_session():
        return jsonify({
            'success': False,
            'error': 'Authentication required',
            'auth_required': True,
        }), 401
    return None

# Configuration
app.config['CONFIG_DIR'] = Path.home() / ".dockerpilot_extras"
app.config['CONFIG_DIR'].mkdir(exist_ok=True)
app.config['PIPELINES_DIR'] = app.config['CONFIG_DIR'] / "pipelines"
app.config['PIPELINES_DIR'].mkdir(exist_ok=True)
app.config['DEPLOYMENTS_DIR'] = app.config['CONFIG_DIR'] / "deployments"
app.config['DEPLOYMENTS_DIR'].mkdir(exist_ok=True)
app.config['SERVERS_DIR'] = app.config['CONFIG_DIR'] / "servers"
app.config['SERVERS_DIR'].mkdir(exist_ok=True)

_storage_runtime_config = {}
_state_store = None
_storage_init_warning = None


def _build_file_store() -> FileStateStore:
    return FileStateStore(
        config_dir=app.config['CONFIG_DIR'],
        servers_dir=app.config['SERVERS_DIR'],
    )


def init_state_store(runtime_config: dict = None) -> tuple:
    """Initialize storage backend and fallback to file mode on errors."""
    global _state_store, _storage_runtime_config, _storage_init_warning
    cfg = runtime_config or resolve_storage_config(app.config['CONFIG_DIR'])
    try:
        _state_store = create_store(
            config_dir=app.config['CONFIG_DIR'],
            servers_dir=app.config['SERVERS_DIR'],
            resolved_cfg=cfg,
        )
        _storage_runtime_config = cfg
        _storage_init_warning = None
        return True, None
    except Exception as exc:
        _storage_init_warning = str(exc)
        app.logger.error(
            f"Failed to initialize storage backend '{cfg.get('backend', 'unknown')}': {exc}. "
            "Falling back to file storage."
        )
        fallback_cfg = {"backend": "file", "postgres": {}}
        _state_store = _build_file_store()
        _storage_runtime_config = fallback_cfg
        return False, str(exc)


def get_state_store():
    if _state_store is None:
        init_state_store()
    return _state_store


def get_storage_status() -> dict:
    store = get_state_store()
    mode = getattr(store, "mode", "file")
    healthy = False
    schema_version = None
    error = None
    try:
        healthy = bool(store.is_healthy())
        schema_version = store.schema_version()
    except Exception as exc:
        error = str(exc)
    return {
        "backend": mode,
        "healthy": healthy,
        "schema_version": schema_version,
        "warning": _storage_init_warning,
        "error": error,
        "config": {
            "backend": _storage_runtime_config.get("backend", "file"),
            "postgres": sanitize_postgres_config(_storage_runtime_config.get("postgres", {})),
        },
    }


init_state_store()

# DockerPilot instance cache (per server)
_dockerpilot_instances = {}  # {server_id: DockerPilotEnhanced instance}
_current_server_id = None

# Global progress tracking for deployments
_deployment_progress = {}  # {container_name: {'stage': str, 'progress': int, 'message': str, 'timestamp': datetime}}

# Global progress tracking for migrations
_migration_progress = {}  # {container_name: {'stage': str, 'progress': int, 'message': str, 'timestamp': datetime}}
_migration_cancel_flags = {}  # {container_name: bool} - flags to cancel migrations

def create_docker_client_for_server(server_config):
    """Create Docker client for remote server via SSH"""
    if not SSH_AVAILABLE:
        raise ImportError("SSH libraries not available")
    
    try:
        import docker
        from paramiko import SSHClient, AutoAddPolicy
        import base64
        from io import StringIO
        
        hostname = server_config.get('hostname')
        port = server_config.get('port', 22)
        username = server_config.get('username')
        auth_type = server_config.get('auth_type', 'password')
        
        # Build SSH connection parameters
        ssh_kwargs = {
            'hostname': hostname,
            'port': port,
            'username': username,
            'timeout': 10
        }
        
        # Add authentication
        if auth_type == 'password':
            ssh_kwargs['password'] = server_config.get('password')
        elif auth_type == 'key':
            key_content = server_config.get('private_key')
            key_passphrase = server_config.get('key_passphrase')
            if not key_content:
                raise ValueError('Private key required for key authentication')
            
            # Load private key
            key_file = StringIO(key_content)
            try:
                key = paramiko.RSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            except:
                try:
                    key_file.seek(0)
                    key = paramiko.DSSKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
                except:
                    key_file.seek(0)
                    key = paramiko.ECDSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            
            ssh_kwargs['pkey'] = key
        else:
            raise ValueError(f'Unsupported auth type: {auth_type}')
        
        # Create SSH URL for Docker
        # Docker SDK supports ssh:// URL format
        ssh_url = f"ssh://{username}@{hostname}:{port}"
        
        # Create Docker client with SSH transport
        # Note: Docker SDK's use_ssh_client option requires SSH agent or key file path
        # For password auth or key from memory, we need to use SSH tunnel or socket forwarding
        
        # Alternative approach: Use SSH socket forwarding
        # This creates an SSH connection and forwards the Docker socket
        
        # For now, use base_url with SSH (if supported) or create socket forwarding
        # Docker SDK has limited SSH support, so we'll use subprocess to forward socket
        
        # Simple approach: Use docker CLI with SSH context
        # But DockerPilot uses docker SDK, so we need to provide base_url
        
        # Actually, Docker SDK Python doesn't directly support SSH URLs
        # We need to either:
        # 1. Use SSH tunnel (port forwarding)
        # 2. Use docker context with SSH
        # 3. Execute docker commands over SSH
        
        # For simplicity, let's use SSH to execute docker commands
        # But DockerPilot uses docker SDK, so we need a wrapper
        
        # Best approach: Create a Docker client that executes commands over SSH
        # This requires custom implementation or using docker-py with base_url
        
        # Temporary solution: Return None and handle in DockerPilot
        # Or use docker context API
        
        # Actually, we can use docker's SSH context feature (Docker 18.09+)
        # Create docker context with SSH
        import subprocess
        import tempfile
        import os
        
        # Create temporary SSH config for docker context
        context_name = f"dockerpilot-{hostname}-{port}"
        
        # Use docker context create with SSH
        # docker context create --docker "host=ssh://user@host" context_name
        
        # For now, let's create a simple SSH-based docker client wrapper
        # that executes docker commands over SSH
        
        # However, DockerPilotEnhanced expects a docker client object
        # So we need to either:
        # 1. Modify DockerPilot to accept SSH parameters
        # 2. Create a proxy docker client that executes over SSH
        # 3. Use docker socket forwarding
        
        # Simplest: Use SSH to forward Docker socket locally, then connect to forwarded socket
        # This requires creating an SSH tunnel
        
        raise NotImplementedError("SSH Docker connection requires socket forwarding or Docker context. Will implement basic version.")
        
    except Exception as e:
        app.logger.error(f"Failed to create Docker client for server: {e}")
        raise

def get_dockerpilot():
    """Get or create DockerPilot instance for current server
    
    Note: Signal handlers are skipped in Flask context as they only work
    in the main thread of the main interpreter.
    """
    global _dockerpilot_instances, _current_server_id
    
    # Get selected server from session
    selected_server_id = session.get('selected_server', 'local')
    
    # If server changed, we might need a new instance
    # But for now, let's support per-server caching
    server_id = selected_server_id
    
    # Check if we have instance for this server
    if server_id in _dockerpilot_instances:
        return _dockerpilot_instances[server_id]
    
    # Create new instance
    config_path = app.config['CONFIG_DIR'] / 'deployment.yml'
    config_path_str = str(config_path) if config_path.exists() else None
    
    # For remote servers, we need to configure Docker client for SSH
    docker_client = None
    if server_id != 'local':
        # Load server config
        config = load_servers_config()
        server_config = None
        for server in config.get('servers', []):
            if server.get('id') == server_id:
                server_config = server
                break
        
        if server_config:
            try:
                # Try to create Docker client for remote server
                # For now, we'll use SSH to execute docker commands
                # This requires modifying how DockerPilot works, or using a wrapper
                app.logger.warning(f"Remote server {server_id} selected, but Docker over SSH not fully implemented yet. Using local Docker.")
                # docker_client = create_docker_client_for_server(server_config)
            except Exception as e:
                app.logger.error(f"Failed to create Docker client for remote server: {e}")
                # Fall back to local
                server_id = 'local'
    
    # Temporarily patch signal.signal to avoid errors in Flask threads
    import signal
    original_signal = signal.signal
    
    def safe_signal(signum, handler):
        """Safe signal handler that only works in main thread"""
        try:
            return original_signal(signum, handler)
        except ValueError:
            # Signal handlers only work in main thread
            # In Flask context, we skip them
            pass
    
    # Patch signal.signal during initialization
    signal.signal = safe_signal
    
    try:
        instance = DockerPilotEnhanced(
            config_file=config_path_str,
            log_level=LogLevel.INFO
        )
        
        # If we have a remote docker client, we could set it here
        # But DockerPilotEnhanced doesn't expose client setter easily
        # For now, we'll use subprocess-based approach for remote servers
        
        # Store instance
        _dockerpilot_instances[server_id] = instance
        _current_server_id = server_id
        
    except Exception as e:
        # If initialization fails, log error but don't crash
        # The instance will be None and we'll handle it in endpoints
        import logging
        logging.error(f"Failed to initialize DockerPilot: {e}")
        raise
    finally:
        # Restore original signal.signal
        signal.signal = original_signal
            
    return instance

def execute_command_via_ssh(server_config, command, check_exit_status=True):
    """Execute any command on remote server via SSH (or local if server_config is None or id == 'local')"""
    # Handle local server
    if server_config is None or server_config.get('id') == 'local':
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            if check_exit_status and result.returncode != 0:
                raise Exception(f"Command failed (exit {result.returncode}): {result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            raise Exception(f"Command timeout: {command}")
        except Exception as e:
            raise Exception(f"Command failed: {str(e)}")
    
    if not SSH_AVAILABLE:
        raise ImportError("SSH libraries not available")
    
    try:
        import paramiko
        from io import StringIO
        
        hostname = server_config.get('hostname')
        port = server_config.get('port', 22)
        username = server_config.get('username')
        auth_type = server_config.get('auth_type', 'password')
        
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Prepare authentication
        if auth_type == 'password':
            ssh.connect(hostname, port=port, username=username, password=server_config.get('password'), timeout=10)
        elif auth_type == 'key':
            key_content = server_config.get('private_key')
            key_passphrase = server_config.get('key_passphrase')
            if not key_content:
                raise ValueError('Private key required for key authentication')
            
            # Load private key
            key_file = StringIO(key_content)
            try:
                key = paramiko.RSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            except:
                try:
                    key_file.seek(0)
                    key = paramiko.DSSKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
                except:
                    key_file.seek(0)
                    key = paramiko.ECDSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            
            ssh.connect(hostname, port=port, username=username, pkey=key, timeout=10)
        elif auth_type == '2fa':
            password = server_config.get('password')
            totp_code = server_config.get('totp_code', '')
            # For 2FA, typically password + code
            ssh.connect(hostname, port=port, username=username, password=password + totp_code, timeout=10)
        else:
            raise ValueError(f'Unsupported auth type: {auth_type}')
        
        # Execute command
        stdin, stdout, stderr = ssh.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8')
        error_output = stderr.read().decode('utf-8')
        
        ssh.close()
        
        if check_exit_status and exit_status != 0:
            raise Exception(f"Command failed (exit {exit_status}): {error_output}")
        
        return output
        
    except Exception as e:
        app.logger.error(f"Failed to execute command via SSH: {e}")
        raise

# Cache for sudo requirements per server
_docker_sudo_cache = {}
_elevation_tokens = {}
_elevation_tokens_lock = threading.Lock()


def _get_or_create_elevation_session_id() -> str:
    """Get stable session-scoped ID for token binding."""
    session_id = session.get('elevation_session_id')
    if not session_id:
        session_id = secrets.token_hex(16)
        session['elevation_session_id'] = session_id
        session.permanent = True
    return session_id


def _cleanup_expired_elevation_tokens() -> int:
    """Cleanup expired tokens from in-memory cache."""
    now_ts = time.time()
    removed = 0
    with _elevation_tokens_lock:
        expired = [key for key, entry in _elevation_tokens.items() if entry.get('expires_at_ts', 0) <= now_ts]
        for key in expired:
            _elevation_tokens.pop(key, None)
            removed += 1
    return removed


def _issue_elevation_token(sudo_password: str, scope: dict = None, ttl_seconds: int = None) -> dict:
    """Issue a one-time elevation token bound to current web session."""
    if not sudo_password:
        raise ValueError('sudo_password is required')

    _cleanup_expired_elevation_tokens()
    session_id = _get_or_create_elevation_session_id()
    requested_ttl = int(ttl_seconds if ttl_seconds is not None else ELEVATION_TOKEN_TTL_SECONDS)
    effective_ttl = max(30, min(requested_ttl, ELEVATION_TOKEN_MAX_TTL_SECONDS))
    issued_at = datetime.now()
    expires_at = issued_at + timedelta(seconds=effective_ttl)
    token_plain = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token_plain.encode('utf-8')).hexdigest()
    scope_data = scope if isinstance(scope, dict) else {}

    entry = {
        'session_id': session_id,
        'sudo_password': sudo_password,
        'scope': scope_data,
        'issued_at_iso': issued_at.isoformat(),
        'expires_at_iso': expires_at.isoformat(),
        'expires_at_ts': expires_at.timestamp(),
    }

    with _elevation_tokens_lock:
        # Limit token count per session to reduce stale privileged material in memory.
        session_keys = [
            key for key, value in _elevation_tokens.items()
            if value.get('session_id') == session_id
        ]
        if len(session_keys) >= ELEVATION_TOKEN_MAX_PER_SESSION:
            session_keys.sort(key=lambda key: _elevation_tokens.get(key, {}).get('expires_at_ts', 0))
            for key in session_keys[: len(session_keys) - ELEVATION_TOKEN_MAX_PER_SESSION + 1]:
                _elevation_tokens.pop(key, None)

        _elevation_tokens[token_hash] = entry

    return {
        'token': token_plain,
        'expires_in': effective_ttl,
        'expires_at': entry['expires_at_iso'],
        'scope': scope_data,
    }


def _consume_elevation_token(token: str, expected_action: str = None, expected_scope: dict = None) -> tuple:
    """Validate and consume one-time elevation token."""
    if not token or not isinstance(token, str):
        return False, 'Missing elevation token', None

    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    current_session_id = session.get('elevation_session_id')
    now_ts = time.time()

    with _elevation_tokens_lock:
        entry = _elevation_tokens.get(token_hash)
        if not entry:
            return False, 'Invalid or expired elevation token', None

        if entry.get('expires_at_ts', 0) <= now_ts:
            _elevation_tokens.pop(token_hash, None)
            return False, 'Elevation token expired', None

        if not current_session_id or entry.get('session_id') != current_session_id:
            return False, 'Elevation token does not match active session', None

        scope = entry.get('scope') or {}
        if expected_action and scope.get('action') != expected_action:
            return False, 'Elevation token scope mismatch (action)', None

        if isinstance(expected_scope, dict):
            for key, value in expected_scope.items():
                if value is None:
                    continue
                if scope.get(key) != value:
                    return False, f"Elevation token scope mismatch ({key})", None

        sudo_password = entry.get('sudo_password')
        _elevation_tokens.pop(token_hash, None)

    return True, 'ok', sudo_password


def _revoke_elevation_tokens_for_current_session() -> int:
    """Revoke all elevation tokens bound to current session."""
    current_session_id = session.get('elevation_session_id')
    if not current_session_id:
        return 0
    removed = 0
    with _elevation_tokens_lock:
        to_delete = [
            key for key, value in _elevation_tokens.items()
            if value.get('session_id') == current_session_id
        ]
        for key in to_delete:
            _elevation_tokens.pop(key, None)
            removed += 1
    return removed

def _check_docker_sudo_required(server_config):
    """Check if docker commands require sudo on the server (with caching)"""
    # Use server ID or hostname as cache key
    cache_key = server_config.get('id') or server_config.get('hostname', 'unknown')
    
    # Check cache first
    if cache_key in _docker_sudo_cache:
        return _docker_sudo_cache[cache_key]
    
    # For local server, assume no sudo needed (user should have docker group access)
    if server_config is None or server_config.get('id') == 'local':
        _docker_sudo_cache[cache_key] = False
        return False
    
    try:
        # Try to run docker ps without sudo
        result = execute_command_via_ssh(server_config, "docker ps", check_exit_status=False)
        # If it works, no sudo needed
        _docker_sudo_cache[cache_key] = False
        return False
    except Exception as e:
        # If it fails with permission error, sudo is likely required
        error_msg = str(e).lower()
        if 'permission denied' in error_msg or 'cannot connect' in error_msg or 'permission' in error_msg:
            _docker_sudo_cache[cache_key] = True
            return True
        # For other errors, assume no sudo needed (might be other issues like docker not running)
        _docker_sudo_cache[cache_key] = False
        return False

def execute_docker_command_via_ssh(server_config, docker_command, check_exit_status=True, use_sudo=None, return_stderr=False):
    """Execute docker command on remote server via SSH
    
    Args:
        server_config: Server configuration
        docker_command: Docker command to execute (without 'docker' prefix)
        check_exit_status: Whether to raise exception on non-zero exit code
        use_sudo: Whether to use sudo (None = auto-detect, True/False = force)
        return_stderr: If True, return tuple (stdout, stderr), otherwise just stdout
    
    Returns:
        stdout string, or (stdout, stderr) tuple if return_stderr=True
    """
    # Auto-detect sudo requirement if not specified
    if use_sudo is None:
        use_sudo = _check_docker_sudo_required(server_config)
    
    # Build command with or without sudo
    if use_sudo:
        # Use sudo docker - need to handle password if required
        # For now, assume passwordless sudo is configured or user is in docker group
        command = f"sudo docker {docker_command}"
    else:
        command = f"docker {docker_command}"
    
    # For docker load, we need to check stderr too (docker load outputs to stderr)
    if return_stderr or 'load' in docker_command:
        return _execute_command_via_ssh_with_stderr(server_config, command, check_exit_status=check_exit_status)
    else:
        return execute_command_via_ssh(server_config, command, check_exit_status=check_exit_status)

def _execute_command_via_ssh_with_stderr(server_config, command, check_exit_status=True):
    """Execute command via SSH and return both stdout and stderr"""
    # Handle local server
    if server_config is None or server_config.get('id') == 'local':
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            if check_exit_status and result.returncode != 0:
                raise Exception(f"Command failed (exit {result.returncode}): {result.stderr}")
            return result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            raise Exception(f"Command timeout: {command}")
        except Exception as e:
            raise Exception(f"Command failed: {str(e)}")
    
    if not SSH_AVAILABLE:
        raise ImportError("SSH libraries not available")
    
    try:
        import paramiko
        from io import StringIO
        
        hostname = server_config.get('hostname')
        port = server_config.get('port', 22)
        username = server_config.get('username')
        auth_type = server_config.get('auth_type', 'password')
        
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Prepare authentication (same as execute_command_via_ssh)
        if auth_type == 'password':
            ssh.connect(hostname, port=port, username=username, password=server_config.get('password'), timeout=10)
        elif auth_type == 'key':
            key_content = server_config.get('private_key')
            key_passphrase = server_config.get('key_passphrase')
            if not key_content:
                raise ValueError('Private key required for key authentication')
            
            key_file = StringIO(key_content)
            try:
                key = paramiko.RSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            except:
                try:
                    key_file.seek(0)
                    key = paramiko.DSSKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
                except:
                    key_file.seek(0)
                    key = paramiko.ECDSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            
            ssh.connect(hostname, port=port, username=username, pkey=key, timeout=10)
        elif auth_type == '2fa':
            password = server_config.get('password')
            totp_code = server_config.get('totp_code', '')
            ssh.connect(hostname, port=port, username=username, password=password + totp_code, timeout=10)
        else:
            raise ValueError(f'Unsupported auth type: {auth_type}')
        
        # Execute command
        stdin, stdout, stderr = ssh.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8')
        error_output = stderr.read().decode('utf-8')
        
        ssh.close()
        
        if check_exit_status and exit_status != 0:
            raise Exception(f"Command failed (exit {exit_status}): {error_output}")
        
        return output, error_output
        
    except Exception as e:
        app.logger.error(f"Failed to execute command via SSH: {e}")
        raise

def get_selected_server_config():
    """Get configuration for currently selected server"""
    try:
        selected_server_id = session.get('selected_server', 'local')
        app.logger.debug(f"Selected server ID from session: {selected_server_id}")
        
        if selected_server_id == 'local':
            return None
        
        config = load_servers_config()
        for server in config.get('servers', []):
            if server.get('id') == selected_server_id:
                app.logger.info(f"Found server config for {selected_server_id}: {server.get('hostname')}")
                return server
        
        app.logger.warning(f"Server {selected_server_id} not found in config, falling back to local")
        return None
    except Exception as e:
        app.logger.error(f"Error getting selected server config: {e}", exc_info=True)
        return None


def find_all_deployment_configs_for_env(env: str) -> list:
    """Find all deployment configs for a given environment across all deployment directories.
    
    Args:
        env: Environment name (dev/staging/prod)
    
    Returns:
        List of dicts with 'path' and 'container_name' keys
    """
    configs = []
    deployments_dir = Path.home() / '.dockerpilot_extras' / 'deployments'
    
    if not deployments_dir.exists():
        return configs
    
    for deployment_dir in deployments_dir.iterdir():
        if deployment_dir.is_dir():
            config_path = deployment_dir / f'deployment-{env}.yml'
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_content = yaml.safe_load(f)
                        container_name = config_content.get('deployment', {}).get('container_name', deployment_dir.name.split('_')[0])
                        configs.append({
                            'path': str(config_path),
                            'container_name': container_name
                        })
                except Exception as e:
                    app.logger.warning(f"Failed to load {config_path}: {e}")
    
    return configs


def get_env_servers_config_path() -> Path:
    """Get path to environment->server mapping file."""
    return app.config['CONFIG_DIR'] / 'environments.json'


def load_env_servers_config() -> dict:
    """Load environment->server mapping via configured state store."""
    try:
        config = get_state_store().load_env_servers_config() or {}
        config.setdefault("env_servers", {})
        return config
    except Exception as e:
        app.logger.error(f"Failed to load environments config: {e}")
        return {"env_servers": {}}


def save_env_servers_config(config: dict) -> bool:
    """Save environment->server mapping via configured state store."""
    try:
        return bool(get_state_store().save_env_servers_config(config))
    except Exception as e:
        app.logger.error(f"Failed to save environments config: {e}")
        return False


def get_deployment_history_data(limit: int = 50) -> list:
    """Load deployment history via configured state store."""
    try:
        return list(get_state_store().get_deployment_history(limit=limit))
    except TypeError:
        # File store compatibility (older signature without limit)
        try:
            history = list(get_state_store().get_deployment_history())
            return history[-limit:]
        except Exception as e:
            app.logger.error(f"Failed to load deployment history: {e}")
            return []
    except Exception as e:
        app.logger.error(f"Failed to load deployment history: {e}")
        return []


def append_deployment_history_data(entry: dict, max_entries: int = 50) -> bool:
    """Append deployment history entry via configured state store."""
    try:
        return bool(get_state_store().append_deployment_history(entry, max_entries=max_entries))
    except TypeError:
        # File store compatibility for older method signatures.
        try:
            history = get_deployment_history_data(limit=max_entries)
            history.append(dict(entry))
            return bool(get_state_store().replace_deployment_history(history[-max_entries:], max_entries=max_entries))
        except Exception as e:
            app.logger.error(f"Failed to append deployment history: {e}")
            return False
    except Exception as e:
        app.logger.error(f"Failed to append deployment history: {e}")
        return False


(
    PipelineGenerate,
    PipelineSave,
    PipelineDeploymentConfig,
    PipelineIntegration,
    DeploymentConfig,
    DeploymentExecute,
    DeploymentHistory,
) = create_pipeline_resources(
    Resource=Resource,
    app=app,
    request=request,
    datetime_cls=datetime,
    PipelineGenerator=PipelineGenerator,
    parse_env_vars=parse_env_vars,
    generate_deployment_config_for_environment=generate_deployment_config_for_environment,
    save_deployment_config=save_deployment_config,
    append_deployment_history_data=append_deployment_history_data,
    get_deployment_history_data=get_deployment_history_data,
)


def load_env_container_bindings() -> dict:
    """Load environment->containers bindings via configured state store."""
    try:
        config = get_state_store().load_env_container_bindings() or {}
        config.setdefault("env_containers", {})
        return config
    except Exception as e:
        app.logger.error(f"Failed to load env container bindings: {e}")
        return {"env_containers": {}}


def save_env_container_bindings(config: dict) -> bool:
    """Save environment->containers bindings via configured state store."""
    try:
        return bool(get_state_store().save_env_container_bindings(config))
    except Exception as e:
        app.logger.error(f"Failed to save env container bindings: {e}")
        return False


def _normalize_env_container_bindings(config: dict) -> dict:
    envs = ['dev', 'staging', 'prod']
    cfg = config if isinstance(config, dict) else {}
    src_map = cfg.get("env_containers", {}) if isinstance(cfg.get("env_containers", {}), dict) else {}
    normalized = {}
    for env in envs:
        values = src_map.get(env, [])
        if not isinstance(values, list):
            values = []
        deduped = []
        seen = set()
        for name in values:
            if not isinstance(name, str):
                continue
            clean = name.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            deduped.append(clean)
        normalized[env] = deduped
    return {"env_containers": normalized, "updated_at": datetime.now().isoformat()}


def move_container_binding(container_name: str, from_env: str, to_env: str) -> bool:
    if not container_name:
        return False
    data = load_env_container_bindings()
    normalized = _normalize_env_container_bindings(data)
    env_containers = normalized["env_containers"]
    for env in env_containers:
        env_containers[env] = [name for name in env_containers[env] if name != container_name]
    if to_env in env_containers:
        env_containers[to_env].append(container_name)
    saved = save_env_container_bindings(_normalize_env_container_bindings(normalized))
    if saved and '_environment_status_cache' in globals():
        _environment_status_cache["data"] = None
        _environment_status_cache["timestamp"] = None
    return saved


def move_many_container_bindings(container_names: list, from_env: str, to_env: str) -> bool:
    data = load_env_container_bindings()
    normalized = _normalize_env_container_bindings(data)
    env_containers = normalized["env_containers"]
    unique_names = []
    seen = set()
    for name in container_names or []:
        if isinstance(name, str) and name.strip() and name not in seen:
            seen.add(name)
            unique_names.append(name)
    for container_name in unique_names:
        for env in env_containers:
            env_containers[env] = [existing for existing in env_containers[env] if existing != container_name]
        if to_env in env_containers:
            env_containers[to_env].append(container_name)
    saved = save_env_container_bindings(_normalize_env_container_bindings(normalized))
    if saved and '_environment_status_cache' in globals():
        _environment_status_cache["data"] = None
        _environment_status_cache["timestamp"] = None
    return saved


def load_legacy_file_state_snapshot() -> dict:
    """Load state snapshot directly from legacy JSON files."""
    file_store = _build_file_store()
    return {
        "servers_config": file_store.load_servers_config(),
        "env_servers_config": file_store.load_env_servers_config(),
        "deployment_history": file_store.get_deployment_history(),
        "env_container_bindings": file_store.load_env_container_bindings(),
    }


def migrate_legacy_file_state_to_store(target_store) -> dict:
    """Copy legacy file state into target store. Returns migration counters."""
    snapshot = load_legacy_file_state_snapshot()
    servers_cfg = snapshot.get("servers_config", {}) or {"servers": [], "default_server": "local"}
    env_cfg = snapshot.get("env_servers_config", {}) or {"env_servers": {}}
    history = list(snapshot.get("deployment_history", []) or [])
    env_container_bindings = snapshot.get("env_container_bindings", {}) or {"env_containers": {}}

    target_store.save_servers_config(servers_cfg)
    target_store.save_env_servers_config(env_cfg)
    target_store.replace_deployment_history(history, max_entries=50)
    target_store.save_env_container_bindings(_normalize_env_container_bindings(env_container_bindings))

    return {
        "servers": len(servers_cfg.get("servers", [])),
        "env_mappings": len((env_cfg.get("env_servers") or {}).keys()),
        "history_entries": min(len(history), 50),
        "env_container_bindings": sum(
            len(v)
            for v in _normalize_env_container_bindings(env_container_bindings).get("env_containers", {}).values()
        ),
    }


def resolve_server_id_for_env(env: str) -> str:
    """Resolve which server_id should host a given environment."""
    cfg = load_env_servers_config()
    env_servers = cfg.get("env_servers", {}) if isinstance(cfg, dict) else {}
    return env_servers.get(env, "local")


def _get_containers_and_images_for_server(server_config) -> tuple:
    """Get containers and images list for one server (local or remote).

    Returns (containers, images, host_error). host_error is set when Docker inventory
    could not be read (typically SSH unreachable); omitted when data was returned.
    """
    if server_config is None:
        server_config = {'id': 'local'}
    containers = []
    images = []
    container_error = None
    image_error = None
    try:
        out = execute_docker_command_via_ssh(
            server_config,
            r"ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}'",
            check_exit_status=False
        )
        for line in (out or "").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append({
                    "name": parts[0].lstrip("/"),
                    "image": parts[1],
                    "state": parts[2].lower(),
                    "status": parts[3],
                })
    except Exception as e:
        app.logger.warning(f"Failed to get containers for server {server_config.get('id', '?')}: {e}")
        container_error = str(e)
    try:
        out = execute_docker_command_via_ssh(
            server_config,
            r"images --format '{{.Repository}}\t{{.Tag}}'",
            check_exit_status=False
        )
        for line in (out or "").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] and parts[0] != "<none>":
                tag = f"{parts[0]}:{parts[1]}"
                if tag not in images:
                    images.append(tag)
    except Exception as e:
        app.logger.warning(f"Failed to get images for server {server_config.get('id', '?')}: {e}")
        image_error = str(e)

    host_error = None
    if container_error or image_error:
        if not containers and not images:
            if container_error and image_error and container_error == image_error:
                host_error = container_error
            else:
                host_error = "; ".join(
                    msg for msg in (container_error, image_error) if msg
                )
    return containers, images, host_error


def get_server_config_by_id(server_id: str):
    """Return server config dict from servers.json by id."""
    if server_id == 'local':
        return {'id': 'local'}
    config = load_servers_config()
    for server in config.get('servers', []):
        if server.get('id') == server_id:
            return server
    return None


def _apply_env_resource_presets(deployment: dict, target_env: str) -> None:
    """Apply environment resource presets in-place (cpu/memory)."""
    env_configs = {
        'dev': {'cpu': '0.5', 'memory': '512Mi'},
        'staging': {'cpu': '1.0', 'memory': '1Gi'},
        'prod': {'cpu': '2.0', 'memory': '2Gi'},
    }
    preset = env_configs.get(target_env)
    if not preset:
        return
    deployment['cpu_limit'] = preset['cpu']
    deployment['memory_limit'] = preset['memory']


def _write_remote_file(server_config: dict, remote_path: str, content: str) -> None:
    """Write text file to remote host via SSH using base64 payload."""
    import base64
    payload = base64.b64encode(content.encode('utf-8')).decode('ascii')
    remote_dir = str(Path(remote_path).parent)
    cmd = (
        f"mkdir -p '{remote_dir}' && "
        f"python3 - <<'PY'\n"
        f"import base64, pathlib\n"
        f"p = pathlib.Path(r'''{remote_path}''')\n"
        f"p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"data = base64.b64decode(r'''{payload}'''.encode('ascii'))\n"
        f"p.write_bytes(data)\n"
        f"print('OK')\n"
        f"PY"
    )
    execute_command_via_ssh(server_config, cmd, check_exit_status=True)


def promote_config_to_server(server_id: str, config_path_str: str, from_env: str, to_env: str, skip_backup: bool = False) -> bool:
    """Promote by deploying on the target server (env -> server mapping).

    For remote servers, we SSH in, write the promoted config, and run dockerpilot there.
    """
    try:
        if not config_path_str or not Path(config_path_str).exists():
            raise FileNotFoundError(f"Config file not found: {config_path_str}")

        with open(config_path_str, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

        deployment = config.get('deployment') or {}
        if not isinstance(deployment, dict):
            raise ValueError("Invalid deployment config format (deployment must be a dict)")

        container_name = deployment.get('container_name') or Path(config_path_str).parent.name.split('_')[0]
        _apply_env_resource_presets(deployment, to_env)

        # Choose deployment strategy by environment
        deployment_type = 'blue-green' if to_env == 'prod' else 'rolling'

        # Local target: use in-process pilot (existing behavior)
        if server_id == 'local':
            pilot = get_dockerpilot()
            return bool(pilot.environment_promotion(from_env, to_env, config_path_str, skip_backup))

        server_config = get_server_config_by_id(server_id)
        if not server_config:
            raise ValueError(f"Target server '{server_id}' not found in servers config")

        # Write promoted config to remote ~/.dockerpilot_extras/deployments/<container>/deployment-<env>.yml
        remote_config_path = f"/home/{server_config.get('username','root')}/.dockerpilot_extras/deployments/{container_name}/deployment-{to_env}.yml"
        promoted_yaml = yaml.dump(config, default_flow_style=False, allow_unicode=True)
        _write_remote_file(server_config, remote_config_path, promoted_yaml)

        # Run deploy remotely
        cmd = f"dockerpilot deploy config '{remote_config_path}' --type {deployment_type}"
        if skip_backup:
            cmd += " --skip-backup"
        execute_command_via_ssh(server_config, cmd, check_exit_status=True)
        return True

    except Exception as e:
        app.logger.error(f"Remote promotion failed ({from_env}->{to_env} on {server_id}): {e}", exc_info=True)
        return False

AuthStatus, AuthLogin, AuthLogout, CheckSudoRequired, ElevationToken, SudoPassword = create_auth_resources(
    Resource=Resource,
    app=app,
    request=request,
    session=session,
    web_auth_enabled=WEB_AUTH_ENABLED,
    web_auth_username=WEB_AUTH_USERNAME,
    web_auth_totp_secret=WEB_AUTH_TOTP_SECRET,
    web_auth_totp_window=WEB_AUTH_TOTP_WINDOW,
    auth_status_payload=lambda: globals()["_auth_status_payload"](),
    verify_password=lambda password: globals()["_verify_password"](password),
    verify_totp_code=lambda secret, code, window=1: globals()["_verify_totp_code"](secret, code, window),
    clear_auth_session=lambda: globals()["_clear_auth_session"](),
    get_dockerpilot=get_dockerpilot,
    issue_elevation_token=lambda sudo_password, scope, ttl_seconds=None: globals()["_issue_elevation_token"](
        sudo_password=sudo_password,
        scope=scope,
        ttl_seconds=ttl_seconds,
    ),
    revoke_elevation_tokens_for_current_session=lambda: globals()[
        "_revoke_elevation_tokens_for_current_session"
    ](),
    now_ts=lambda: time.time(),
    datetime_cls=datetime,
)


MigrationProgress, CancelMigration, DeploymentProgress = create_progress_resources(
    Resource=Resource,
    app=app,
    request=request,
    deployment_progress=_deployment_progress,
    migration_progress=_migration_progress,
    migration_cancel_flags=_migration_cancel_flags,
    datetime_cls=datetime,
)


def _build_status_context(server_config):
    """Compatibility wrapper for status context (kept for test monkeypatching)."""
    return _svc_build_status_context(server_config)


def _run_remote_probe(server_config, command, attempts=2):
    """Compatibility wrapper for remote probe (kept for test monkeypatching)."""
    return _svc_run_remote_probe(
        server_config,
        command,
        execute_command_via_ssh=execute_command_via_ssh,
        logger=app.logger,
        attempts=attempts,
        retry_delay=0.25,
    )


def _probe_remote_binary_version(server_config, binary_name: str, missing_marker: str, no_version_marker: str):
    """Compatibility wrapper for binary probe (kept for test monkeypatching)."""
    return _svc_probe_remote_binary_version(
        server_config=server_config,
        binary_name=binary_name,
        missing_marker=missing_marker,
        no_version_marker=no_version_marker,
        run_remote_probe_fn=lambda cmd: _run_remote_probe(server_config, cmd, attempts=1),
    )


def _invalidate_environment_status_cache():
    """Best-effort cache invalidation compatibility hook."""
    if "_environment_status_cache" in globals():
        _environment_status_cache["data"] = None
        _environment_status_cache["timestamp"] = None


EnvironmentStatus, StatusCheck, PreflightCheck = create_status_resources(
    Resource=Resource,
    app=app,
    preflight_base_dir=Path(__file__).resolve().parent.parent,
    run_preflight_checks=lambda base_dir: globals()["run_preflight_checks"](base_dir),
    load_env_servers_config=load_env_servers_config,
    get_server_config_by_id=get_server_config_by_id,
    get_containers_and_images_for_server=_get_containers_and_images_for_server,
    build_environment_status=_svc_build_environment_status,
    get_selected_server_config=lambda: globals()["get_selected_server_config"](),
    build_status_context=lambda server_config: globals()["_build_status_context"](server_config),
    run_remote_probe=lambda server_config, command, attempts=2: globals()["_run_remote_probe"](
        server_config, command, attempts=attempts
    ),
    probe_remote_binary_version=lambda server_config, binary_name, missing_marker, no_version_marker: globals()[
        "_probe_remote_binary_version"
    ](server_config, binary_name, missing_marker, no_version_marker),
    get_dockerpilot=get_dockerpilot,
)


(
    ContainerList,
    DockerImages,
    DockerfilePaths,
    FileBrowser,
    PrepareContainerConfig,
    ImportDeploymentConfig,
    EnvServersMap,
    EnvContainerBindings,
    BlueGreenReplace,
) = create_environment_resources(
    Resource=Resource,
    app=app,
    request=request,
    load_env_servers_config=load_env_servers_config,
    save_env_servers_config=save_env_servers_config,
    load_env_container_bindings=load_env_container_bindings,
    save_env_container_bindings=save_env_container_bindings,
    normalize_env_container_bindings=_normalize_env_container_bindings,
    invalidate_environment_status_cache=lambda: globals()["_invalidate_environment_status_cache"](),
    get_selected_server_config=lambda: globals()["get_selected_server_config"](),
    get_server_config_by_id=get_server_config_by_id,
    get_dockerpilot=get_dockerpilot,
    execute_docker_command_via_ssh=execute_docker_command_via_ssh,
    resolve_server_id_for_env=resolve_server_id_for_env,
    detect_health_check_endpoint=_detect_health_check_endpoint,
    infer_port_mapping_for_host_network=_infer_port_mapping_for_host_network,
    save_deployment_config=save_deployment_config,
    format_env_name=format_env_name,
)


ExecuteCommand, GetCommandHelp, DockerPilotCommands = create_command_resources(
    Resource=Resource,
    request=request,
)


# ==================== SSH SERVER MANAGEMENT ====================

try:
    import paramiko
    from paramiko import SSHClient, AutoAddPolicy
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_ssh_private_key
    SSH_AVAILABLE = True
except ImportError:
    SSH_AVAILABLE = False
    app.logger.warning("SSH libraries not available. Install paramiko and cryptography for SSH server support.")

# Current selected server (stored in session)
def get_servers_config_path():
    """Get path to servers configuration file"""
    return _svc_get_servers_config_path(app.config['SERVERS_DIR'])

def load_servers_config():
    """Load servers configuration via configured state store."""
    return _svc_load_servers_config(get_state_store=get_state_store, logger=app.logger)

def save_servers_config(config):
    """Save servers configuration via configured state store."""
    return _svc_save_servers_config(config, get_state_store=get_state_store, logger=app.logger)

def convert_putty_key_to_openssh(ppk_content, passphrase=None):
    """Convert PuTTY private key (.ppk) to OpenSSH format"""
    return _svc_convert_putty_key_to_openssh(ppk_content, passphrase=passphrase)

def test_ssh_connection(server_config):
    """Test SSH connection to a server"""
    return _svc_test_ssh_connection(server_config, ssh_available=SSH_AVAILABLE)


ServerList, ServerCreate, ServerUpdate, ServerDelete, ServerTest, ServerSelect = create_server_resources(
    Resource=Resource,
    app=app,
    request=request,
    session=session,
    ssh_available=SSH_AVAILABLE,
    load_servers_config=lambda: globals()["load_servers_config"](),
    save_servers_config=lambda config: globals()["save_servers_config"](config),
    test_ssh_connection=lambda server_config: globals()["test_ssh_connection"](server_config),
)


ContainerMigrate = create_migration_resource(
    Resource=Resource,
    app=app,
    request=request,
    datetime_cls=datetime,
    migration_progress=_migration_progress,
    migration_cancel_flags=_migration_cancel_flags,
    load_servers_config=lambda: globals()["load_servers_config"](),
    get_dockerpilot=get_dockerpilot,
    execute_command_via_ssh=execute_command_via_ssh,
    execute_docker_command_via_ssh=execute_docker_command_via_ssh,
    save_deployment_config=save_deployment_config,
    infer_port_mapping_for_host_network=_infer_port_mapping_for_host_network,
)


HealthCheck, EnvironmentPromote, CancelPromotion, EnvironmentPromoteSingle = create_promotion_resources(
    Resource=Resource,
    app=app,
    request=request,
    session=session,
    datetime_cls=datetime,
    deployment_progress=_deployment_progress,
    get_dockerpilot=get_dockerpilot,
    consume_elevation_token=lambda *args, **kwargs: globals()["_consume_elevation_token"](*args, **kwargs),
    find_all_deployment_configs_for_env=find_all_deployment_configs_for_env,
    resolve_server_id_for_env=resolve_server_id_for_env,
    promote_config_to_server=promote_config_to_server,
    move_many_container_bindings=move_many_container_bindings,
    move_container_binding=move_container_binding,
    format_env_name=format_env_name,
    find_active_deployment_dir=find_active_deployment_dir,
    ContainerMigrate_cls=ContainerMigrate,
)


def _parse_env_list(env_list):
    env_map = {}
    for item in env_list or []:
        if isinstance(item, str) and '=' in item:
            key, value = item.split('=', 1)
            env_map[key] = value
    return env_map


def discover_local_postgres(container_name: str = 'postgres-dozeyserver') -> dict:
    """Inspect local Docker container and infer PostgreSQL connection params."""
    try:
        import docker
    except ImportError as exc:
        return {'success': False, 'error': f'Docker SDK not available: {exc}'}

    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        return {'success': False, 'error': f'Container {container_name} not found'}
    except Exception as exc:
        return {'success': False, 'error': f'Failed to inspect container: {exc}'}

    container.reload()
    attrs = container.attrs or {}
    env_map = _parse_env_list((attrs.get('Config') or {}).get('Env') or [])
    ports = ((attrs.get('NetworkSettings') or {}).get('Ports') or {}).get('5432/tcp') or []
    host_port = None
    if ports and isinstance(ports, list) and ports[0]:
        host_port = ports[0].get('HostPort')

    postgres_cfg = {
        'host': '127.0.0.1',
        'port': int(host_port) if host_port else 5432,
        'database': env_map.get('POSTGRES_DB', 'postgres'),
        'user': env_map.get('POSTGRES_USER', 'postgres'),
        'password': env_map.get('POSTGRES_PASSWORD', ''),
        'sslmode': 'prefer',
        'schema': DEFAULT_POSTGRES_SCHEMA,
        'table_prefix': DEFAULT_POSTGRES_TABLE_PREFIX,
        'auto_create_schema': DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
        'container_name': container_name,
    }

    return {
        'success': True,
        'container': {
            'name': container.name,
            'status': container.status,
            'image': str(container.image.tags[0] if container.image.tags else container.image.id),
            'id': container.id,
        },
        'postgres': postgres_cfg,
        'postgres_sanitized': sanitize_postgres_config(postgres_cfg),
    }


def ensure_local_postgres_container(
    container_name: str,
    image: str,
    host_port: int,
    database: str,
    user: str,
    password: str,
    volume_name: str = None,
):
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError(f'Docker SDK not available: {exc}') from exc

    client = docker.from_env()
    created = False
    try:
        container = client.containers.get(container_name)
        if container.status != 'running':
            container.start()
            container.reload()
    except docker.errors.NotFound:
        env = {
            'POSTGRES_DB': database,
            'POSTGRES_USER': user,
            'POSTGRES_PASSWORD': password,
        }
        volumes = None
        if volume_name:
            volumes = {volume_name: {'bind': '/var/lib/postgresql/data', 'mode': 'rw'}}
        container = client.containers.run(
            image=image,
            name=container_name,
            detach=True,
            restart_policy={'Name': 'unless-stopped'},
            environment=env,
            ports={'5432/tcp': int(host_port)},
            volumes=volumes,
        )
        created = True
    return container, created


(
    StorageStatus,
    StorageTestPostgres,
    StorageDiscoverLocalPostgres,
    StorageBootstrapLocalPostgres,
    StorageConfigure,
) = create_storage_resources(
    Resource=Resource,
    app=app,
    request=request,
    default_postgres_schema=DEFAULT_POSTGRES_SCHEMA,
    default_postgres_table_prefix=DEFAULT_POSTGRES_TABLE_PREFIX,
    storage_error_cls=StorageError,
    get_storage_status=get_storage_status,
    test_postgres_connection=test_postgres_connection,
    discover_local_postgres=discover_local_postgres,
    sanitize_postgres_config=sanitize_postgres_config,
    ensure_local_postgres_container=ensure_local_postgres_container,
    create_store=create_store,
    migrate_legacy_file_state_to_store=migrate_legacy_file_state_to_store,
    save_storage_config=save_storage_config,
    init_state_store=init_state_store,
    build_postgres_dsn=build_postgres_dsn,
)


register_api_routes(
    api,
    HealthCheck=HealthCheck,
    AuthStatus=AuthStatus,
    AuthLogin=AuthLogin,
    AuthLogout=AuthLogout,
    PipelineGenerate=PipelineGenerate,
    PipelineSave=PipelineSave,
    PipelineDeploymentConfig=PipelineDeploymentConfig,
    PipelineIntegration=PipelineIntegration,
    DeploymentConfig=DeploymentConfig,
    DeploymentExecute=DeploymentExecute,
    DeploymentHistory=DeploymentHistory,
    EnvironmentPromote=EnvironmentPromote,
    CancelPromotion=CancelPromotion,
    CheckSudoRequired=CheckSudoRequired,
    ElevationToken=ElevationToken,
    SudoPassword=SudoPassword,
    EnvironmentPromoteSingle=EnvironmentPromoteSingle,
    DeploymentProgress=DeploymentProgress,
    EnvironmentStatus=EnvironmentStatus,
    PrepareContainerConfig=PrepareContainerConfig,
    ImportDeploymentConfig=ImportDeploymentConfig,
    EnvServersMap=EnvServersMap,
    EnvContainerBindings=EnvContainerBindings,
    StatusCheck=StatusCheck,
    PreflightCheck=PreflightCheck,
    ContainerList=ContainerList,
    DockerImages=DockerImages,
    DockerfilePaths=DockerfilePaths,
    FileBrowser=FileBrowser,
    ExecuteCommand=ExecuteCommand,
    GetCommandHelp=GetCommandHelp,
    DockerPilotCommands=DockerPilotCommands,
    StorageStatus=StorageStatus,
    StorageTestPostgres=StorageTestPostgres,
    StorageDiscoverLocalPostgres=StorageDiscoverLocalPostgres,
    StorageBootstrapLocalPostgres=StorageBootstrapLocalPostgres,
    StorageConfigure=StorageConfigure,
    ServerList=ServerList,
    ServerCreate=ServerCreate,
    ServerUpdate=ServerUpdate,
    ServerDelete=ServerDelete,
    ServerTest=ServerTest,
    ServerSelect=ServerSelect,
    BlueGreenReplace=BlueGreenReplace,
    ContainerMigrate=ContainerMigrate,
    MigrationProgress=MigrationProgress,
    CancelMigration=CancelMigration,
)


@app.route('/')
def index():
    """Serve React app"""
    index_path = Path(app.static_folder) / 'index.html'
    if index_path.exists():
        return send_from_directory(app.static_folder, 'index.html')

    dev_url = (
        os.environ.get('FRONTEND_DEV_URL')
        or os.environ.get('VITE_DEV_URL')
        or 'http://localhost:5173'
    )
    return (
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>DockerPilot Extras</title>
  </head>
  <body>
    <h2>DockerPilot Extras backend is running.</h2>
    <p>No frontend build found at <code>{index_path}</code>.</p>
    <p>Start the frontend dev server and open: <a href="{dev_url}">{dev_url}</a></p>
  </body>
</html>""",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


@app.errorhandler(404)
def not_found(e):
    """Handle React Router routes"""
    # Keep API 404s as JSON-ish errors, don't mask them with SPA fallback.
    if request.path.startswith('/api/'):
        return {"error": "Not found", "path": request.path}, 404

    index_path = Path(app.static_folder) / 'index.html'
    if index_path.exists():
        return send_from_directory(app.static_folder, 'index.html')

    dev_url = (
        os.environ.get('FRONTEND_DEV_URL')
        or os.environ.get('VITE_DEV_URL')
        or 'http://localhost:5173'
    )
    return (
        f"Frontend build not found. Start Vite and open {dev_url}",
        404,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


if __name__ == '__main__':
    # Development server
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
