"""
DockerPilot Extras - Flask Backend API
Backend for CI/CD Pipeline Management
"""

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_restful import Api, Resource
import atexit
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
import shlex

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
from backend.security import redact_sensitive_text, safe_error_message
from backend.resources.auth import create_auth_resources
from backend.resources.commands import create_command_resources
from backend.resources.environment import create_environment_resources
from backend.resources.migration import create_migration_resource
from backend.resources.migration_async import create_async_migration_resources
from backend.resources.pipeline import create_pipeline_resources
from backend.resources.promotion import create_promotion_resources
from backend.resources.progress import create_progress_resources
from backend.resources.servers import create_server_resources
from backend.resources.storage import create_storage_resources
from backend.resources.status import create_status_resources
from backend.secure_deploy.resources import create_secure_deploy_resources
from backend.secure_deploy.service import SecureDeployService
from backend.secure_deploy.store import FileSecureDeployStore
from backend.secure_deploy.errors import (
    AuthRequiredError,
    ForbiddenError,
    UnauthorizedError,
    SecureDeployError as SecureDeployGateError,
)
from backend.services.auth_guard import SlidingWindowRateLimiter, csrf_token_matches
from backend.services.deployment_files import (
    find_active_deployment_dir as _svc_find_active_deployment_dir,
    find_all_deployment_dirs as _svc_find_all_deployment_dirs,
    format_env_name as _svc_format_env_name,
    generate_deployment_id as _svc_generate_deployment_id,
    get_or_create_deployment_dir as _svc_get_or_create_deployment_dir,
    load_deployment_config as _svc_load_deployment_config,
    save_deployment_config as _svc_save_deployment_config,
)
from backend.services.health_detection import (
    detect_from_running_containers as _svc_detect_health_from_containers,
    detect_health_check_endpoint as _svc_detect_health_check_endpoint,
)
from backend.services.local_postgres import (
    discover_local_postgres as _svc_discover_local_postgres,
    ensure_local_postgres_container as _svc_ensure_local_postgres_container,
    parse_env_list as _svc_parse_env_list,
)
from backend.services.environment_state import (
    append_deployment_history_data as _svc_append_deployment_history_data,
    get_deployment_history_data as _svc_get_deployment_history_data,
    load_env_container_bindings as _svc_load_env_container_bindings,
    load_env_servers_config as _svc_load_env_servers_config,
    load_legacy_file_state_snapshot as _svc_load_legacy_file_state_snapshot,
    migrate_legacy_file_state_to_store as _svc_migrate_legacy_file_state_to_store,
    move_container_binding as _svc_move_container_binding,
    move_many_container_bindings as _svc_move_many_container_bindings,
    normalize_env_container_bindings as _svc_normalize_env_container_bindings,
    resolve_server_id_for_env as _svc_resolve_server_id_for_env,
    save_env_container_bindings as _svc_save_env_container_bindings,
    save_env_servers_config as _svc_save_env_servers_config,
)
from backend.services.elevation_tokens import ElevationTokenManager
from backend.services.secret_store import EncryptedSecretStore, SecretStoreError
from backend.services.ssh_security import (
    SSHHostKeyMismatch,
    SSHHostKeyRequired,
    create_verified_ssh_client,
)
from backend.services.remote_commands import (
    build_dockerpilot_deploy_command as _svc_build_dockerpilot_deploy_command,
    build_remote_file_write_command as _svc_build_remote_file_write_command,
)
from backend.services.ssh_execution import (
    build_docker_command as _svc_build_docker_command,
    execute_command as _svc_execute_ssh_command,
    execute_script as _svc_execute_ssh_script,
    open_verified_ssh_client as _svc_open_verified_ssh_client,
)
from backend.services.environment_status import build_environment_status as _svc_build_environment_status
from backend.services.migration_jobs import MigrationJobRegistry
from backend.services.migration_service import MigrationService
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
    return _svc_format_env_name(env)

def generate_deployment_id(container_name: str, image_tag: str = None) -> str:
    return _svc_generate_deployment_id(container_name, image_tag)

def _detect_health_check_endpoint_from_containers(image_tag: str) -> str:
    return _svc_detect_health_from_containers(
        image_tag,
        get_dockerpilot=get_dockerpilot,
        logger=app.logger,
    )


def _detect_health_check_endpoint(image_tag: str) -> str:
    return _svc_detect_health_check_endpoint(
        image_tag,
        get_dockerpilot=get_dockerpilot,
        logger=app.logger,
    )


def _extract_port_from_string(value: str):
    """Extract valid TCP/UDP port number from text value."""
    return _svc_extract_port_from_string(value)


def _infer_port_mapping_for_host_network(attrs: dict, image_tag: str = "") -> dict:
    """Infer minimal port mapping for host-network containers."""
    return _svc_infer_port_mapping_for_host_network(attrs, image_tag)


def get_or_create_deployment_dir(container_name: str, image_tag: str = None, deployment_id: str = None) -> Path:
    return _svc_get_or_create_deployment_dir(
        app.config['DEPLOYMENTS_DIR'],
        container_name,
        image_tag,
        deployment_id,
    )

def find_active_deployment_dir(container_name: str) -> Path:
    return _svc_find_active_deployment_dir(app.config['DEPLOYMENTS_DIR'], container_name)

def find_all_deployment_dirs(container_name: str = None) -> list:
    return _svc_find_all_deployment_dirs(app.config['DEPLOYMENTS_DIR'], container_name)

def save_deployment_config(container_name: str, config: dict, env: str = None, image_tag: str = None) -> Path:
    return _svc_save_deployment_config(
        app.config['DEPLOYMENTS_DIR'],
        container_name,
        config,
        env,
        image_tag,
    )

def load_deployment_config(container_name: str, env: str = None) -> dict:
    return _svc_load_deployment_config(app.config['DEPLOYMENTS_DIR'], container_name, env)

app = Flask(__name__, static_folder='../frontend/build', static_url_path='')
_configured_secret_key = (os.environ.get('SECRET_KEY') or '').strip()
app.config['SECRET_KEY'] = _configured_secret_key or os.urandom(24).hex()
app.config['SESSION_COOKIE_HTTPONLY'] = True
# Lax is compatible with SPA-on-different-port (SameSite is scheme+eTLD+1, not port).
# Production must set SESSION_COOKIE_SECURE=true; Secure Deploy POSTs always require CSRF,
# and production/SECURE_DEPLOY_REQUIRE_ORIGIN also requires allowlisted Origin.
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
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
AUTH_LOGIN_MAX_FAILURES = int(os.environ.get('AUTH_LOGIN_MAX_FAILURES', '5'))
AUTH_LOGIN_WINDOW_SECONDS = int(os.environ.get('AUTH_LOGIN_WINDOW_SECONDS', '60'))
_login_rate_limiter = SlidingWindowRateLimiter(
    max_failures=AUTH_LOGIN_MAX_FAILURES,
    window_seconds=AUTH_LOGIN_WINDOW_SECONDS,
)

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=APP_SESSION_IDLE_MINUTES)

# Short-lived elevation token settings (for privileged operations)
ELEVATION_TOKEN_TTL_SECONDS = int(os.environ.get('ELEVATION_TOKEN_TTL_SECONDS', '120'))
ELEVATION_TOKEN_MAX_TTL_SECONDS = int(os.environ.get('ELEVATION_TOKEN_MAX_TTL_SECONDS', '600'))
ELEVATION_TOKEN_MAX_PER_SESSION = int(os.environ.get('ELEVATION_TOKEN_MAX_PER_SESSION', '16'))
_elevation_token_manager = ElevationTokenManager(
    default_ttl_seconds=ELEVATION_TOKEN_TTL_SECONDS,
    max_ttl_seconds=ELEVATION_TOKEN_MAX_TTL_SECONDS,
    max_per_session=ELEVATION_TOKEN_MAX_PER_SESSION,
)

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
    if os.environ.get('FLASK_ENV') == 'production':
        raise RuntimeError(
            "Refusing production startup with default DockerPilot Extras credentials (admin/admin)"
        )
    app.logger.warning(
        "WEB_AUTH is enabled with default credentials (admin/admin). "
        "Set WEB_AUTH_PASSWORD_HASH or WEB_AUTH_PASSWORD before production."
    )

if WEB_AUTH_ENABLED and os.environ.get('FLASK_ENV') == 'production' and not _configured_secret_key:
    raise RuntimeError("SECRET_KEY must be explicitly configured when WEB_AUTH is enabled in production")


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
        'mfa_verified': bool(session.get('auth_mfa_verified')) if authed else False,
        'session_idle_minutes': APP_SESSION_IDLE_MINUTES,
        'session_expires_in_seconds': expires_in,
        'csrf_token': _ensure_secure_deploy_csrf() if authed and WEB_AUTH_ENABLED else None,
        'secure_deploy_csrf': _ensure_secure_deploy_csrf() if authed and WEB_AUTH_ENABLED else None,
    }


PUBLIC_API_PATHS = {
    '/api/health',
    '/api/auth/login',
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
    # Secure Deploy has a stricter dedicated gate.
    if path.startswith('/api/secure-deploy/'):
        return None

    if not _is_authenticated_session():
        return jsonify({
            'success': False,
            'error': 'Authentication required',
            'auth_required': True,
        }), 401

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if not csrf_token_matches(
            header_token=request.headers.get('X-CSRF-Token'),
            session_token=session.get('secure_deploy_csrf'),
        ):
            return jsonify({
                'success': False,
                'error': 'CSRF token required',
                'csrf_required': True,
            }), 403
    return None


SECURE_DEPLOY_MAX_BODY = 2 * 1024 * 1024


def _ensure_secure_deploy_csrf() -> str:
    token = session.get('secure_deploy_csrf')
    if not token:
        token = secrets.token_urlsafe(32)
        session['secure_deploy_csrf'] = token
        session.permanent = True
    return token


def _secure_deploy_origin_allowed() -> bool:
    origin = (request.headers.get('Origin') or '').strip()
    referer = (request.headers.get('Referer') or '').strip()
    allowed = set(cors_origins)
    if origin:
        return origin in allowed
    if referer:
        return any(referer.startswith(item.rstrip('/') + '/') or referer.rstrip('/') == item.rstrip('/') for item in allowed)
    return False


def require_secure_deploy_access() -> None:
    """Fail-closed auth gate for /api/secure-deploy/* (stricter than legacy)."""
    if not WEB_AUTH_ENABLED:
        raise AuthRequiredError(
            code='secure_deploy_auth_required',
            message='Secure Deploy requires WEB_AUTH_ENABLED=true',
        )
    if not WEB_AUTH_TOTP_SECRET:
        raise ForbiddenError(
            code='secure_deploy_mfa_required',
            message='Secure Deploy requires WEB_AUTH_TOTP_SECRET / MFA',
        )
    if not _is_authenticated_session():
        raise UnauthorizedError()
    if not session.get('auth_mfa_verified'):
        raise ForbiddenError(
            code='secure_deploy_mfa_unverified',
            message='Secure Deploy requires verified MFA session',
        )

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        csrf_header = (request.headers.get('X-CSRF-Token') or '').strip()
        csrf_session = session.get('secure_deploy_csrf') or ''
        csrf_ok = bool(csrf_header) and bool(csrf_session) and hmac.compare_digest(csrf_header, csrf_session)
        if not csrf_ok:
            raise ForbiddenError(
                code='secure_deploy_csrf',
                message='Secure Deploy CSRF token required',
            )

        origin = (request.headers.get('Origin') or '').strip()
        require_origin = (
            os.environ.get('SECURE_DEPLOY_REQUIRE_ORIGIN', '').lower() == 'true'
            or os.environ.get('FLASK_ENV') == 'production'
            or app.config.get('SESSION_COOKIE_SECURE')
        )
        trusted_client = os.environ.get('SECURE_DEPLOY_TRUSTED_CLIENT', 'false').lower() == 'true'
        # SameSite is scheme+registrable-domain (not port). CSRF is always required;
        # production browser calls must also send an allowlisted Origin.
        if require_origin and not trusted_client:
            if not origin:
                raise ForbiddenError(
                    code='secure_deploy_csrf',
                    message='Secure Deploy Origin required in production',
                )
            if not _secure_deploy_origin_allowed():
                raise ForbiddenError(
                    code='secure_deploy_csrf',
                    message='Secure Deploy Origin check failed',
                )
        elif origin and not _secure_deploy_origin_allowed():
            raise ForbiddenError(
                code='secure_deploy_csrf',
                message='Secure Deploy Origin check failed',
            )
    _ensure_secure_deploy_csrf()


def get_secure_deploy_actor() -> str:
    return str(session.get('auth_username') or WEB_AUTH_USERNAME)


def get_secure_deploy_session_hash() -> str:
    from backend.secure_deploy.approval import session_id_hash

    material = '|'.join(
        [
            str(session.get('auth_username') or ''),
            str(session.get('secure_deploy_csrf') or ''),
            str(session.get('auth_mfa_verified') or ''),
        ]
    )
    return session_id_hash(material)

# Configuration
app.config['CONFIG_DIR'] = Path.home() / ".dockerpilot_extras"
app.config['CONFIG_DIR'].mkdir(exist_ok=True)
app.config['PIPELINES_DIR'] = app.config['CONFIG_DIR'] / "pipelines"
app.config['PIPELINES_DIR'].mkdir(exist_ok=True)
app.config['DEPLOYMENTS_DIR'] = app.config['CONFIG_DIR'] / "deployments"
app.config['DEPLOYMENTS_DIR'].mkdir(exist_ok=True)
app.config['SERVERS_DIR'] = app.config['CONFIG_DIR'] / "servers"
app.config['SERVERS_DIR'].mkdir(exist_ok=True)
app.config['SSH_KNOWN_HOSTS_PATH'] = app.config['CONFIG_DIR'] / "known_hosts"
_server_secret_store = EncryptedSecretStore.from_config_dir(app.config['CONFIG_DIR'])

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
_dockerpilot_instances_lock = threading.RLock()
_current_server_id = None

# Global progress tracking for deployments
_deployment_progress = {}  # {container_name: {'stage': str, 'progress': int, 'message': str, 'timestamp': datetime}}

# Global progress tracking for migrations
_migration_progress = {}  # {container_name: {'stage': str, 'progress': int, 'message': str, 'timestamp': datetime}}
_migration_cancel_flags = {}  # {container_name: bool} - flags to cancel migrations


def get_dockerpilot(server_id=None):
    """Get or create DockerPilot instance for current server

    Flask embeds DockerPilot without registering process signal handlers.
    Cache construction is serialized because request threads can arrive
    before the first instance has been initialized.
    """
    global _dockerpilot_instances, _current_server_id
    
    # Request handlers may use the selected server. Background-safe callers
    # pass an explicit id and therefore do not need Flask session state.
    if server_id is None:
        server_id = session.get('selected_server', 'local')
    
    with _dockerpilot_instances_lock:
        # Cache lookup and construction must be atomic: duplicate pilots own
        # separate Docker clients and used to race during concurrent requests.
        if server_id in _dockerpilot_instances:
            return _dockerpilot_instances[server_id]

        config_path = app.config['CONFIG_DIR'] / 'deployment.yml'
        config_path_str = str(config_path) if config_path.exists() else None

        # For remote servers, we need to configure Docker client for SSH.
        docker_client = None
        if server_id != 'local':
            config = load_servers_config()
            server_config = None
            for server in config.get('servers', []):
                if server.get('id') == server_id:
                    server_config = server
                    break

            if server_config:
                try:
                    app.logger.warning(f"Remote server {server_id} selected, but Docker over SSH not fully implemented yet. Using local Docker.")
                except Exception as e:
                    app.logger.error(f"Failed to create Docker client for remote server: {e}")
                    server_id = 'local'
                    if server_id in _dockerpilot_instances:
                        return _dockerpilot_instances[server_id]

        try:
            instance = DockerPilotEnhanced(
                config_file=config_path_str,
                log_level=LogLevel.INFO,
                register_signal_handlers=False,
            )
            _dockerpilot_instances[server_id] = instance
            _current_server_id = server_id
        except Exception as e:
            import logging
            logging.error(f"Failed to initialize DockerPilot: {e}")
            raise

        return instance

def execute_command_via_ssh(server_config, command, check_exit_status=True):
    """Execute command locally without a shell or remotely over verified SSH."""
    try:
        return _svc_execute_ssh_command(
            server_config,
            command,
            ssh_available=SSH_AVAILABLE,
            known_hosts_path=app.config['SSH_KNOWN_HOSTS_PATH'],
            check_exit_status=check_exit_status,
            return_stderr=False,
            timeout=300,
        )
    except Exception as exc:
        app.logger.error("Failed to execute command via SSH: %s", safe_error_message(exc))
        raise


# Cache for sudo requirements per server
_docker_sudo_cache = {}


def _get_or_create_elevation_session_id() -> str:
    """Compatibility wrapper for session-bound elevation identity."""
    return _elevation_token_manager._get_or_create_session_id(session)


def _cleanup_expired_elevation_tokens() -> int:
    return _elevation_token_manager.cleanup_expired()


def _issue_elevation_token(sudo_password: str, scope: dict = None, ttl_seconds: int = None) -> dict:
    return _elevation_token_manager.issue(
        session,
        sudo_password=sudo_password,
        scope=scope,
        ttl_seconds=ttl_seconds,
    )


def _consume_elevation_token(token: str, expected_action: str = None, expected_scope: dict = None) -> tuple:
    return _elevation_token_manager.consume(
        session,
        token,
        expected_action=expected_action,
        expected_scope=expected_scope,
    )


def _revoke_elevation_tokens_for_current_session() -> int:
    return _elevation_token_manager.revoke_for_session(session)


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
    
    # Parse and re-quote Docker arguments so remote shell metacharacters never
    # gain command-separator semantics.
    command = _svc_build_docker_command(docker_command, use_sudo=bool(use_sudo))
    
    # For docker load, we need to check stderr too (docker load outputs to stderr)
    if return_stderr or 'load' in docker_command:
        return _execute_command_via_ssh_with_stderr(server_config, command, check_exit_status=check_exit_status)
    else:
        return execute_command_via_ssh(server_config, command, check_exit_status=check_exit_status)

def _execute_command_via_ssh_with_stderr(server_config, command, check_exit_status=True):
    """Execute command and return stdout/stderr without invoking a local shell."""
    try:
        return _svc_execute_ssh_command(
            server_config,
            command,
            ssh_available=SSH_AVAILABLE,
            known_hosts_path=app.config['SSH_KNOWN_HOSTS_PATH'],
            check_exit_status=check_exit_status,
            return_stderr=True,
            timeout=300,
        )
    except Exception as exc:
        app.logger.error("Failed to execute command via SSH: %s", safe_error_message(exc))
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
    return _svc_load_env_servers_config(get_state_store=get_state_store, logger=app.logger)


def save_env_servers_config(config: dict) -> bool:
    return _svc_save_env_servers_config(config, get_state_store=get_state_store, logger=app.logger)


def get_deployment_history_data(limit: int = 50) -> list:
    return _svc_get_deployment_history_data(get_state_store=get_state_store, limit=limit, logger=app.logger)


def append_deployment_history_data(entry: dict, max_entries: int = 50) -> bool:
    return _svc_append_deployment_history_data(
        entry, get_state_store=get_state_store, max_entries=max_entries, logger=app.logger
    )


(
    PipelineGenerate,
    PipelineSave,
    PipelineDeploymentConfig,
    PipelineIntegration,
    DeploymentConfig,
    DeploymentExecute,
    DeploymentHistory,
    PipelineLibrary,
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
    return _svc_load_env_container_bindings(get_state_store=get_state_store, logger=app.logger)


def save_env_container_bindings(config: dict) -> bool:
    return _svc_save_env_container_bindings(config, get_state_store=get_state_store, logger=app.logger)


def _normalize_env_container_bindings(config: dict) -> dict:
    return _svc_normalize_env_container_bindings(config)


def move_container_binding(container_name: str, from_env: str, to_env: str) -> bool:
    return _svc_move_container_binding(
        container_name,
        from_env,
        to_env,
        load_bindings=load_env_container_bindings,
        save_bindings=save_env_container_bindings,
        invalidate_cache=lambda: globals()["_invalidate_environment_status_cache"](),
    )


def move_many_container_bindings(container_names: list, from_env: str, to_env: str) -> bool:
    return _svc_move_many_container_bindings(
        container_names,
        from_env,
        to_env,
        load_bindings=load_env_container_bindings,
        save_bindings=save_env_container_bindings,
        invalidate_cache=lambda: globals()["_invalidate_environment_status_cache"](),
    )


def load_legacy_file_state_snapshot() -> dict:
    return _svc_load_legacy_file_state_snapshot(file_store_factory=_build_file_store)


def migrate_legacy_file_state_to_store(target_store) -> dict:
    return _svc_migrate_legacy_file_state_to_store(
        target_store=target_store, file_store_factory=_build_file_store
    )


def resolve_server_id_for_env(env: str) -> str:
    return _svc_resolve_server_id_for_env(env, load_env_servers=load_env_servers_config)


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
    """Write text to a remote path using a shell-safe Python command."""
    command = _svc_build_remote_file_write_command(remote_path, content)
    execute_command_via_ssh(server_config, command, check_exit_status=True)


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
        cmd = _svc_build_dockerpilot_deploy_command(
            remote_config_path,
            deployment_type,
            skip_backup=skip_backup,
        )
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
    login_rate_limiter=_login_rate_limiter,
    login_rate_key=lambda _username: str(request.remote_addr or 'unknown'),
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
    from paramiko import SSHClient
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
    """Load server config and decrypt credential fields only in process memory."""
    raw = _svc_load_servers_config(get_state_store=get_state_store, logger=app.logger)
    try:
        return _server_secret_store.reveal_config(raw)
    except SecretStoreError as exc:
        app.logger.error("Failed to decrypt server configuration: %s", safe_error_message(exc))
        raise


def save_servers_config(config):
    """Encrypt credential fields before writing server config to any state backend."""
    protected = _server_secret_store.protect_config(config or {})
    return _svc_save_servers_config(protected, get_state_store=get_state_store, logger=app.logger)


def _migrate_plaintext_server_secrets() -> None:
    """One-way migration of legacy plaintext server credentials to encrypted-at-rest values."""
    raw = _svc_load_servers_config(get_state_store=get_state_store, logger=app.logger)
    if not _server_secret_store.has_plaintext_secrets(raw):
        return
    protected = _server_secret_store.protect_config(raw)
    if not _svc_save_servers_config(protected, get_state_store=get_state_store, logger=app.logger):
        raise RuntimeError("Failed to persist encrypted server credential migration")
    app.logger.info("Migrated legacy server credentials to encrypted-at-rest storage")


def convert_putty_key_to_openssh(ppk_content, passphrase=None):
    """Convert PuTTY private key (.ppk) to OpenSSH format"""
    return _svc_convert_putty_key_to_openssh(ppk_content, passphrase=passphrase)


def test_ssh_connection(server_config):
    """Test SSH connection using strict host-key verification."""
    return _svc_test_ssh_connection(
        server_config,
        ssh_available=SSH_AVAILABLE,
        known_hosts_path=app.config['SSH_KNOWN_HOSTS_PATH'],
    )


_migrate_plaintext_server_secrets()


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
    get_dockerpilot=lambda: get_dockerpilot('local'),
    execute_command_via_ssh=execute_command_via_ssh,
    execute_shell_script_via_ssh=lambda server_config, script, check_exit_status=True: _svc_execute_ssh_script(
        server_config,
        script,
        ssh_available=SSH_AVAILABLE,
        known_hosts_path=app.config['SSH_KNOWN_HOSTS_PATH'],
        check_exit_status=check_exit_status,
        timeout=300,
    ),
    execute_docker_command_via_ssh=execute_docker_command_via_ssh,
    open_ssh_client_for_transfer=lambda server_config: _svc_open_verified_ssh_client(
        server_config,
        known_hosts_path=app.config['SSH_KNOWN_HOSTS_PATH'],
        timeout=30,
    ),
    save_deployment_config=save_deployment_config,
    infer_port_mapping_for_host_network=_infer_port_mapping_for_host_network,
)


_migration_job_registry = MigrationJobRegistry()
_migration_service = MigrationService(
    ContainerMigrate.migration_runner,
    registry=_migration_job_registry,
    max_workers=1,
    queue_capacity=2,
)
ContainerMigrate.migration_runner = _migration_service
ContainerMigrationCollection, ContainerMigrationJob = create_async_migration_resources(
    Resource=Resource,
    request=request,
    migration_service=_migration_service,
)
atexit.register(
    lambda: _migration_service.shutdown(
        wait=True,
        cancel_pending=True,
        timeout=2.0,
    )
)


HealthCheck, EnvironmentPromote, CancelPromotion, EnvironmentPromoteSingle = create_promotion_resources(
    Resource=Resource,
    app=app,
    request=request,
    session=session,
    datetime_cls=datetime,
    deployment_progress=_deployment_progress,
    get_dockerpilot=lambda: get_dockerpilot('local'),
    consume_elevation_token=lambda *args, **kwargs: globals()["_consume_elevation_token"](*args, **kwargs),
    find_all_deployment_configs_for_env=find_all_deployment_configs_for_env,
    resolve_server_id_for_env=resolve_server_id_for_env,
    promote_config_to_server=promote_config_to_server,
    move_many_container_bindings=move_many_container_bindings,
    move_container_binding=move_container_binding,
    format_env_name=format_env_name,
    find_active_deployment_dir=find_active_deployment_dir,
    migration_runner=ContainerMigrate.migration_runner,
)


def _parse_env_list(env_list):
    return _svc_parse_env_list(env_list)


def discover_local_postgres(container_name: str = 'postgres-dozeyserver') -> dict:
    return _svc_discover_local_postgres(
        container_name,
        default_schema=DEFAULT_POSTGRES_SCHEMA,
        default_table_prefix=DEFAULT_POSTGRES_TABLE_PREFIX,
        default_auto_create_schema=DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
        sanitize_postgres_config=sanitize_postgres_config,
    )


def ensure_local_postgres_container(
    container_name: str,
    image: str,
    host_port: int,
    database: str,
    user: str,
    password: str,
    volume_name: str = None,
):
    return _svc_ensure_local_postgres_container(
        container_name=container_name,
        image=image,
        host_port=host_port,
        database=database,
        user=user,
        password=password,
        volume_name=volume_name,
    )


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


_secure_deploy_root = Path(
    os.environ.get(
        'SECURE_DEPLOY_STORE_ROOT',
        str(Path.home() / '.dockerpilot_extras' / 'secure_deploy'),
    )
)
_secure_deploy_store = FileSecureDeployStore(_secure_deploy_root)
_secure_deploy_service = SecureDeployService(_secure_deploy_store)

from backend.secure_deploy.approval import ApprovalService
from backend.secure_deploy.broker_client import BrokerClient
from backend.secure_deploy.step_up import StepUpTotpGuard

_secure_deploy_approvals = ApprovalService(_secure_deploy_store)
_secure_deploy_broker = BrokerClient(os.environ.get('SECURE_DEPLOY_BROKER_SOCKET'))
_secure_deploy_step_up = StepUpTotpGuard(
    verify_fn=lambda secret, code, window=1: _verify_totp_code(secret, code, window),
)


def _verify_secure_deploy_step_up(code: str) -> bool:
    actor = get_secure_deploy_actor()
    return _secure_deploy_step_up.verify(
        actor_key=actor,
        secret=WEB_AUTH_TOTP_SECRET,
        code=code,
        window=WEB_AUTH_TOTP_WINDOW,
    )


(
    SecureDeployDrafts,
    SecureDeployDraftDetail,
    SecureDeployValidate,
    SecureDeployPlan,
    SecureDeployPlanDetail,
    SecureDeployPlanApprove,
    SecureDeployApprovalDetail,
    SecureDeployApprovalRevoke,
    SecureDeployBrokerDryRun,
    SecureDeployCanaryAdmit,
    SecureDeployCanaryDeploy,
    SecureDeployCanaryRemove,
) = create_secure_deploy_resources(
    Resource=Resource,
    request=request,
    service=_secure_deploy_service,
    approval_service=_secure_deploy_approvals,
    broker_client=_secure_deploy_broker,
    require_secure_deploy_access=require_secure_deploy_access,
    get_actor=get_secure_deploy_actor,
    get_session_hash=get_secure_deploy_session_hash,
    verify_step_up_totp=_verify_secure_deploy_step_up,
    max_body_bytes=SECURE_DEPLOY_MAX_BODY,
)


register_api_routes(
    api,
    HealthCheck=HealthCheck,
    AuthStatus=AuthStatus,
    AuthLogin=AuthLogin,
    AuthLogout=AuthLogout,
    PipelineGenerate=PipelineGenerate,
    PipelineSave=PipelineSave,
    PipelineLibrary=PipelineLibrary,
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
    ContainerMigrationCollection=ContainerMigrationCollection,
    ContainerMigrationJob=ContainerMigrationJob,
    MigrationProgress=MigrationProgress,
    CancelMigration=CancelMigration,
    SecureDeployDrafts=SecureDeployDrafts,
    SecureDeployDraftDetail=SecureDeployDraftDetail,
    SecureDeployValidate=SecureDeployValidate,
    SecureDeployPlan=SecureDeployPlan,
    SecureDeployPlanDetail=SecureDeployPlanDetail,
    SecureDeployPlanApprove=SecureDeployPlanApprove,
    SecureDeployApprovalDetail=SecureDeployApprovalDetail,
    SecureDeployApprovalRevoke=SecureDeployApprovalRevoke,
    SecureDeployBrokerDryRun=SecureDeployBrokerDryRun,
    SecureDeployCanaryAdmit=SecureDeployCanaryAdmit,
    SecureDeployCanaryDeploy=SecureDeployCanaryDeploy,
    SecureDeployCanaryRemove=SecureDeployCanaryRemove,
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
