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

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent.parent))
# Add src directory to path for DockerPilot import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from utils.pipeline_generator import (
    PipelineGenerator, 
    parse_env_vars,
    generate_deployment_config_for_environment
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
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)

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

# Configuration
app.config['CONFIG_DIR'] = Path.home() / ".dockerpilot_extras"
app.config['CONFIG_DIR'].mkdir(exist_ok=True)
app.config['PIPELINES_DIR'] = app.config['CONFIG_DIR'] / "pipelines"
app.config['PIPELINES_DIR'].mkdir(exist_ok=True)
app.config['DEPLOYMENTS_DIR'] = app.config['CONFIG_DIR'] / "deployments"
app.config['DEPLOYMENTS_DIR'].mkdir(exist_ok=True)
app.config['SERVERS_DIR'] = app.config['CONFIG_DIR'] / "servers"
app.config['SERVERS_DIR'].mkdir(exist_ok=True)

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


class HealthCheck(Resource):
    """Health check endpoint"""
    def get(self):
        return {'status': 'ok', 'timestamp': datetime.now().isoformat()}


class PipelineGenerate(Resource):
    """Generate CI/CD pipeline"""
    def post(self):
        try:
            data = request.get_json()
            
            pipeline_type = data.get('type', 'gitlab')  # gitlab or jenkins
            project_name = data.get('project_name', 'myapp')
            docker_image = data.get('docker_image', 'myapp:latest')
            dockerfile = data.get('dockerfile', './Dockerfile')
            stages = data.get('stages', ['build', 'test', 'deploy'])
            env_vars_text = data.get('env_vars', 'ENV=production')
            deploy_strategy = data.get('deploy_strategy', 'rolling')
            
            # Parse environment variables
            env_vars = parse_env_vars(env_vars_text)
            
            # Generate pipeline
            generator = PipelineGenerator()
            
            if pipeline_type == 'gitlab':
                runner_tags = data.get('runner_tags', 'docker,linux').split(',')
                use_cache = data.get('use_cache', True)
                registry_url = data.get('registry_url')
                enable_environments = data.get('enable_environments', True)
                deployment_config_path = data.get('deployment_config_path', 'deployment.yml')
                
                pipeline_content = generator.generate_gitlab_pipeline(
                    project_name=project_name,
                    docker_image=docker_image,
                    dockerfile=dockerfile,
                    runner_tags=runner_tags,
                    stages=stages,
                    env_vars=env_vars,
                    deploy_strategy=deploy_strategy,
                    use_cache=use_cache,
                    registry_url=registry_url,
                    enable_environments=enable_environments,
                    deployment_config_path=deployment_config_path
                )
                filename = '.gitlab-ci.yml'
                
            elif pipeline_type == 'jenkins':
                agent = data.get('agent', 'any')
                credentials_id = data.get('credentials_id', 'docker-credentials')
                enable_environments = data.get('enable_environments', True)
                deployment_config_path = data.get('deployment_config_path', 'deployment.yml')
                
                pipeline_content = generator.generate_jenkins_pipeline(
                    project_name=project_name,
                    docker_image=docker_image,
                    dockerfile=dockerfile,
                    agent=agent,
                    credentials_id=credentials_id,
                    stages=stages,
                    env_vars=env_vars,
                    deploy_strategy=deploy_strategy,
                    enable_environments=enable_environments,
                    deployment_config_path=deployment_config_path
                )
                filename = 'Jenkinsfile'
            else:
                return {'error': 'Invalid pipeline type'}, 400
            
            return {
                'success': True,
                'content': pipeline_content,
                'filename': filename,
                'type': pipeline_type
            }
            
        except Exception as e:
            return {'error': str(e)}, 500


class PipelineSave(Resource):
    """Save pipeline to file"""
    def post(self):
        try:
            data = request.get_json()
            content = data.get('content')
            filename = data.get('filename', 'pipeline.yml')
            
            if not content:
                return {'error': 'No content provided'}, 400
            
            # Save to pipelines directory
            filepath = app.config['PIPELINES_DIR'] / filename
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return {
                'success': True,
                'message': f'Pipeline saved to {filepath}',
                'path': str(filepath)
            }
            
        except Exception as e:
            return {'error': str(e)}, 500


class PipelineDeploymentConfig(Resource):
    """Generate deployment config for environments from pipeline config"""
    def post(self):
        try:
            data = request.get_json()
            base_config = data.get('base_config', {})
            image_tag = data.get('image_tag', 'myapp:latest')
            container_name = data.get('container_name', 'myapp')
            environments = data.get('environments', ['dev', 'staging', 'prod'])
            
            if not base_config:
                # Try to load from existing deployment.yml
                config_path = app.config['CONFIG_DIR'] / 'deployment.yml'
                if config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        base_config = yaml.safe_load(f) or {}
            
            configs = {}
            # Save configs to unified deployment directory structure
            for env in environments:
                env_config = generate_deployment_config_for_environment(
                    base_config, env, image_tag, container_name
                )
                configs[env] = env_config
                
                # Save config for each environment using new structure
                save_deployment_config(container_name, env_config, env=env, image_tag=image_tag)
            
            # Also save main deployment.yml (defaults to dev)
            default_config = configs.get('dev', configs.get(list(configs.keys())[0] if configs else {}))
            save_deployment_config(container_name, default_config, image_tag=image_tag)
            
            return {
                'success': True,
                'configs': configs,
                'message': f'Generated deployment configs for {len(environments)} environments'
            }
            
        except Exception as e:
            return {'error': str(e)}, 500


class PipelineIntegration(Resource):
    """Integrate pipeline with deployment - generate both pipeline and deployment configs"""
    def post(self):
        try:
            data = request.get_json()
            
            # Generate pipeline
            pipeline_type = data.get('type', 'gitlab')
            project_name = data.get('project_name', 'myapp')
            docker_image = data.get('docker_image', 'myapp:latest')
            dockerfile = data.get('dockerfile', './Dockerfile')
            stages = data.get('stages', ['build', 'test', 'deploy'])
            env_vars_text = data.get('env_vars', 'ENV=production')
            deploy_strategy = data.get('deploy_strategy', 'rolling')
            enable_environments = data.get('enable_environments', True)
            
            env_vars = parse_env_vars(env_vars_text)
            generator = PipelineGenerator()
            
            if pipeline_type == 'gitlab':
                runner_tags = data.get('runner_tags', 'docker,linux').split(',')
                use_cache = data.get('use_cache', True)
                pipeline_content = generator.generate_gitlab_pipeline(
                    project_name=project_name,
                    docker_image=docker_image,
                    dockerfile=dockerfile,
                    runner_tags=runner_tags,
                    stages=stages,
                    env_vars=env_vars,
                    deploy_strategy=deploy_strategy,
                    use_cache=use_cache,
                    enable_environments=enable_environments,
                    deployment_config_path='deployment.yml'
                )
                filename = '.gitlab-ci.yml'
            elif pipeline_type == 'jenkins':
                agent = data.get('agent', 'any')
                credentials_id = data.get('credentials_id', 'docker-credentials')
                pipeline_content = generator.generate_jenkins_pipeline(
                    project_name=project_name,
                    docker_image=docker_image,
                    dockerfile=dockerfile,
                    agent=agent,
                    credentials_id=credentials_id,
                    stages=stages,
                    env_vars=env_vars,
                    deploy_strategy=deploy_strategy,
                    enable_environments=enable_environments,
                    deployment_config_path='deployment.yml'
                )
                filename = 'Jenkinsfile'
            else:
                return {'error': 'Invalid pipeline type'}, 400
            
            # Generate deployment configs for all environments
            base_config = data.get('base_deployment_config', {
                'deployment': {
                    'image_tag': docker_image,
                    'container_name': project_name,
                    'port_mapping': {'8080': '8080'},
                    'environment': env_vars,
                    'restart_policy': 'unless-stopped',
                    'health_check_endpoint': '/health',
                    'health_check_timeout': 30,
                    'health_check_retries': 10,
                    'network': 'bridge'
                }
            })
            
            environments = ['dev', 'staging', 'prod'] if enable_environments else ['prod']
            deployment_configs = {}
            
            for env in environments:
                env_config = generate_deployment_config_for_environment(
                    base_config, env, docker_image, project_name
                )
                deployment_configs[env] = env_config
                
                # Save config for each environment using new structure
                save_deployment_config(project_name, env_config, env=env, image_tag=docker_image)
            
            # Save main deployment.yml (defaults to dev)
            default_deployment = deployment_configs.get('dev', deployment_configs.get('prod', {}))
            save_deployment_config(project_name, default_deployment, image_tag=docker_image)
            
            # Save pipeline
            pipeline_path = app.config['PIPELINES_DIR'] / filename
            with open(pipeline_path, 'w', encoding='utf-8') as f:
                f.write(pipeline_content)
            
            return {
                'success': True,
                'pipeline': {
                    'content': pipeline_content,
                    'filename': filename,
                    'path': str(pipeline_path)
                },
                'deployment_configs': deployment_configs,
                'environments': environments,
                'message': 'Pipeline and deployment configs generated successfully'
            }
            
        except Exception as e:
            return {'error': str(e)}, 500


class DeploymentConfig(Resource):
    """Get or update deployment configuration"""
    def get(self):
        """Get default deployment configuration"""
        default_config = {
            'deployment': {
                'image_tag': 'myapp:latest',
                'container_name': 'myapp',
                'port_mapping': {'8080': '8080'},
                'environment': {'ENV': 'production'},
                'volumes': {},
                'restart_policy': 'unless-stopped',
                'health_check_endpoint': '/health',
                'health_check_timeout': 30,
                'health_check_retries': 10,
                'cpu_limit': '0.5',
                'memory_limit': '512m',
                'network': 'bridge'
            }
        }
        
        # Try to load existing config
        config_path = app.config['CONFIG_DIR'] / 'deployment.yml'
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded_config = yaml.safe_load(f) or default_config
                    # Normalize config
                    if 'deployment' in loaded_config:
                        deployment = loaded_config['deployment']
                        # Convert resources to cpu_limit and memory_limit if needed
                        if 'resources' in deployment:
                            resources = deployment.pop('resources')
                            if 'cpu_limit' not in deployment:
                                deployment['cpu_limit'] = resources.get('cpu_limit')
                            if 'memory_limit' not in deployment:
                                deployment['memory_limit'] = resources.get('memory_limit')
                        # Ensure required fields are present
                        if 'volumes' not in deployment:
                            deployment['volumes'] = {}
                        if 'port_mapping' not in deployment:
                            deployment['port_mapping'] = {}
                        if 'environment' not in deployment:
                            deployment['environment'] = {}
                    default_config = loaded_config
            except:
                pass
        
        return {'config': default_config}
    
    def post(self):
        """Save deployment configuration"""
        try:
            data = request.get_json()
            config = data.get('config')
            
            if not config:
                return {'error': 'No config provided'}, 400
            
            # Normalize deployment config: ensure all required fields are present
            if 'deployment' in config:
                deployment = config['deployment']
                
                # Convert resources to cpu_limit and memory_limit if needed
                if 'resources' in deployment:
                    resources = deployment.pop('resources')
                    if 'cpu_limit' not in deployment:
                        deployment['cpu_limit'] = resources.get('cpu_limit')
                    if 'memory_limit' not in deployment:
                        deployment['memory_limit'] = resources.get('memory_limit')
                
                # Ensure required fields have default values
                if 'volumes' not in deployment or not deployment['volumes']:
                    deployment['volumes'] = {}
                if 'port_mapping' not in deployment or not deployment['port_mapping']:
                    deployment['port_mapping'] = {}
                if 'environment' not in deployment or not deployment['environment']:
                    deployment['environment'] = {}
            
            # Save config to deployment directory with unique ID
            container_name = config.get('deployment', {}).get('container_name', 'myapp')
            image_tag = config.get('deployment', {}).get('image_tag', 'latest')
            config_path = save_deployment_config(container_name, config, image_tag=image_tag)
            
            return {
                'success': True,
                'message': 'Configuration saved',
                'path': str(config_path),
                'deployment_id': config_path.parent.name
            }
            
        except Exception as e:
            return {'error': str(e)}, 500


class DeploymentExecute(Resource):
    """Execute deployment using DockerPilot"""
    def post(self):
        try:
            data = request.get_json()
            config = data.get('config')
            strategy = data.get('strategy', 'rolling')
            
            if not config:
                return {'error': 'No deployment config provided'}, 400
            
            # Normalize deployment config: ensure all required fields are present
            if 'deployment' in config:
                deployment = config['deployment']
                
                # Convert resources to cpu_limit and memory_limit
                if 'resources' in deployment:
                    resources = deployment.pop('resources')
                    if 'cpu_limit' not in deployment:
                        deployment['cpu_limit'] = resources.get('cpu_limit')
                    if 'memory_limit' not in deployment:
                        deployment['memory_limit'] = resources.get('memory_limit')
                
                # Ensure required fields have default values
                if 'volumes' not in deployment or not deployment['volumes']:
                    deployment['volumes'] = {}
                if 'port_mapping' not in deployment or not deployment['port_mapping']:
                    deployment['port_mapping'] = {}
                if 'environment' not in deployment or not deployment['environment']:
                    deployment['environment'] = {}
                
                # Set optional fields with defaults if not present
                if 'restart_policy' not in deployment:
                    deployment['restart_policy'] = 'unless-stopped'
                if 'health_check_endpoint' not in deployment:
                    deployment['health_check_endpoint'] = '/health'
                if 'health_check_timeout' not in deployment:
                    deployment['health_check_timeout'] = 30
                if 'health_check_retries' not in deployment:
                    deployment['health_check_retries'] = 10
                if 'network' not in deployment:
                    deployment['network'] = 'bridge'
            
            # Save config to deployment directory with unique ID
            container_name = config.get('deployment', {}).get('container_name', 'myapp')
            image_tag = config.get('deployment', {}).get('image_tag', 'latest')
            config_path = save_deployment_config(container_name, config, image_tag=image_tag)
            
            # Execute DockerPilot using the main config file
            try:
                result = subprocess.run(
                    ['dockerpilot', 'deploy', 'config', str(config_path), '--type', strategy],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode == 0:
                    # Save to history
                    history_path = app.config['CONFIG_DIR'] / 'deployment_history.json'
                    history = []
                    if history_path.exists():
                        with open(history_path, 'r', encoding='utf-8') as f:
                            history = json.load(f)
                    
                    history.append({
                        'timestamp': datetime.now().isoformat(),
                        'strategy': strategy,
                        'status': 'success',
                        'output': result.stdout,
                        'config_path': str(config_path)
                    })
                    
                    with open(history_path, 'w', encoding='utf-8') as f:
                        json.dump(history[-50:], f, indent=2)  # Keep last 50
                    
                    return {
                        'success': True,
                        'message': 'Deployment executed successfully',
                        'output': result.stdout,
                        'config_path': str(config_path)
                    }
                else:
                    return {
                        'success': False,
                        'error': result.stderr,
                        'output': result.stdout,
                        'config_path': str(config_path)
                    }, 500
                    
            except subprocess.TimeoutExpired:
                return {'error': 'Deployment timeout'}, 500
            except FileNotFoundError:
                return {'error': 'DockerPilot not found. Please install DockerPilot.'}, 500
                    
        except Exception as e:
            return {'error': str(e)}, 500


class DeploymentHistory(Resource):
    """Get deployment history"""
    def get(self):
        history_path = app.config['CONFIG_DIR'] / 'deployment_history.json'
        if history_path.exists():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                return {'history': history}
            except:
                pass
        
        return {'history': []}


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
            
            # Promote all containers with their configs
            pilot = get_dockerpilot()
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
                    success = pilot.environment_promotion(from_env, to_env, config_path_str, skip_backup)
                    
                    if success:
                        results['success'].append(container_name)
                        app.logger.info(f"Successfully promoted {container_name}")
                    else:
                        results['failed'].append(container_name)
                        app.logger.error(f"Failed to promote {container_name}")
                except Exception as e:
                    app.logger.error(f"Error promoting {container_name}: {e}")
                    results['failed'].append(container_name)
            
            # Return summary
            if results['failed']:
                return {
                    'success': False,
                    'message': f'Promoted {len(results["success"])}/{len(configs_to_promote)} containers',
                    'successful': results['success'],
                    'failed': results['failed'],
                    'error': f'Some promotions failed: {", ".join(results["failed"])}'
                }, 500
            else:
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


class CheckSudoRequired(Resource):
    """Check if backup will require sudo password"""
    def post(self):
        try:
            data = request.get_json()
            container_name = data.get('container_name')
            
            if not container_name:
                return {'error': 'container_name is required'}, 400
            
            pilot = get_dockerpilot()
            
            # Check if sudo will be required and get mount information
            requires_sudo, privileged_paths, mount_info = pilot._check_sudo_required_for_backup(container_name)
            
            # Check if there are large mounts (> 500GB used OR > 1TB capacity)
            large_mounts = mount_info.get('large_mounts', [])
            total_size_tb = mount_info.get('total_size_tb', 0)
            has_large_mounts = len(large_mounts) > 0
            
            # Calculate total capacity from large mounts
            total_capacity_tb = sum(m.get('total_capacity_tb', 0) for m in large_mounts)
            if total_capacity_tb == 0:
                total_capacity_tb = total_size_tb  # Fallback to used size
            
            return {
                'requires_sudo': requires_sudo,
                'privileged_paths': privileged_paths[:5],  # First 5 paths
                'total_privileged_paths': len(privileged_paths),
                'has_large_mounts': has_large_mounts,
                'large_mounts': large_mounts[:3],  # First 3 large mounts (with capacity info)
                'total_size_tb': round(total_size_tb, 2),
                'total_size_gb': round(mount_info.get('total_size_gb', 0), 2),
                'total_capacity_tb': round(total_capacity_tb, 2),
                'message': 'Backup will require sudo password' if requires_sudo else 'No sudo required',
                'warning': f'⚠️ Wykryto duże dyski (użyte: {total_size_tb:.2f} TB, pojemność: {total_capacity_tb:.2f} TB). Backup może trwać bardzo długo!' if has_large_mounts else None
            }
            
        except Exception as e:
            app.logger.error(f"Check sudo failed: {e}")
            return {'error': str(e)}, 500


class SudoPassword(Resource):
    """Store sudo password in session for backup operations"""
    def post(self):
        try:
            data = request.get_json()
            sudo_password = data.get('sudo_password')
            
            if not sudo_password:
                return {'error': 'sudo_password is required'}, 400
            
            # Store password in session (only in memory, not logged)
            # Password will be cleared after use or session expiry
            session['sudo_password'] = sudo_password
            session['sudo_password_timestamp'] = datetime.now().isoformat()
            
            # Set session to expire after 5 minutes for security
            session.permanent = True
            app.permanent_session_lifetime = timedelta(minutes=5)
            
            app.logger.info("Sudo password stored in session (not logged)")
            
            return {
                'success': True,
                'message': 'Sudo password stored securely'
            }
            
        except Exception as e:
            app.logger.error(f"Store sudo password failed: {e}")
            return {'error': str(e)}, 500
    
    def delete(self):
        """Clear sudo password from session"""
        try:
            session.pop('sudo_password', None)
            session.pop('sudo_password_timestamp', None)
            return {'success': True, 'message': 'Sudo password cleared'}
        except Exception as e:
            return {'error': str(e)}, 500


class MigrationProgress(Resource):
    """Get migration progress for a container"""
    def get(self):
        try:
            container_name = request.args.get('container_name')
            
            if not container_name:
                # Return all active migrations
                active_migrations = {}
                for name, progress in _migration_progress.items():
                    if progress and progress.get('stage') not in ['completed', 'failed', 'cancelled']:
                        active_migrations[name] = progress
                
                return {
                    'success': True,
                    'active_migrations': active_migrations,
                    'count': len(active_migrations)
                }
            
            # Return progress for specific container
            progress = _migration_progress.get(container_name, None)
            if progress:
                return {
                    'success': True,
                    'progress': progress
                }
            else:
                return {
                    'success': True,
                    'progress': None
                }
        except Exception as e:
            app.logger.error(f"Error getting migration progress: {e}")
            return {'error': str(e)}, 500


class CancelMigration(Resource):
    """Cancel ongoing container migration"""
    def post(self):
        try:
            data = request.get_json()
            container_name = data.get('container_name')
            
            if not container_name:
                return {'error': 'container_name is required'}, 400
            
            # Set cancel flag
            _migration_cancel_flags[container_name] = True
            
            # Update progress to show cancellation
            if container_name in _migration_progress:
                _migration_progress[container_name] = {
                    'stage': 'cancelling',
                    'progress': _migration_progress[container_name].get('progress', 0),
                    'message': f'Cancelling container migration {container_name}...',
                    'timestamp': datetime.now().isoformat()
                }
            
            app.logger.info(f"Cancel flag set for migration {container_name}")
            
            return {
                'success': True,
                'message': f'Cancelling container migration {container_name}. Migration will be stopped at the next checkpoint.'
            }
            
        except Exception as e:
            app.logger.error(f"Cancel migration failed: {e}")
            return {'error': str(e)}, 500


class DeploymentProgress(Resource):
    """Get deployment progress for a container or all active deployments"""
    def get(self):
        try:
            container_name = request.args.get('container_name')
            
            # If no container_name specified, return all active deployments
            if not container_name:
                active_deployments = {}
                completed_deployments = []
                
                for name, progress in _deployment_progress.items():
                    if not progress:
                        continue
                    
                    stage = progress.get('stage', '')
                    # Only return deployments that are not completed/failed/error/cancelled
                    if stage not in ['completed', 'failed', 'error', 'cancelled']:
                        active_deployments[name] = progress
                    else:
                        # Mark completed/cancelled deployments for cleanup (older than 30 seconds)
                        timestamp_str = progress.get('timestamp')
                        if timestamp_str:
                            try:
                                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                                if timestamp.tzinfo is None:
                                    timestamp = timestamp.replace(tzinfo=datetime.now().astimezone().tzinfo)
                                
                                age_seconds = (datetime.now(timestamp.tzinfo) - timestamp).total_seconds()
                                # Clean up completed deployments older than 30 seconds
                                if age_seconds > 30:
                                    completed_deployments.append(name)
                            except (ValueError, TypeError):
                                # If timestamp parsing fails, mark for cleanup anyway
                                completed_deployments.append(name)
                        else:
                            # No timestamp, mark for cleanup immediately
                            completed_deployments.append(name)
                
                # Clean up old completed deployments
                for name in completed_deployments:
                    _deployment_progress.pop(name, None)
                
                return {
                    'success': True,
                    'active_deployments': active_deployments,
                    'count': len(active_deployments)
                }
            
            # Return progress for specific container
            progress = _deployment_progress.get(container_name, None)
            if progress:
                return {
                    'success': True,
                    'progress': progress
                }
            else:
                return {
                    'success': True,
                    'progress': None
                }
        except Exception as e:
            app.logger.error(f"Error getting deployment progress: {e}")
            return {'error': str(e)}, 500


class EnvironmentPromoteSingle(Resource):
    """Promote single container from one environment to another"""
    def post(self):
        try:
            data = request.get_json()
            from_env = data.get('from_env')
            to_env = data.get('to_env')
            container_name = data.get('container_name')
            skip_backup = data.get('skip_backup', False)
            
            if not from_env or not to_env or not container_name:
                return {'error': 'Missing required parameters'}, 400
            
            # Initialize progress tracking
            _deployment_progress[container_name] = {
                'stage': 'initializing',
                'progress': 0,
                'message': f'Inicjalizacja promocji {container_name}...',
                'timestamp': datetime.now().isoformat()
            }
            
            # Find deployment config for this specific container
            configs = find_all_deployment_configs_for_env(from_env)
            config_to_promote = None
            
            for config_item in configs:
                if config_item['container_name'] == container_name:
                    config_to_promote = config_item
                    break
            
            if not config_to_promote:
                del _deployment_progress[container_name]
                return {
                    'success': False,
                    'error': f'No deployment configuration found for {container_name} in {from_env} environment'
                }, 404
            
            app.logger.info(f"Promoting single container {container_name} from {from_env} to {to_env}")
            
            # Update progress
            _deployment_progress[container_name] = {
                'stage': 'preparing',
                'progress': 10,
                'message': 'Preparing promotion...',
                'timestamp': datetime.now().isoformat()
            }
            
            # Get sudo password from session if available
            sudo_password = session.get('sudo_password')
            if sudo_password:
                # Set sudo password in pilot instance for this request
                pilot = get_dockerpilot()
                pilot._sudo_password = sudo_password  # Temporary storage for this operation
                app.logger.info("Using sudo password from session")
            else:
                pilot = get_dockerpilot()
            
            # Set progress callback in pilot
            def update_progress(stage, progress, message):
                _deployment_progress[container_name] = {
                    'stage': stage,
                    'progress': progress,
                    'message': message,
                    'timestamp': datetime.now().isoformat()
                }
            
            pilot._progress_callback = update_progress
            
            config_path_str = config_to_promote['path']
            
            try:
                success = pilot.environment_promotion(from_env, to_env, config_path_str, skip_backup)
                
                # Wait a moment for final progress callback from pilot
                import time
                time.sleep(0.5)
                
                # Check if pilot already set 'completed' via callback
                current_progress = _deployment_progress.get(container_name, {})
                current_stage = current_progress.get('stage', '')
                
                if success:
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
                        'message': f'Container {container_name} promoted from {format_env_name(from_env)} to {format_env_name(to_env)}',
                        'container_name': container_name
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
                    return {
                        'success': False,
                        'error': f'Failed to promote {container_name}'
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
                import threading
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


# Cache for environment status to reduce load
_environment_status_cache = {
    'data': None,
    'timestamp': None,
    'ttl': 2  # Cache for 2 seconds
}

class EnvironmentStatus(Resource):
    """Get status of all environments using DockerPilot"""
    def get(self):
        global _environment_status_cache
        
        # Check cache first
        import time
        current_time = time.time()
        if (_environment_status_cache['data'] is not None and 
            _environment_status_cache['timestamp'] is not None and
            current_time - _environment_status_cache['timestamp'] < _environment_status_cache['ttl']):
            app.logger.debug("Returning cached environment status")
            return _environment_status_cache['data']
        
        try:
            environments = ['dev', 'staging', 'prod']
            env_status = {}
            
            # Check if remote server is selected
            server_config = get_selected_server_config()
            
            containers = []
            images = []
            
            if server_config:
                # Use remote server via SSH
                try:
                    # Get containers via SSH - use tab-separated format for easier parsing
                    # Use raw string to avoid issues with curly braces in Go template syntax
                    containers_output = execute_docker_command_via_ssh(
                        server_config,
                        r"ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}'"
                    )
                    # Parse tab-separated lines
                    for line in containers_output.strip().split('\n'):
                        line = line.strip()
                        if line:
                            parts = line.split('\t')
                            if len(parts) >= 4:
                                container_name = parts[0].lstrip('/')
                                containers.append({
                                    'name': container_name,
                                    'image': parts[1],
                                    'state': parts[2].lower(),
                                    'status': parts[3]
                                })
                    
                    # Get images via SSH
                    images_output = execute_docker_command_via_ssh(
                        server_config,
                        r"images --format '{{.Repository}}\t{{.Tag}}'"
                    )
                    # Parse tab-separated lines
                    for line in images_output.strip().split('\n'):
                        line = line.strip()
                        if line:
                            parts = line.split('\t')
                            if len(parts) >= 2:
                                repo = parts[0]
                                tag = parts[1]
                                if repo and repo != '<none>':
                                    image_tag = f"{repo}:{tag}"
                                    if image_tag not in images:
                                        images.append(image_tag)
                                
                except Exception as e:
                    app.logger.error(f"Failed to get containers/images from remote server: {e}")
                    return {
                        'error': f'Failed to connect to remote server: {str(e)}',
                        'environments': {env: {'containers': [], 'error': f'Remote server connection failed: {str(e)}'} for env in environments}
                    }, 500
            else:
                # Use local DockerPilot
                pilot = get_dockerpilot()
                
                # Check if Docker client is available
                if not pilot.client or not pilot.container_manager:
                    return {
                        'error': 'Docker client not initialized',
                        'environments': {env: {'containers': [], 'error': 'Docker not available'} for env in environments}
                    }, 500
                
                # Get containers using DockerPilot
                containers_data = pilot.list_containers(show_all=True, format_output='json')
                if isinstance(containers_data, list):
                    for container in containers_data:
                        if isinstance(container, dict):
                            containers.append({
                                'name': container.get('name', ''),
                                'image': container.get('image', ''),
                                'state': container.get('state', '').lower(),
                                'status': container.get('status', '')
                            })
                
                # Get images using DockerPilot
                images_data = pilot.list_images(show_all=True, format_output='json')
                if isinstance(images_data, list):
                    for image in images_data:
                        if isinstance(image, dict):
                            image_tag = image.get('repository', '') + ':' + image.get('tag', 'latest')
                            if image_tag != ':latest':
                                images.append(image_tag)
                        elif isinstance(image, str):
                            images.append(image)
            
            # FIRST: Check deployment history to determine which containers are in PROD
            # This takes priority over configs to ensure recent deployments are correctly mapped
            history_locations = [
                app.config['CONFIG_DIR'] / 'deployment_history.json',
                Path.home() / 'DockerPilot' / 'deployment_history.json',
                Path.cwd() / 'deployment_history.json'
            ]
            
            active_deployments = {}
            container_to_env_map = {}  # Map container base names to environments (PROD takes priority)
            
            for history_path in history_locations:
                if history_path.exists():
                    try:
                        with open(history_path, 'r', encoding='utf-8') as f:
                            history = json.load(f)
                            # Sort by timestamp, most recent first
                            sorted_history = sorted(history, key=lambda x: x.get('timestamp', ''), reverse=True)
                            
                            # Get most recent deployment for each container
                            # Check deployment history for environment information
                            for record in sorted_history:
                                container_name = record.get('container_name', '').lower()
                                if container_name and container_name not in active_deployments:
                                    active_deployments[container_name] = record
                                    
                                    # Check if deployment history has environment info
                                    deployment_env = record.get('environment') or record.get('target_env')
                                    deployment_type = record.get('type', '').lower()
                                    
                                    # Priority: prod > staging > dev
                                    # If type contains 'prod' or 'promotion-prod', it's definitely PROD
                                    if 'prod' in deployment_type or 'promotion-prod' in deployment_type:
                                        container_to_env_map[container_name] = 'prod'
                                        app.logger.info(f"Container {container_name} mapped to PROD based on deployment history type '{deployment_type}' (timestamp: {record.get('timestamp', 'unknown')})")
                                    elif deployment_env == 'prod':
                                        container_to_env_map[container_name] = 'prod'
                                        app.logger.info(f"Container {container_name} mapped to PROD based on deployment history environment (timestamp: {record.get('timestamp', 'unknown')})")
                                    elif deployment_env == 'staging':
                                        # Only map to staging if there's an actual staging deployment config or container with _staging suffix
                                        # Otherwise, staging promotion might have just saved config without deploying
                                        container_to_env_map[container_name] = 'staging'
                                        app.logger.info(f"Container {container_name} mapped to STAGING based on deployment history (timestamp: {record.get('timestamp', 'unknown')})")
                                    elif 'staging' in deployment_type and deployment_env == 'staging':
                                        container_to_env_map[container_name] = 'staging'
                                        app.logger.info(f"Container {container_name} mapped to STAGING based on deployment history type '{deployment_type}' (timestamp: {record.get('timestamp', 'unknown')})")
                                    else:
                                        # Default to prod for recent deployments without explicit env info
                                        container_to_env_map[container_name] = 'prod'
                                        app.logger.info(f"Container {container_name} mapped to PROD based on deployment history (timestamp: {record.get('timestamp', 'unknown')})")
                    except Exception as e:
                        app.logger.warning(f"Failed to load deployment history from {history_path}: {e}")
                        pass
            
            # SECOND: Load deployment configs from unified deployment directory structure
            env_container_names = {}
            all_container_names = set()  # Track all known container names from configs
            loaded_configs = []  # Track which configs were loaded (for debugging)
            
            # Load from unified deployment directory structure
            deployments_dir = app.config['DEPLOYMENTS_DIR']
            if deployments_dir.exists():
                # Find all deployment directories
                all_deployments = find_all_deployment_dirs()
                
                for deployment_dir, metadata in all_deployments:
                    container_name = metadata.get('container_name', '').lower()
                    if not container_name:
                        continue
                    
                    all_container_names.add(container_name)
                    loaded_configs.append(f"deployment: {deployment_dir.name} -> {container_name}")
                    
                    # Check for environment-specific configs
                    for env in environments:
                        env_config_path = deployment_dir / f'deployment-{env}.yml'
                        if env_config_path.exists():
                            try:
                                with open(env_config_path, 'r', encoding='utf-8') as f:
                                    env_config = yaml.safe_load(f) or {}
                                    config_container_name = env_config.get('deployment', {}).get('container_name', '').lower()
                                    # Check if config container name matches base container name or with suffix
                                    base_config_name = config_container_name
                                    for suffix in ['_staging', '_blue', '_green', '_canary', '_new', '_old']:
                                        if base_config_name.endswith(suffix):
                                            base_config_name = base_config_name[:-len(suffix)]
                                            break
                                    
                                    # Match if container name matches config name or base name
                                    if (config_container_name == container_name or 
                                        base_config_name == container_name or
                                        container_name == config_container_name or
                                        (env == 'staging' and container_name == f"{base_config_name}_staging")):
                                        # Store multiple container names per environment (use list)
                                        if env not in env_container_names:
                                            env_container_names[env] = []
                                        # Use base name for mapping
                                        if base_config_name not in env_container_names[env]:
                                            env_container_names[env].append(base_config_name)
                                        # Only set mapping if not already set by deployment history (PROD takes priority)
                                        if base_config_name not in container_to_env_map:
                                            container_to_env_map[base_config_name] = env
                                        app.logger.info(f"Loaded {env} config from {deployment_dir.name}: container={base_config_name} -> {container_to_env_map.get(base_config_name, env)}")
                            except Exception as e:
                                app.logger.warning(f"Failed to load {env_config_path}: {e}")
                    
                    # Check for main deployment.yml
                    main_config_path = deployment_dir / 'deployment.yml'
                    if main_config_path.exists():
                        try:
                            with open(main_config_path, 'r', encoding='utf-8') as f:
                                main_config = yaml.safe_load(f) or {}
                                if main_config.get('deployment', {}).get('container_name', '').lower() == container_name:
                                    # If no env-specific config found and not in deployment history, default to prod
                                    if container_name not in container_to_env_map:
                                        container_to_env_map[container_name] = 'prod'
                                    # Also set for prod environment if not already set
                                    if 'prod' not in env_container_names:
                                        env_container_names['prod'] = []
                                    if container_name not in env_container_names['prod']:
                                        env_container_names['prod'].append(container_name)
                        except Exception as e:
                            app.logger.warning(f"Failed to load {main_config_path}: {e}")
            
            # Also check legacy locations for backward compatibility
            legacy_locations = [
                app.config['CONFIG_DIR'],  # ~/.dockerpilot_extras/
                Path.home() / 'DockerPilot',  # Main project directory
            ]
            
            for config_dir in legacy_locations:
                # Check for legacy deployment-{env}.yml files
                for env in environments:
                    env_config_path = config_dir / f'deployment-{env}.yml'
                    if env_config_path.exists():
                        try:
                            with open(env_config_path, 'r', encoding='utf-8') as f:
                                env_config = yaml.safe_load(f) or {}
                                container_name = env_config.get('deployment', {}).get('container_name', '')
                                if container_name:
                                    container_name_lower = container_name.lower()
                                    # Store multiple container names per environment (use list)
                                    if env not in env_container_names:
                                        env_container_names[env] = []
                                    if container_name_lower not in env_container_names[env]:
                                        env_container_names[env].append(container_name_lower)
                                    all_container_names.add(container_name_lower)
                                    # Only set mapping if not already set by deployment history (PROD takes priority)
                                    if container_name_lower not in container_to_env_map:
                                        container_to_env_map[container_name_lower] = env
                                    loaded_configs.append(f"legacy {env}: {str(env_config_path)} -> {container_name}")
                        except:
                            pass
                
                # Check for legacy named configs (e.g., grafana-deployment.yml)
                try:
                    if hasattr(config_dir, 'glob'):
                        config_files = list(config_dir.glob('*-deployment.yml'))
                        for config_path in config_files:
                            if any(f'deployment-{env}.yml' in str(config_path) for env in environments):
                                continue
                            try:
                                with open(config_path, 'r', encoding='utf-8') as f:
                                    config = yaml.safe_load(f) or {}
                                    container_name = config.get('deployment', {}).get('container_name', '')
                                    if container_name:
                                        container_name_lower = container_name.lower()
                                        all_container_names.add(container_name_lower)
                                        loaded_configs.append(f"legacy named: {str(config_path)} -> {container_name}")
                                        # Only set to prod if not already mapped by deployment history
                                        if container_name_lower not in container_to_env_map:
                                            container_to_env_map[container_name_lower] = 'prod'
                            except:
                                pass
                except:
                    pass
            
            app.logger.info(f"Loaded {len(loaded_configs)} configs: {loaded_configs}")
            app.logger.info(f"Known container names: {all_container_names}")
            app.logger.info(f"Container to env map: {container_to_env_map}")
            
            # Also check deployment history to see which environment is active
            history_locations = [
                app.config['CONFIG_DIR'] / 'deployment_history.json',
                Path.home() / 'DockerPilot' / 'deployment_history.json',
                Path.cwd() / 'deployment_history.json'
            ]
            
            active_deployments = {}
            recent_deployments = []  # Track recent deployments to infer environment
            for history_path in history_locations:
                if history_path.exists():
                    try:
                        with open(history_path, 'r', encoding='utf-8') as f:
                            history = json.load(f)
                            # Sort by timestamp, most recent first
                            sorted_history = sorted(history, key=lambda x: x.get('timestamp', ''), reverse=True)
                            recent_deployments = sorted_history[:10]  # Last 10 deployments
                            
                            # Get most recent deployment for each container
                            for record in sorted_history:
                                container_name = record.get('container_name', '').lower()
                                if container_name and container_name not in active_deployments:
                                    active_deployments[container_name] = record
                                    # Update container_to_env_map based on history
                                    # Check if deployment history has environment info
                                    deployment_env = record.get('environment') or record.get('target_env')
                                    if deployment_env:
                                        # Use environment from deployment history
                                        container_to_env_map[container_name] = deployment_env
                                        app.logger.info(f"Container {container_name} mapped to {deployment_env} based on deployment history (timestamp: {record.get('timestamp', 'unknown')})")
                                    elif container_name not in container_to_env_map:
                                        # Default to prod if no environment info
                                        container_to_env_map[container_name] = 'prod'
                                        app.logger.info(f"Container {container_name} mapped to PROD based on deployment history (timestamp: {record.get('timestamp', 'unknown')})")
                    except:
                        pass
            
            # Track which containers have been assigned to an environment
            # to prevent duplicates across environments
            assigned_containers = set()
            # Store full container lists per environment (before limiting to 5 for display)
            env_containers_full = {}
            
            # Process each environment (process PROD first to take priority)
            env_processing_order = ['prod', 'staging', 'dev']
            for env in env_processing_order:
                if env not in environments:
                    continue
                    
                env_containers = []
                env_images = []
                
                # Find containers for this environment
                # Use container_to_env_map as the primary source of truth
                # This ensures containers are assigned to only one environment
                for c in containers:
                    container_id = c.get('name', '')
                    name_lower = c['name'].lower()
                    
                    # Handle blue/green/canary/staging variants: grafana_blue -> grafana, grafana_staging -> grafana
                    base_name = name_lower
                    for suffix in ['_blue', '_green', '_canary', '_new', '_old', '_staging']:
                        if base_name.endswith(suffix):
                            base_name = base_name[:-len(suffix)]
                            break
                    
                    # Check if this container is mapped to this environment
                    mapped_env = container_to_env_map.get(base_name)
                    
                    # Skip if already assigned to another environment
                    if container_id in assigned_containers:
                        continue
                    
                    # Priority 1: Container with _staging suffix belongs to STAGING
                    if name_lower.endswith('_staging') and env == 'staging':
                        env_containers.append(c)
                        assigned_containers.add(container_id)
                        continue
                    
                    # Priority 2: Containers with _blue/_green suffix belong to PROD (blue-green deployment)
                    if (name_lower.endswith('_blue') or name_lower.endswith('_green')) and env == 'prod':
                        env_containers.append(c)
                        assigned_containers.add(container_id)
                        continue
                    
                    # Priority 3: Check explicit mapping from deployment history/configs
                    if mapped_env == env:
                        # Container is explicitly mapped to this environment
                        env_containers.append(c)
                        assigned_containers.add(container_id)
                        continue
                    
                    # Priority 4: For PROD, check if container has no _staging suffix
                    # This handles containers that are actually running in PROD despite staging history
                    if env == 'prod' and mapped_env is None:
                        # Check if this is a PROD container (no _staging suffix, has deployment-prod.yml or no deployment-staging.yml)
                        has_staging_suffix = name_lower.endswith('_staging')
                        has_blue_green_suffix = name_lower.endswith('_blue') or name_lower.endswith('_green')
                        has_staging_config = base_name in env_container_names.get('staging', [])
                        has_prod_config = base_name in env_container_names.get('prod', [])
                        
                        # Skip blue/green variants (already handled above)
                        if has_blue_green_suffix:
                            continue
                        
                        # If container doesn't have _staging suffix, it's running in PROD
                        # Even if it has deployment-staging.yml (which was just a saved config)
                        if not has_staging_suffix:
                            # Default to PROD for containers without staging suffix
                            env_containers.append(c)
                            assigned_containers.add(container_id)
                            container_to_env_map[base_name] = 'prod'
                            app.logger.info(f"Container {container_id} assigned to PROD (no staging suffix - running in PROD)")
                            continue
                    
                    # Priority 5: Check deployment history
                    if mapped_env is None:
                        has_deployment_history = base_name in active_deployments
                        
                        if has_deployment_history:
                            # Check if deployment history has environment info
                            deployment_record = active_deployments.get(base_name, {})
                            deployment_env = deployment_record.get('environment') or deployment_record.get('target_env')
                            deployment_type = deployment_record.get('type', '').lower()
                            
                            # Only trust staging history if container actually has _staging suffix
                            # Containers without _staging suffix are in PROD, not STAGING
                            if deployment_env == 'staging' and env == 'staging':
                                has_staging_suffix = name_lower.endswith('_staging')
                                # Only assign to staging if it has staging suffix
                                # If no suffix, it's running in PROD (even if deployment-staging.yml exists)
                                if has_staging_suffix:
                                    env_containers.append(c)
                                    assigned_containers.add(container_id)
                                    container_to_env_map[base_name] = env
                                    continue
                            elif deployment_env == env:
                                # Deployment history explicitly says this environment
                                env_containers.append(c)
                                assigned_containers.add(container_id)
                                container_to_env_map[base_name] = env
                                continue
                            elif env == 'prod' and (deployment_type == 'promotion-prod' or deployment_env == 'prod'):
                                # PROD promotion takes priority
                                env_containers.append(c)
                                assigned_containers.add(container_id)
                                container_to_env_map[base_name] = 'prod'
                                continue
                    
                    # Priority 6: Check configs
                    if mapped_env is None:
                        # Check if container name matches expected names for this environment
                        expected_container_names = env_container_names.get(env, [])
                        if isinstance(expected_container_names, str):
                            expected_container_names = [expected_container_names]
                        
                        # Check if base name or full name matches expected names
                        matches_expected = False
                        for expected_name in expected_container_names:
                            if (name_lower == expected_name or 
                                name_lower.startswith(expected_name + '_') or
                                base_name == expected_name):
                                matches_expected = True
                                break
                        
                        # Also check if container has staging suffix and we're in staging environment
                        if env == 'staging' and name_lower.endswith('_staging'):
                            staging_base = name_lower[:-8]  # Remove '_staging'
                            if staging_base in expected_container_names:
                                matches_expected = True
                        
                        if matches_expected:
                            # Check if this container has deployment history that says it's in another environment
                            # Only skip if explicitly mapped to another environment via history
                            if base_name in active_deployments:
                                deployment_record = active_deployments[base_name]
                                deployment_env = deployment_record.get('environment') or deployment_record.get('target_env')
                                deployment_type = deployment_record.get('type', '').lower()
                                # If deployment history says PROD, don't override with staging
                                if (deployment_env == 'prod' or deployment_type == 'promotion-prod') and env != 'prod':
                                    continue
                            
                            # Container matches config for this environment
                            env_containers.append(c)
                            assigned_containers.add(container_id)
                            # Update mapping to reflect assignment
                            container_to_env_map[base_name] = env
                
                # 4. Find images for this environment
                # Check images used by containers in this environment
                for c in env_containers:
                    if c['image'] and c['image'] not in env_images:
                        env_images.append(c['image'])
                
                # Also check for images with env suffix or matching container images
                for img in images:
                    img_lower = img.lower()
                    if (env in img_lower or 
                        img.endswith(f'-{env}') or 
                        img.endswith(f':{env}')):
                        if img not in env_images:
                            env_images.append(img)
                
                # If no images found but we have containers, use their images
                if not env_images and env_containers:
                    for c in env_containers:
                        if c.get('image') and c['image'] not in env_images:
                            env_images.append(c['image'])
                
                # Store full container list before limiting for display
                env_containers_full[env] = env_containers.copy()
                
                running_containers = [c for c in env_containers if c['state'] == 'running']
                stopped_containers = [c for c in env_containers if c['state'] != 'running']
                
                env_status[env] = {
                    'containers': {
                        'total': len(env_containers),
                        'running': len(running_containers),
                        'stopped': len(stopped_containers),
                        'list': env_containers[:5],  # Limit to 5 for display
                        'all': env_containers  # Full list for modal/selection
                    },
                    'images': env_images[:5] if env_images else [],  # Limit to 5
                    'status': 'active' if running_containers else ('inactive' if env_containers else 'empty'),
                    'primary_image': env_images[0] if env_images else None
                }
            
            # After processing all environments, assign unassigned containers to DEV as fallback
            # This ensures new containers without configs are automatically assigned to DEV
            unassigned = [c for c in containers if c.get('name', '') not in assigned_containers]
            if unassigned and 'dev' in env_containers_full:
                # Add unassigned containers to DEV
                for c in unassigned:
                    container_id = c.get('name', '')
                    env_containers_full['dev'].append(c)
                    assigned_containers.add(container_id)
                    
                    name_lower = c['name'].lower()
                    base_name = name_lower
                    for suffix in ['_blue', '_green', '_canary', '_new', '_old']:
                        if base_name.endswith(suffix):
                            base_name = base_name[:-len(suffix)]
                            break
                    container_to_env_map[base_name] = 'dev'
                    app.logger.info(f"Auto-assigned unassigned container '{container_id}' to DEV (default)")
                
                # Update DEV status with unassigned containers
                dev_containers = env_containers_full['dev']
                running_dev = [c for c in dev_containers if c.get('state', '').lower() == 'running']
                stopped_dev = [c for c in dev_containers if c.get('state', '').lower() != 'running']
                
                # Update images
                dev_images = env_status['dev']['images'].copy()
                for c in dev_containers:
                    if c.get('image') and c['image'] not in dev_images:
                        if len(dev_images) < 5:
                            dev_images.append(c['image'])
                
                env_status['dev'] = {
                    'containers': {
                        'total': len(dev_containers),
                        'running': len(running_dev),
                        'stopped': len(stopped_dev),
                        'list': dev_containers[:5]  # Limit to 5 for display
                    },
                    'images': dev_images[:5] if dev_images else [],
                    'status': 'active' if running_dev else ('inactive' if dev_containers else 'empty'),
                    'primary_image': dev_images[0] if dev_images else None
                }
            
            result = {
                'success': True,
                'environments': env_status,
                'debug': {
                    'loaded_configs': loaded_configs,
                    'known_containers': list(all_container_names),
                    'container_mapping': container_to_env_map,
                    'deployments_dir': str(app.config['DEPLOYMENTS_DIR'])
                }
            }
            
            # Update cache
            _environment_status_cache['data'] = result
            _environment_status_cache['timestamp'] = current_time
            
            return result
            
        except Exception as e:
            return {'error': str(e)}, 500


class StatusCheck(Resource):
    """Check Docker and DockerPilot status using DockerPilot API"""
    def get(self):
        status = {
            'docker': {'available': False, 'version': None, 'error': None},
            'dockerpilot': {'available': False, 'version': None, 'error': None}
        }
        
        # Check if remote server is selected
        server_config = get_selected_server_config()
        
        if server_config:
            # Check Docker on remote server via SSH
            try:
                docker_version_output = execute_docker_command_via_ssh(server_config, "--version")
                if docker_version_output:
                    status['docker'] = {
                        'available': True,
                        'version': docker_version_output.strip()
                    }
                else:
                    status['docker']['error'] = 'Could not get Docker version from remote server'
            except Exception as e:
                status['docker']['error'] = f'Remote server connection failed: {str(e)}'
        else:
            # Check Docker via DockerPilot API (local)
            try:
                pilot = get_dockerpilot()
                if pilot.client:
                    # Test Docker connection
                    try:
                        pilot.client.ping()
                        docker_version = pilot.client.version()
                        status['docker'] = {
                            'available': True,
                            'version': docker_version.get('Version', 'Unknown')
                        }
                    except Exception as e:
                        status['docker']['error'] = f'Docker connection failed: {str(e)}'
                else:
                    status['docker']['error'] = 'Docker client not initialized'
            except Exception as e:
                status['docker']['error'] = f'Docker check failed: {str(e)}'
                # Fallback to CLI check
                try:
                    result = subprocess.run(
                        ['docker', '--version'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        status['docker'] = {
                            'available': True,
                            'version': result.stdout.strip()
                        }
                except:
                    pass
        
        # Check DockerPilot
        if server_config:
            # Check DockerPilot on remote server via SSH
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
                    ssh.connect(hostname, port=port, username=username, password=password + totp_code, timeout=10)
                
                # Check if dockerpilot command exists using 'which' or 'command -v'
                import time
                dockerpilot_available = False
                version_str = 'DockerPilot Enhanced'
                last_error = None
                
                # Method 1: Check if dockerpilot is in PATH using 'which'
                try:
                    stdin, stdout, stderr = ssh.exec_command("which dockerpilot 2>&1", timeout=5)
                    time.sleep(0.5)  # Give it more time
                    exit_status = stdout.channel.recv_exit_status()
                    output = stdout.read().decode('utf-8').strip()
                    error_output = stderr.read().decode('utf-8').strip()
                    
                    app.logger.info(f"DockerPilot check - which: exit={exit_status}, output='{output}', error='{error_output}'")
                    
                    if exit_status == 0 and output:
                        dockerpilot_available = True
                        app.logger.info(f"DockerPilot found at: {output}")
                    elif exit_status != 0:
                        # Try 'command -v' as alternative
                        stdin, stdout, stderr = ssh.exec_command("command -v dockerpilot 2>&1", timeout=5)
                        time.sleep(0.5)
                        exit_status = stdout.channel.recv_exit_status()
                        output = stdout.read().decode('utf-8').strip()
                        error_output = stderr.read().decode('utf-8').strip()
                        
                        app.logger.info(f"DockerPilot check - command -v: exit={exit_status}, output='{output}', error='{error_output}'")
                        
                        if exit_status == 0 and output:
                            dockerpilot_available = True
                            app.logger.info(f"DockerPilot found at: {output}")
                        else:
                            last_error = f"which/command -v failed: exit={exit_status}, output='{output}', error='{error_output}'"
                except Exception as e:
                    last_error = f"command -v/which check exception: {str(e)}"
                    app.logger.error(f"DockerPilot check failed: {e}")
                
                # Method 2: If not found by which/command -v, try direct execution
                if not dockerpilot_available:
                    try:
                        # Try to execute dockerpilot directly (it might be a function or alias)
                        stdin, stdout, stderr = ssh.exec_command("bash -c 'type dockerpilot' 2>&1", timeout=5)
                        time.sleep(0.5)
                        exit_status = stdout.channel.recv_exit_status()
                        output = stdout.read().decode('utf-8').strip()
                        
                        app.logger.info(f"DockerPilot check - type: exit={exit_status}, output='{output}'")
                        
                        if exit_status == 0 and ('dockerpilot' in output.lower() or 'is' in output.lower()):
                            dockerpilot_available = True
                            app.logger.info(f"DockerPilot found via type: {output}")
                    except Exception as e:
                        app.logger.debug(f"type check failed: {e}")
                
                # Method 3: Try to execute dockerpilot and see if it responds (even if it shows menu)
                if not dockerpilot_available:
                    try:
                        # Execute dockerpilot with timeout - if it responds (even with menu), it exists
                        stdin, stdout, stderr = ssh.exec_command("timeout 2 bash -c 'dockerpilot' 2>&1 | head -n 5", timeout=3)
                        time.sleep(0.8)
                        output = stdout.read().decode('utf-8')
                        error_output = stderr.read().decode('utf-8')
                        combined = (output + error_output).strip()
                        
                        app.logger.info(f"DockerPilot check - direct exec: output='{combined[:200]}'")
                        
                        if combined and ('dockerpilot' in combined.lower() or 'docker' in combined.lower() or 'managing' in combined.lower()):
                            dockerpilot_available = True
                            app.logger.info("DockerPilot found via direct execution")
                    except Exception as e:
                        app.logger.debug(f"Direct execution check failed: {e}")
                
                # Method 4: If found, try to get version info
                if dockerpilot_available:
                    try:
                        # Try to get version by running dockerpilot with timeout and capturing header
                        stdin, stdout, stderr = ssh.exec_command("timeout 1 bash -c 'dockerpilot' 2>&1 | head -n 10", timeout=3)
                        time.sleep(0.5)
                        output = stdout.read().decode('utf-8')
                        error_output = stderr.read().decode('utf-8')
                        combined = (output + error_output).strip()
                        
                        # Look for version or enhanced info in output
                        if combined:
                            for line in combined.split('\n'):
                                line = line.strip()
                                if 'version' in line.lower() and ('enhanced' in line.lower() or 'dockerpilot' in line.lower()):
                                    version_str = line
                                    break
                                elif 'enhanced' in line.lower() and 'dockerpilot' in line.lower():
                                    version_str = line
                                    break
                                elif 'author' in line.lower() and 'dozey' in line.lower():
                                    version_str = 'DockerPilot Enhanced'
                                    break
                    except Exception as e:
                        app.logger.debug(f"Version extraction failed: {e}, using default")
                
                ssh.close()
                
                if dockerpilot_available:
                    status['dockerpilot'] = {
                        'available': True,
                        'version': version_str
                    }
                    app.logger.info(f"DockerPilot status set to available: {version_str}")
                else:
                    error_msg = last_error or 'DockerPilot is not installed on remote server or not in PATH'
                    status['dockerpilot']['error'] = error_msg
                    app.logger.warning(f"DockerPilot not available on remote server: {error_msg}")
                        
            except Exception as e:
                # DockerPilot might not be installed or not in PATH
                error_msg = str(e).lower()
                if 'command not found' in error_msg or 'not found' in error_msg or 'no such file' in error_msg:
                    status['dockerpilot']['error'] = 'DockerPilot is not installed on remote server or not in PATH'
                else:
                    status['dockerpilot']['error'] = f'Error checking DockerPilot: {str(e)}'
        else:
            # Check DockerPilot locally
            try:
                pilot = get_dockerpilot()
                if pilot.client:
                    # Try to list containers to verify DockerPilot works
                    try:
                        containers = pilot.list_containers(show_all=False, format_output='json')
                        # Get version from __init__
                        try:
                            from dockerpilot import __version__
                            version_str = f'DockerPilot {__version__}'
                        except ImportError:
                            version_str = 'DockerPilot Enhanced'
                        
                        status['dockerpilot'] = {
                            'available': True,
                            'version': version_str
                        }
                    except Exception as e:
                        status['dockerpilot']['error'] = f'DockerPilot API test failed: {str(e)}'
                else:
                    status['dockerpilot']['error'] = 'DockerPilot client not initialized'
            except Exception as e:
                status['dockerpilot']['error'] = f'DockerPilot check failed: {str(e)}'
                # Fallback to CLI check
                try:
                    result = subprocess.run(
                        ['dockerpilot', '--version'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        status['dockerpilot'] = {
                            'available': True,
                            'version': result.stdout.strip()
                        }
                except:
                    pass
        
        return status


class ContainerList(Resource):
    """Get container status summary using DockerPilot API"""
    def get(self):
        try:
            # Check if remote server is selected
            server_config = get_selected_server_config()
            containers = []
            
            if server_config:
                # Use remote server via SSH
                try:
                    app.logger.info(f"Getting containers from remote server: {server_config.get('hostname')}")
                    # Get containers via SSH - use tab-separated format for easier parsing
                    containers_output = execute_docker_command_via_ssh(
                        server_config,
                        r"ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}'"
                    )
                    app.logger.debug(f"Containers output from remote server (first 500 chars): {containers_output[:500]}")
                    
                    # Parse tab-separated lines
                    for line in containers_output.strip().split('\n'):
                        line = line.strip()
                        if line:
                            parts = line.split('\t')
                            if len(parts) >= 4:
                                container_name = parts[0].lstrip('/')
                                state = parts[2].lower()
                                containers.append({
                                    'name': container_name,
                                    'status': parts[3],
                                    'state': state,
                                    'image': parts[1]
                                })
                    
                    app.logger.info(f"Found {len(containers)} containers on remote server")
                except Exception as e:
                    app.logger.error(f"Failed to get containers from remote server: {e}", exc_info=True)
                    return {
                        'success': False,
                        'error': f'Failed to connect to remote server: {str(e)}',
                        'containers': [],
                        'running': 0,
                        'stopped': 0
                    }, 500
            else:
                # Use local DockerPilot
                pilot = get_dockerpilot()
                
                # Check if Docker client is available
                if not pilot.client or not pilot.container_manager:
                    return {
                        'error': 'Docker client not initialized',
                        'containers': [],
                        'running': 0,
                        'stopped': 0
                    }, 500
                
                containers_data = pilot.list_containers(show_all=True, format_output='json')
                
                if isinstance(containers_data, list):
                    for container in containers_data:
                        if isinstance(container, dict):
                            state = container.get('state', '').lower()
                            status = container.get('status', '')
                            name = container.get('name', '')
                            
                            containers.append({
                                'name': name,
                                'status': status,
                                'state': state,
                                'image': container.get('image', '')
                            })
                else:
                    # Fallback to Docker CLI if API returns unexpected format
                    pass
            
            # Calculate summary from containers
            running = 0
            stopped = 0
            
            for container in containers:
                state = container.get('state', '').lower()
                if state == 'running':
                    running += 1
                else:
                    stopped += 1
            
            return {
                'success': True,
                'summary': {
                    'total': len(containers),
                    'running': running,
                    'stopped': stopped
                },
                'containers': containers  # Wszystkie kontenery (limit usunięty dla modalu)
            }
            
        except Exception as e:
            app.logger.error(f"Failed to get containers: {e}")
            return {
                'error': str(e),
                'containers': [],
                'running': 0,
                'stopped': 0
            }, 500


class ExecuteCommand(Resource):
    """Execute Docker or DockerPilot command"""
    def post(self):
        try:
            data = request.get_json()
            program = data.get('program', 'docker')  # 'docker' or 'dockerpilot'
            command = data.get('command', '')
            working_directory = data.get('working_directory', None)  # Optional working directory
            
            if not command:
                return {'error': 'Brak komendy'}, 400
            
            # Bezpieczeństwo - dozwolone tylko podstawowe komendy
            # Można rozszerzyć o whitelistę dozwolonych komend
            allowed_programs = ['docker', 'dockerpilot']
            if program not in allowed_programs:
                return {'error': f'Disallowed program: {program}'}, 400
            
            # Parsuj komendę
            command_parts = command.strip().split()
            if not command_parts:
                return {'error': 'Empty command'}, 400
            
            # Map Docker commands -> DockerPilot for user convenience
            original_command = ' '.join(command_parts)
            exec_converted_to_simple = False  # Flag to track exec -> exec-simple conversion
            
            # Helper function to check if string looks like container ID (12 hex characters)
            def looks_like_container_id(s):
                return len(s) == 12 and all(c in '0123456789abcdefABCDEF' for c in s)
            
            if program == 'dockerpilot' and len(command_parts) > 0:
                docker_command = command_parts[0]
                
                # Check if first argument is container ID (syntax: container ID + command)
                # np. "0694da8cd817 logs" -> "container logs 0694da8cd817"
                if len(command_parts) >= 2 and looks_like_container_id(docker_command):
                    container_id = docker_command
                    actual_command = command_parts[1]
                    container_commands_with_id = ['logs', 'start', 'stop', 'restart', 'remove', 'rm', 
                                                  'pause', 'unpause', 'exec', 'exec-simple', 'inspect']
                    
                    if actual_command in container_commands_with_id:
                        # Specjalna obsługa dla inspect - użyj docker inspect bezpośrednio
                        if actual_command == 'inspect':
                            program = 'docker'
                            command_parts = ['inspect', container_id] + command_parts[2:]
                        else:
                            # Mapuj na container <command> <id>
                            if actual_command == 'rm':
                                actual_command = 'remove'
                            elif actual_command == 'exec-simple':
                                actual_command = 'exec-simple'
                            command_parts = ['container', actual_command, container_id] + command_parts[2:]
                        docker_command = command_parts[0] if len(command_parts) > 0 else ''
                
                docker_to_dockerpilot = {
                    # Container operations
                    'ps': 'container list',
                    'list': 'container list',
                    'ls': 'container list',
                    'images': 'container list-images',
                    'list-img': 'container list-images',
                    'list-images': 'container list-images',
                    'img': 'container list-images',
                    'rmi': 'container remove-image',
                    'remove-image': 'container remove-image',
                    'start': 'container start',
                    'stop': 'container stop',
                    'restart': 'container restart',
                    'rm': 'container remove',
                    'remove': 'container remove',
                    'pause': 'container pause',
                    'unpause': 'container unpause',
                    'exec': 'container exec',
                    'logs': 'container logs',
                    # Monitor operations
                    'stats': 'monitor stats',
                    'health': 'monitor health',
                    'monitor': 'monitor dashboard',
                    # Deploy operations
                    'deploy': 'deploy config',
                    # Other operations
                    'build': 'build',
                    'validate': 'validate',
                    'test': 'test',
                    'promote': 'promote',
                    'alerts': 'alerts',
                    'docs': 'docs',
                    'checklist': 'checklist',
                    # Special commands - użyj docker bezpośrednio
                    'inspect': 'docker_inspect',  # Oznaczenie że to docker inspect
                    'json': 'docker_json',  # Oznaczenie że to docker json format
                }
                
                # Sprawdź czy pierwsza komenda jest aliasem Docker
                if docker_command in docker_to_dockerpilot:
                    mapped_cmd = docker_to_dockerpilot[docker_command]
                    # Specjalna obsługa dla inspect i json - użyj docker bezpośrednio
                    if mapped_cmd == 'docker_inspect':
                        program = 'docker'
                        command_parts = ['inspect'] + command_parts[1:]
                    elif mapped_cmd == 'docker_json':
                        # json -> docker ps --format json
                        program = 'docker'
                        if len(command_parts) > 1 and looks_like_container_id(command_parts[1]):
                            # json <container_id> -> docker inspect <container_id> --format '{{json .}}'
                            command_parts = ['inspect', command_parts[1], '--format', '{{json .}}'] + command_parts[2:]
                        else:
                            # json -> docker ps --format json
                            command_parts = ['ps', '--format', 'json'] + command_parts[1:]
                    else:
                        # Zamień pierwszą komendę na odpowiednik DockerPilot
                        mapped_command = mapped_cmd.split()
                        command_parts = mapped_command + command_parts[1:]
                # If user entered command without "container" prefix, check if it's a container command
                # (tylko jeśli program nadal to dockerpilot)
                elif program == 'dockerpilot' and docker_command not in ['container', 'monitor', 'deploy', 'backup', 'config', 'pipeline']:
                    # Sprawdź czy to może być komenda kontenera (np. "list-images" zamiast "container list-images")
                    container_commands = ['list', 'list-images', 'list-img', 'remove-image', 'start', 'stop', 
                                        'restart', 'remove', 'pause', 'unpause', 'stop-remove', 'exec-simple', 
                                        'exec', 'logs']
                    if docker_command in container_commands:
                        command_parts = ['container'] + command_parts
            
            # Specjalna obsługa dla 'container exec' - konwertuj na exec-simple dla interfejsu web
            # container exec jest interaktywny i powoduje timeout w web CLI
            if program == 'dockerpilot' and len(command_parts) >= 3 and command_parts[0] == 'container' and command_parts[1] == 'exec':
                container_name = None
                command_to_execute = None
                i = 2
                
                # Parsuj argumenty container exec
                while i < len(command_parts):
                    arg = command_parts[i]
                    if arg in ['--command', '-c']:
                        # Następny argument to komenda
                        if i + 1 < len(command_parts):
                            command_to_execute = command_parts[i + 1]
                            i += 2
                        else:
                            i += 1
                    elif arg == '--help' or arg == '-h':
                        # Pozwól na --help
                        break
                    elif not arg.startswith('-') and container_name is None:
                        # To jest nazwa kontenera
                        container_name = arg
                        i += 1
                    elif not arg.startswith('-') and container_name is not None and command_to_execute is None:
                        # To może być komenda (jeśli nie ma --command flag)
                        command_to_execute = ' '.join(command_parts[i:])
                        break
                    else:
                        i += 1
                
                # Konwertuj na exec-simple
                if container_name:
                    exec_converted_to_simple = True
                    if command_to_execute:
                        command_parts = ['container', 'exec-simple', container_name, command_to_execute]
                    else:
                        # Brak komendy - użyj prostej komendy która pokaże informację
                        # Używamy pwd jako przykładowej komendy, która zawsze działa
                        command_parts = ['container', 'exec-simple', container_name, 'pwd']
                # Jeśli brak container_name, zostaw jak jest (pokaże błąd z dockerpilot)
            
            env = os.environ.copy()
            
            # Walidacja i normalizacja working_directory
            cwd = None
            if working_directory:
                try:
                    cwd_path = Path(working_directory).resolve()
                    # Security - don't allow going outside home directory
                    home = Path.home()
                    if str(cwd_path).startswith(str(home)):
                        if cwd_path.exists() and cwd_path.is_dir():
                            cwd = str(cwd_path)
                        else:
                            return {'error': f'Working directory does not exist: {working_directory}'}, 400
                    else:
                        return {'error': 'Working directory must be in user home directory'}, 400
                except Exception as e:
                    return {'error': f'Błędna ścieżka katalogu roboczego: {e}'}, 400
            
            # Wykonaj komendę
            try:
                result = subprocess.run(
                    [program] + command_parts,
                    capture_output=True,
                    text=True,
                    timeout=30,  # Maksymalny timeout 30 sekund
                    env=env,
                    cwd=cwd  # Ustaw katalog roboczy
                )
                
                # Dla DockerPilot, usuń powtarzający się banner z outputu
                output = result.stdout
                error_output = result.stderr
                
                if program == 'dockerpilot':
                    # Filtruj tylko banner z stdout - NIE dotykaj tabel ASCII
                    if output:
                        lines = output.split('\n')
                        filtered_lines = []
                        in_banner = False
                        banner_end_markers = ['INFO:', 'usage:', 'dockerpilot:', 'Author:', 'WARNING:', 'ERROR:']
                        
                        for i, line in enumerate(lines):
                            # Wykryj początek bannera (tylko na początku outputu, pierwsze 20 linii)
                            if i < 20 and ('╭───────────────────── Docker Managing Tool' in line or 
                                         ('Docker Managing Tool' in line and 'by Dozey' not in output[max(0, i-5):i+5])):
                                in_banner = True
                                continue
                            
                            # Wykryj koniec bannera
                            if in_banner:
                                if any(marker in line for marker in banner_end_markers):
                                    in_banner = False
                                    # Zachowaj linię z markerem tylko jeśli to ważna informacja
                                    if 'usage:' not in line.lower() and 'dockerpilot: error:' not in line.lower():
                                        filtered_lines.append(line)
                                    continue
                                
                                # Sprawdź czy to nie jest początek tabeli (tabele mają ┏ lub ┡ na początku linii)
                                if line.strip().startswith('┏') or line.strip().startswith('┡') or '🐳' in line:
                                    # To jest początek tabeli, nie bannera - wyjdź z trybu bannera
                                    in_banner = False
                                    filtered_lines.append(line)
                                    continue
                                
                                # Pomiń tylko linie bannera (nie tabel) - sprawdź czy to nie tabela
                                # Tabele mają charakterystyczne wzorce: ││ na początku lub ┃┃
                                is_table_line = (line.strip().startswith('││') or 
                                                line.strip().startswith('┃┃') or
                                                '┏┳' in line or '┡╇' in line or
                                                '└┴' in line)
                                
                                if not is_table_line:
                                    # To jest linia bannera, pomiń tylko jeśli zawiera znaki bannera
                                    if ('│' in line or '╰' in line or '╭' in line or '─' in line) and i < 15:
                                        # Sprawdź czy to nie jest przypadkiem tabela
                                        if '││' not in line and '┃┃' not in line:
                                            continue
                                    # Jeśli linia zawiera tylko tekst bannera, pomiń
                                    if 'Docker' in line and 'Pilot' in line and 'by Dozey' in line:
                                        continue
                            
                            # Zachowaj wszystkie linie (w tym tabele)
                            filtered_lines.append(line)
                        
                        output = '\n'.join(filtered_lines).strip()
                    
                    # Filtruj banner z stderr
                    if error_output:
                        lines = error_output.split('\n')
                        filtered_error_lines = []
                        in_banner = False
                        
                        for line in lines:
                            if '╭───────────────────── Docker Managing Tool' in line or 'Docker Managing Tool' in line:
                                in_banner = True
                                continue
                            
                            if in_banner:
                                if any(marker in line for marker in ['INFO:', 'usage:', 'dockerpilot:', 'Author:']):
                                    in_banner = False
                                    # Zachowaj tylko ważne linie błędów
                                    if 'usage:' in line.lower() or 'dockerpilot:' in line.lower() or 'error:' in line.lower():
                                        filtered_error_lines.append(line)
                                    continue
                                # Pomiń ASCII art
                                if '│' in line or '╰' in line or '╭' in line or '─' in line:
                                    continue
                            
                            filtered_error_lines.append(line)
                        
                        error_output = '\n'.join(filtered_error_lines).strip()
                    
                    # Jeśli output jest pusty po filtrowaniu, użyj error_output
                    if not output and error_output:
                        output = error_output
                        error_output = None
                
                # Dodaj sugestie dla błędów DockerPilot lub konwersji exec
                suggestions = None
                # Jeśli exec został skonwertowany na exec-simple, dodaj informację
                if exec_converted_to_simple:
                    suggestions = {
                        'message': '💡 Note: Command "exec" was automatically converted to "exec-simple" (interactive shell is not available in web CLI).',
                        'commands': ['exec-simple <container> <command> - execute command in container']
                    }
                elif program == 'dockerpilot' and result.returncode != 0:
                    error_text = (error_output or output or '').lower()
                    if 'invalid choice' in error_text:
                        # Sprawdź czy użytkownik próbował użyć komendy Docker
                        first_cmd = original_command.split()[0] if original_command.split() else ''
                        docker_aliases = {
                            'ps': 'container list',
                            'list': 'container list',
                            'ls': 'container list',
                            'images': 'container list-images',
                            'list-img': 'container list-images',
                            'img': 'container list-images',
                            'rmi': 'container remove-image',
                            'start': 'container start',
                            'stop': 'container stop',
                            'restart': 'container restart',
                            'rm': 'container remove',
                            'remove': 'container remove',
                            'pause': 'container pause',
                            'unpause': 'container unpause',
                            'exec': 'container exec',
                            'logs': 'container logs',
                            'stats': 'monitor stats',
                            'health': 'monitor health',
                        }
                        
                        if first_cmd in docker_aliases:
                            suggestions = {
                                'message': f'💡 Wskazówka: "{first_cmd}" to komenda Docker. W DockerPilot użyj:',
                                'commands': [docker_aliases[first_cmd]]
                            }
                        else:
                            # Pełna lista dostępnych komend DockerPilot
                            all_commands = [
                                # Container operations
                                'container list',
                                'container list-images',
                                'container remove-image',
                                'container start',
                                'container stop',
                                'container restart',
                                'container remove',
                                'container pause',
                                'container unpause',
                                'container stop-remove',
                                'container exec-simple',
                                'container exec',
                                'container logs',
                                # Monitor operations
                                'monitor dashboard',
                                'monitor live',
                                'monitor stats',
                                'monitor health',
                                # Deploy operations
                                'deploy config',
                                'deploy init',
                                'deploy history',
                                'deploy quick',
                                # Other operations
                                'build',
                                'validate',
                                'backup create',
                                'backup restore',
                                'config export',
                                'config import',
                                'pipeline create',
                                'test',
                                'promote',
                                'alerts',
                                'docs',
                                'checklist'
                            ]
                            
                            # Wyciągnij dostępne opcje z błędu
                            if 'choose from' in (error_output or output or '').lower():
                                suggestions = {
                                    'message': 'Dostępne komendy DockerPilot:',
                                    'commands': all_commands
                                }
                            else:
                                suggestions = {
                                    'message': 'Użyj jednej z dostępnych komend:',
                                    'commands': all_commands[:10]  # Pokaż pierwsze 10 jako przykład
                                }
                
                return {
                    'success': result.returncode == 0,
                    'output': output,
                    'error': error_output if error_output else None,
                    'return_code': result.returncode,
                    'command': f'{program} {" ".join(command_parts)}',
                    'suggestions': suggestions
                }
            except subprocess.TimeoutExpired:
                return {'error': 'Command exceeded time limit (30s)'}, 500
            except FileNotFoundError:
                return {'error': f'{program} not found'}, 500
            except Exception as e:
                return {'error': str(e)}, 500
                
        except Exception as e:
            return {'error': str(e)}, 500


class GetCommandHelp(Resource):
    """Get help/available commands for Docker or DockerPilot"""
    def get(self):
        try:
            program = request.args.get('program', 'docker')
            
            if program not in ['docker', 'dockerpilot']:
                return {'error': 'Niedozwolony program'}, 400
            
            # Pobierz pomoc dla programu
            try:
                if program == 'docker':
                    result = subprocess.run(
                        [program, '--help'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                else:  # dockerpilot
                    result = subprocess.run(
                        [program, '--help'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                
                if result.returncode == 0 or result.stderr:
                    # Często help jest w stderr lub stdout
                    help_text = result.stdout + result.stderr
                    return {
                        'success': True,
                        'help': help_text
                    }
                else:
                    return {
                        'success': False,
                        'error': 'Failed to get help'
                    }
            except FileNotFoundError:
                return {'error': f'{program} not found'}, 500
            except Exception as e:
                return {'error': str(e)}, 500
                
        except Exception as e:
            return {'error': str(e)}, 500


class DockerPilotCommands(Resource):
    """Get all available DockerPilot commands"""
    def get(self):
        """Return comprehensive list of all DockerPilot commands"""
        commands = {
            'container': {
                'description': 'Container operations',
                'commands': [
                    {'name': 'container list', 'aliases': ['ps', 'list', 'ls'], 'description': 'List containers'},
                    {'name': 'container list-images', 'aliases': ['images', 'list-img', 'img'], 'description': 'List Docker images'},
                    {'name': 'container remove-image', 'aliases': ['rmi'], 'description': 'Remove Docker image(s)'},
                    {'name': 'container start', 'aliases': ['start'], 'description': 'Start container(s)'},
                    {'name': 'container stop', 'aliases': ['stop'], 'description': 'Stop container(s)'},
                    {'name': 'container restart', 'aliases': ['restart'], 'description': 'Restart container(s)'},
                    {'name': 'container remove', 'aliases': ['rm', 'remove'], 'description': 'Remove container(s)'},
                    {'name': 'container pause', 'aliases': ['pause'], 'description': 'Pause container(s)'},
                    {'name': 'container unpause', 'aliases': ['unpause'], 'description': 'Unpause container(s)'},
                    {'name': 'container stop-remove', 'aliases': [], 'description': 'Stop and remove container(s) in one operation'},
                    {'name': 'container exec-simple', 'aliases': [], 'description': 'Execute command non-interactively'},
                    {'name': 'container exec', 'aliases': ['exec'], 'description': 'Execute interactive command in container(s)'},
                    {'name': 'container logs', 'aliases': ['logs'], 'description': 'View container logs'},
                ]
            },
            'monitor': {
                'description': 'Container monitoring',
                'commands': [
                    {'name': 'monitor dashboard', 'aliases': ['monitor'], 'description': 'Multi-container dashboard'},
                    {'name': 'monitor live', 'aliases': [], 'description': 'Live monitoring with screen clearing'},
                    {'name': 'monitor stats', 'aliases': ['stats'], 'description': 'Get one-time container statistics'},
                    {'name': 'monitor health', 'aliases': ['health'], 'description': 'Test health check endpoint'},
                ]
            },
            'deploy': {
                'description': 'Deployment operations',
                'commands': [
                    {'name': 'deploy config', 'aliases': ['deploy'], 'description': 'Deploy from configuration file'},
                    {'name': 'deploy init', 'aliases': [], 'description': 'Create deployment configuration template'},
                    {'name': 'deploy history', 'aliases': [], 'description': 'Show deployment history'},
                    {'name': 'deploy quick', 'aliases': [], 'description': 'Quick deployment (build + replace)'},
                ]
            },
            'other': {
                'description': 'Other operations',
                'commands': [
                    {'name': 'build', 'aliases': ['build'], 'description': 'Build Docker image from Dockerfile'},
                    {'name': 'validate', 'aliases': ['validate'], 'description': 'Validate system requirements'},
                    {'name': 'backup create', 'aliases': [], 'description': 'Create deployment backup'},
                    {'name': 'backup restore', 'aliases': [], 'description': 'Restore from backup'},
                    {'name': 'config export', 'aliases': [], 'description': 'Export configuration'},
                    {'name': 'config import', 'aliases': [], 'description': 'Import configuration'},
                    {'name': 'pipeline create', 'aliases': [], 'description': 'Create CI/CD pipeline'},
                    {'name': 'test', 'aliases': ['test'], 'description': 'Integration testing'},
                    {'name': 'promote', 'aliases': ['promote'], 'description': 'Environment promotion'},
                    {'name': 'alerts', 'aliases': ['alerts'], 'description': 'Setup monitoring alerts'},
                    {'name': 'docs', 'aliases': ['docs'], 'description': 'Generate documentation'},
                    {'name': 'checklist', 'aliases': ['checklist'], 'description': 'Generate production checklist'},
                ]
            }
        }
        
        # Flatten all commands for easy access
        all_commands = []
        for category, data in commands.items():
            for cmd in data['commands']:
                all_commands.append(cmd['name'])
        
        return {
            'success': True,
            'commands': commands,
            'all_commands': all_commands,
            'docker_aliases': {
                'ps': 'container list',
                'list': 'container list',
                'ls': 'container list',
                'images': 'container list-images',
                'list-img': 'container list-images',
                'img': 'container list-images',
                'rmi': 'container remove-image',
                'start': 'container start',
                'stop': 'container stop',
                'restart': 'container restart',
                'rm': 'container remove',
                'remove': 'container remove',
                'pause': 'container pause',
                'unpause': 'container unpause',
                'exec': 'container exec',
                'logs': 'container logs',
                'stats': 'monitor stats',
                'health': 'monitor health',
                'monitor': 'monitor dashboard',
                'deploy': 'deploy config',
                'build': 'build',
                'validate': 'validate',
                'test': 'test',
                'promote': 'promote',
                'alerts': 'alerts',
                'docs': 'docs',
                'checklist': 'checklist',
            }
        }


class DockerImages(Resource):
    """List available Docker images"""
    def get(self):
        try:
            # Pobierz listę obrazów Docker z dodatkowymi informacjami
            result = subprocess.run(
                ['docker', 'images', '--format', '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedAt}}'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                images = []
                images_full = []
                
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    
                    parts = line.split('|')
                    if len(parts) >= 2:
                        repo = parts[0] if parts[0] else '<none>'
                        tag = parts[1] if parts[1] else '<none>'
                        image_id = parts[2] if len(parts) > 2 else ''
                        size = parts[3] if len(parts) > 3 else ''
                        created = parts[4] if len(parts) > 4 else ''
                        
                        image_name = f'{repo}:{tag}'
                        images.append(image_name)
                        images_full.append({
                            'name': image_name,
                            'repository': repo,
                            'tag': tag,
                            'id': image_id[:12] if image_id else '',  # Skrócony ID
                            'size': size,
                            'created': created
                        })
                
                # Usuń duplikaty i posortuj
                images = sorted(list(set(images)))
                # Usuń duplikaty z pełnych informacji
                seen = set()
                unique_full = []
                for img in images_full:
                    if img['name'] not in seen:
                        seen.add(img['name'])
                        unique_full.append(img)
                images_full = sorted(unique_full, key=lambda x: x['name'])
                
                return {
                    'success': True,
                    'images': images,
                    'images_full': images_full
                }
            else:
                return {
                    'success': False,
                    'error': result.stderr or 'Failed to get image list'
                }, 500
                
        except FileNotFoundError:
            return {'error': 'Docker not found'}, 500
        except Exception as e:
            return {'error': str(e)}, 500


class DockerfilePaths(Resource):
    """Find Dockerfile paths in the system"""
    def get(self):
        try:
            dockerfiles = []
            dockerfiles_full = []  # Pełne ścieżki z informacją
            
            # Opcja 1: Przeszukaj bieżący katalog i podkatalogi (ograniczone do 3 poziomów)
            current_dir = Path.cwd()
            max_depth = 3
            
            for depth in range(max_depth + 1):
                pattern = '**/' * depth + 'Dockerfile*'
                for dockerfile_path in current_dir.glob(pattern):
                    if dockerfile_path.is_file():
                        rel_path = dockerfile_path.relative_to(current_dir)
                        rel_str = f'./{rel_path}'
                        full_str = str(dockerfile_path.resolve())
                        dockerfiles.append(rel_str)
                        dockerfiles_full.append({
                            'relative': rel_str,
                            'full': full_str,
                            'name': dockerfile_path.name
                        })
            
            # Opcja 2: Sprawdź czy DockerPilot ma jakieś projekty/konfiguracje
            try:
                config_dir = app.config['CONFIG_DIR']
                for dockerfile_path in config_dir.rglob('Dockerfile*'):
                    if dockerfile_path.is_file():
                        full_str = str(dockerfile_path.resolve())
                        dockerfiles.append(full_str)
                        dockerfiles_full.append({
                            'relative': full_str,
                            'full': full_str,
                            'name': dockerfile_path.name
                        })
            except:
                pass
            
            # Usuń duplikaty i posortuj
            dockerfiles = sorted(list(set(dockerfiles)))
            # Usuń duplikaty z pełnych ścieżek
            seen = set()
            unique_full = []
            for df in dockerfiles_full:
                if df['full'] not in seen:
                    seen.add(df['full'])
                    unique_full.append(df)
            dockerfiles_full = sorted(unique_full, key=lambda x: x['full'])
            
            # Jeśli nie znaleziono, dodaj domyślne opcje
            if not dockerfiles:
                dockerfiles = ['./Dockerfile', './docker/Dockerfile', './build/Dockerfile']
            
            return {
                'success': True,
                'dockerfiles': dockerfiles,
                'dockerfiles_full': dockerfiles_full  # Pełne informacje
            }
                
        except Exception as e:
            return {'error': str(e)}, 500


class FileBrowser(Resource):
    """Browse files and directories"""
    def get(self):
        try:
            path = request.args.get('path', str(Path.home()))
            path_obj = Path(path)
            
            # Bezpieczeństwo - nie pozwól wyjść poza home
            home = Path.home()
            try:
                path_obj = path_obj.resolve()
                if not str(path_obj).startswith(str(home)):
                    path_obj = home
            except:
                path_obj = home
            
            if not path_obj.exists():
                path_obj = home
            
            items = []
            if path_obj.is_dir():
                for item in sorted(path_obj.iterdir()):
                    try:
                        items.append({
                            'name': item.name,
                            'path': str(item),
                            'is_dir': item.is_dir(),
                            'is_file': item.is_file(),
                            'size': item.stat().st_size if item.is_file() else None
                        })
                    except (PermissionError, OSError):
                        continue
            
            return {
                'success': True,
                'current_path': str(path_obj),
                'parent_path': str(path_obj.parent) if path_obj.parent != path_obj else None,
                'items': items
            }
        except Exception as e:
            return {'error': str(e)}, 500


class PrepareContainerConfig(Resource):
    """Prepare deployment configuration for a container from running container"""
    def post(self):
        try:
            data = request.get_json()
            container_name = data.get('container_name')
            target_env = data.get('target_env', 'dev')  # dev/staging/prod
            
            if not container_name:
                return {'error': 'container_name is required'}, 400
            
            if target_env not in ['dev', 'staging', 'prod']:
                return {'error': 'target_env must be dev, staging, or prod'}, 400
            
            pilot = get_dockerpilot()
            client = pilot.client
            
            try:
                container = client.containers.get(container_name)
            except Exception as e:
                if 'NotFound' in str(type(e).__name__):
                    return {'error': f'Container {container_name} not found'}, 404
                raise
            
            # Extract container configuration - inline the logic from prepare_container_for_promotion
            attrs = container.attrs
            
            # Extract image tag
            image_tag = attrs.get('Config', {}).get('Image', '')
            if not image_tag:
                image_tag = container.image.tags[0] if container.image.tags else container.image.id
            
            # Extract port mappings
            port_mapping = {}
            if 'NetworkSettings' in attrs:
                ports = attrs['NetworkSettings'].get('Ports', {})
                for container_port, host_bindings in ports.items():
                    if host_bindings:
                        port_num = container_port.split('/')[0]
                        host_port = host_bindings[0].get('HostPort', '')
                        if host_port:
                            port_mapping[port_num] = host_port
            
            # Extract environment variables
            environment = {}
            env_list = attrs.get('Config', {}).get('Env', [])
            for env_var in env_list:
                if '=' in env_var:
                    key, value = env_var.split('=', 1)
                    environment[key] = value
            
            # Extract volumes
            volumes = {}
            mounts = attrs.get('Mounts', [])
            for mount in mounts:
                source = mount.get('Source', '')
                destination = mount.get('Destination', '')
                volume_name = mount.get('Name', '')
                
                if destination:
                    if volume_name:
                        volumes[volume_name] = destination
                    elif source and not source.startswith('/var/lib/docker/volumes/'):
                        volumes[source] = destination
            
            # Extract restart policy
            restart_policy = 'no'
            host_config = attrs.get('HostConfig', {})
            restart_policy_config = host_config.get('RestartPolicy', {})
            if restart_policy_config:
                restart_policy = restart_policy_config.get('Name', 'no')
            
            # Extract network mode
            network_mode = host_config.get('NetworkMode', 'bridge')
            if network_mode == 'default':
                network_mode = 'bridge'
            
            # Extract resource limits
            cpu_limit = None
            memory_limit = None
            if 'NanoCpus' in host_config:
                cpu_limit = str(host_config['NanoCpus'] / 1000000000)
            if 'Memory' in host_config and host_config['Memory'] > 0:
                memory_mb = host_config['Memory'] / (1024 * 1024)
                if memory_mb >= 1024:
                    memory_limit = f"{int(memory_mb / 1024)}Gi"
                else:
                    memory_limit = f"{int(memory_mb)}Mi"
            
            # Create deployment config
            deployment_config = {
                'deployment': {
                    'image_tag': image_tag,
                    'container_name': container_name,
                    'port_mapping': port_mapping,
                    'environment': environment,
                    'volumes': volumes,
                    'restart_policy': restart_policy,
                    'network': network_mode,
                    'health_check_endpoint': _detect_health_check_endpoint(image_tag),
                    'health_check_timeout': 30,
                    'health_check_retries': 10
                }
            }
            
            if cpu_limit:
                deployment_config['deployment']['cpu_limit'] = cpu_limit
            if memory_limit:
                deployment_config['deployment']['memory_limit'] = memory_limit
            
            # Environment-specific resource adjustments
            if target_env == 'dev':
                if 'cpu_limit' in deployment_config['deployment']:
                    try:
                        cpu = float(deployment_config['deployment']['cpu_limit'])
                        deployment_config['deployment']['cpu_limit'] = str(max(0.5, cpu * 0.5))
                    except:
                        pass
                if 'memory_limit' in deployment_config['deployment']:
                    mem_str = deployment_config['deployment']['memory_limit']
                    if 'Gi' in mem_str:
                        mem_gb = float(mem_str.replace('Gi', ''))
                        deployment_config['deployment']['memory_limit'] = f"{max(0.5, mem_gb * 0.5)}Gi"
                    elif 'Mi' in mem_str:
                        mem_mb = float(mem_str.replace('Mi', ''))
                        deployment_config['deployment']['memory_limit'] = f"{max(512, mem_mb * 0.5)}Mi"
            
            # Save configuration
            # image_tag is already extracted above
            saved_config_path = save_deployment_config(
                container_name,
                deployment_config,
                env=target_env,
                image_tag=image_tag
            )
            
            return {
                'success': True,
                'message': f'Konfiguracja utworzona dla środowiska {format_env_name(target_env)}',
                'container_name': container_name,
                'image_tag': image_tag,
                'config_path': str(saved_config_path),
                'config': deployment_config
            }
            
        except Exception as e:
            app.logger.error(f"Failed to prepare container config: {e}")
            return {'error': str(e)}, 500


class ImportDeploymentConfig(Resource):
    """Import deployment configuration from existing file"""
    def post(self):
        try:
            data = request.get_json()
            config_file_path = data.get('config_file_path')
            target_env = data.get('target_env', 'dev')  # dev/staging/prod
            container_name_override = data.get('container_name')  # Optional override from frontend
            
            if not config_file_path:
                return {'error': 'config_file_path is required'}, 400
            
            if target_env not in ['dev', 'staging', 'prod']:
                return {'error': 'target_env must be dev, staging, or prod'}, 400
            
            # Convert path to Path object
            config_path = Path(config_file_path)
            
            # Check if file exists
            if not config_path.exists():
                return {'error': f'Config file not found: {config_file_path}'}, 404
            
            # Check if it's a YAML file
            if not config_path.suffix.lower() in ['.yml', '.yaml']:
                return {'error': 'File must be a YAML file (.yml or .yaml)'}, 400
            
            # Read and parse YAML
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    deployment_config = yaml.safe_load(f) or {}
            except Exception as e:
                return {'error': f'Failed to parse YAML file: {str(e)}'}, 400
            
            # Validate config structure
            if 'deployment' not in deployment_config:
                # Try to wrap it if it's flat
                if 'image_tag' in deployment_config or 'container_name' in deployment_config:
                    deployment_config = {'deployment': deployment_config}
                else:
                    return {'error': 'Invalid config structure: missing "deployment" section'}, 400
            
            deployment = deployment_config.get('deployment', {})
            
            # Extract container_name and image_tag
            # Use override if provided, otherwise extract from config
            container_name = container_name_override or deployment.get('container_name')
            image_tag = deployment.get('image_tag')
            
            if not container_name:
                return {'error': 'container_name is required (provide container_name parameter or ensure it exists in deployment config)'}, 400
            
            if not image_tag:
                return {'error': 'image_tag is required in deployment config'}, 400
            
            # If container_name was overridden, update it in the config
            if container_name_override and container_name_override != deployment.get('container_name'):
                deployment_config['deployment']['container_name'] = container_name_override
                app.logger.info(f"Overriding container_name from '{deployment.get('container_name')}' to '{container_name_override}'")
            
            # Save configuration to deployment directory structure
            saved_config_path = save_deployment_config(
                container_name,
                deployment_config,
                env=target_env,
                image_tag=image_tag
            )
            
            return {
                'success': True,
                'message': f'Configuration from {config_path.name} imported for container {container_name} in environment {format_env_name(target_env)}',
                'container_name': container_name,
                'image_tag': image_tag,
                'config_path': str(saved_config_path),
                'source_file': str(config_path)
            }
            
        except Exception as e:
            app.logger.error(f"Failed to import deployment config: {e}")
            return {'error': str(e)}, 500


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
    return app.config['SERVERS_DIR'] / 'servers.json'

def load_servers_config():
    """Load servers configuration from file"""
    config_path = get_servers_config_path()
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            app.logger.error(f"Failed to load servers config: {e}")
            return {'servers': [], 'default_server': 'local'}
    return {'servers': [], 'default_server': 'local'}

def save_servers_config(config):
    """Save servers configuration to file"""
    config_path = get_servers_config_path()
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        app.logger.error(f"Failed to save servers config: {e}")
        return False

def convert_putty_key_to_openssh(ppk_content, passphrase=None):
    """Convert PuTTY private key (.ppk) to OpenSSH format"""
    try:
        from paramiko import RSAKey, DSSKey, ECDSAKey, Ed25519Key
        import base64
        import struct
        
        # Try to parse PuTTY key
        lines = ppk_content.strip().split('\n')
        if 'PuTTY-User-Key-File' not in lines[0]:
            raise ValueError("Not a valid PuTTY key file")
        
        # Simple PPK parser - this is a basic implementation
        # For production, consider using a dedicated library
        key_type = None
        encryption = None
        public_key = None
        private_key = None
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('Encryption:'):
                encryption = line.split(':', 1)[1].strip()
            elif line.startswith('Public-Lines:'):
                num_lines = int(line.split(':', 1)[1].strip())
                public_key = ''.join(lines[i+1:i+1+num_lines])
            elif line.startswith('Private-Lines:'):
                num_lines = int(line.split(':', 1)[1].strip())
                private_key = ''.join(lines[i+1:i+1+num_lines])
            i += 1
        
        # For now, we'll use paramiko's key loading which can handle some formats
        # This is a simplified converter - for full PPK support, use puttykeys library
        raise NotImplementedError("Full PuTTY key conversion requires additional library. Please convert .ppk to OpenSSH format using PuTTYgen or use OpenSSH key format.")
        
    except Exception as e:
        raise ValueError(f"Failed to convert PuTTY key: {str(e)}")

def test_ssh_connection(server_config):
    """Test SSH connection to a server"""
    if not SSH_AVAILABLE:
        return {'success': False, 'error': 'SSH libraries not available'}
    
    try:
        hostname = server_config.get('hostname')
        port = server_config.get('port', 22)
        username = server_config.get('username')
        auth_type = server_config.get('auth_type', 'password')  # password, key, 2fa
        
        if not hostname or not username:
            return {'success': False, 'error': 'Missing hostname or username'}
        
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        
        if auth_type == 'password':
            password = server_config.get('password')
            if not password:
                return {'success': False, 'error': 'Password required for password authentication'}
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                password=password,
                timeout=10
            )
        elif auth_type == 'key':
            key_content = server_config.get('private_key')
            key_passphrase = server_config.get('key_passphrase')
            if not key_content:
                return {'success': False, 'error': 'Private key required for key authentication'}
            
            # Try to load key (support both OpenSSH and PuTTY format)
            try:
                # Try OpenSSH format first
                from io import StringIO
                key_file = StringIO(key_content)
                if key_content.strip().startswith('PuTTY-User-Key-File'):
                    # PuTTY format - convert first
                    raise NotImplementedError("PuTTY key conversion required")
                else:
                    # OpenSSH format
                    key = paramiko.RSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
            except:
                # Try other key types
                try:
                    from io import StringIO
                    key_file = StringIO(key_content)
                    key = paramiko.DSSKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
                except:
                    try:
                        from io import StringIO
                        key_file = StringIO(key_content)
                        key = paramiko.ECDSAKey.from_private_key(key_file, password=key_passphrase if key_passphrase else None)
                    except Exception as e:
                        return {'success': False, 'error': f'Failed to load private key: {str(e)}'}
            
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                pkey=key,
                timeout=10
            )
        elif auth_type == '2fa':
            password = server_config.get('password')
            totp_code = server_config.get('totp_code')
            if not password:
                return {'success': False, 'error': 'Password required for 2FA authentication'}
            if not totp_code:
                return {'success': False, 'error': 'TOTP code required for 2FA authentication'}
            
            # For 2FA, we typically use password + TOTP code
            # This depends on server configuration (may need password+TOTP or just TOTP)
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                password=password + totp_code,  # Some servers concatenate
                timeout=10
            )
        else:
            return {'success': False, 'error': f'Unknown authentication type: {auth_type}'}
        
        # Test connection by running a simple command
        stdin, stdout, stderr = client.exec_command('echo "test"')
        exit_status = stdout.channel.recv_exit_status()
        client.close()
        
        if exit_status == 0:
            return {'success': True, 'message': 'Connection successful'}
        else:
            return {'success': False, 'error': f'Command execution failed with status {exit_status}'}
            
    except paramiko.AuthenticationException:
        return {'success': False, 'error': 'Authentication failed - check credentials'}
    except paramiko.SSHException as e:
        return {'success': False, 'error': f'SSH error: {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': f'Connection failed: {str(e)}'}


class ServerList(Resource):
    """List all configured servers"""
    def get(self):
        try:
            config = load_servers_config()
            servers = config.get('servers', [])
            # Don't return sensitive data (passwords/keys)
            safe_servers = []
            for server in servers:
                safe_server = {
                    'id': server.get('id'),
                    'name': server.get('name'),
                    'hostname': server.get('hostname'),
                    'port': server.get('port', 22),
                    'username': server.get('username'),
                    'auth_type': server.get('auth_type', 'password'),
                    'description': server.get('description', '')
                }
                safe_servers.append(safe_server)
            
            return {
                'success': True,
                'servers': safe_servers,
                'default_server': config.get('default_server', 'local')
            }
        except Exception as e:
            app.logger.error(f"Failed to list servers: {e}")
            return {'error': str(e)}, 500


class ServerCreate(Resource):
    """Create a new server configuration"""
    def post(self):
        if not SSH_AVAILABLE:
            return {'error': 'SSH libraries not available. Install paramiko and cryptography.'}, 503
        
        try:
            data = request.get_json()
            config = load_servers_config()
            
            # Validate required fields
            name = data.get('name')
            hostname = data.get('hostname')
            username = data.get('username')
            auth_type = data.get('auth_type', 'password')
            
            if not name or not hostname or not username:
                return {'error': 'Missing required fields: name, hostname, username'}, 400
            
            # Validate authentication data based on type
            if auth_type == 'password':
                if not data.get('password'):
                    return {'error': 'Password required for password authentication'}, 400
            elif auth_type == 'key':
                if not data.get('private_key'):
                    return {'error': 'Private key required for key authentication'}, 400
            elif auth_type == '2fa':
                if not data.get('password'):
                    return {'error': 'Password required for 2FA authentication'}, 400
                # TOTP code is provided at connection time, not stored
            else:
                return {'error': f'Unknown authentication type: {auth_type}'}, 400
            
            # Generate unique ID
            import uuid
            server_id = str(uuid.uuid4())
            
            # Create server configuration
            server_config = {
                'id': server_id,
                'name': name,
                'hostname': hostname,
                'port': data.get('port', 22),
                'username': username,
                'auth_type': auth_type,
                'description': data.get('description', '')
            }
            
            # Store sensitive data based on auth type
            if auth_type == 'password':
                server_config['password'] = data.get('password')
            elif auth_type == 'key':
                server_config['private_key'] = data.get('private_key')
                if data.get('key_passphrase'):
                    server_config['key_passphrase'] = data.get('key_passphrase')
            elif auth_type == '2fa':
                server_config['password'] = data.get('password')
                # TOTP secret could be stored if needed for future connections
                if data.get('totp_secret'):
                    server_config['totp_secret'] = data.get('totp_secret')
            
            # Add to servers list
            config['servers'].append(server_config)
            
            if save_servers_config(config):
                return {
                    'success': True,
                    'message': f'Server {name} created successfully',
                    'server_id': server_id
                }
            else:
                return {'error': 'Failed to save server configuration'}, 500
                
        except Exception as e:
            app.logger.error(f"Failed to create server: {e}")
            return {'error': str(e)}, 500


class ServerUpdate(Resource):
    """Update an existing server configuration"""
    def put(self, server_id):
        if not SSH_AVAILABLE:
            return {'error': 'SSH libraries not available'}, 503
        
        try:
            data = request.get_json()
            config = load_servers_config()
            
            # Find server
            server_index = None
            for i, server in enumerate(config['servers']):
                if server.get('id') == server_id:
                    server_index = i
                    break
            
            if server_index is None:
                return {'error': 'Server not found'}, 404
            
            server = config['servers'][server_index]
            
            # Update fields
            if 'name' in data:
                server['name'] = data['name']
            if 'hostname' in data:
                server['hostname'] = data['hostname']
            if 'port' in data:
                server['port'] = data['port']
            if 'username' in data:
                server['username'] = data['username']
            if 'description' in data:
                server['description'] = data.get('description', '')
            if 'auth_type' in data:
                server['auth_type'] = data['auth_type']
            
            # Update authentication data
            auth_type = server.get('auth_type', 'password')
            if auth_type == 'password':
                if 'password' in data:
                    server['password'] = data['password']
            elif auth_type == 'key':
                if 'private_key' in data:
                    server['private_key'] = data['private_key']
                if 'key_passphrase' in data:
                    server['key_passphrase'] = data.get('key_passphrase')
            elif auth_type == '2fa':
                if 'password' in data:
                    server['password'] = data['password']
                if 'totp_secret' in data:
                    server['totp_secret'] = data.get('totp_secret')
            
            if save_servers_config(config):
                return {
                    'success': True,
                    'message': f'Server {server["name"]} updated successfully'
                }
            else:
                return {'error': 'Failed to save server configuration'}, 500
                
        except Exception as e:
            app.logger.error(f"Failed to update server: {e}")
            return {'error': str(e)}, 500


class ServerDelete(Resource):
    """Delete a server configuration"""
    def delete(self, server_id):
        try:
            config = load_servers_config()
            
            # Find and remove server
            config['servers'] = [s for s in config['servers'] if s.get('id') != server_id]
            
            # If deleted server was default, reset to local
            if config.get('default_server') == server_id:
                config['default_server'] = 'local'
            
            if save_servers_config(config):
                return {
                    'success': True,
                    'message': 'Server deleted successfully'
                }
            else:
                return {'error': 'Failed to save server configuration'}, 500
                
        except Exception as e:
            app.logger.error(f"Failed to delete server: {e}")
            return {'error': str(e)}, 500


class ServerTest(Resource):
    """Test connection to a server"""
    def post(self, server_id=None):
        if not SSH_AVAILABLE:
            return {'error': 'SSH libraries not available'}, 503
        
        try:
            data = request.get_json() or {}
            
            # If server_id provided, load from config
            if server_id:
                config = load_servers_config()
                server_config = None
                for server in config.get('servers', []):
                    if server.get('id') == server_id:
                        server_config = server.copy()
                        break
                
                if not server_config:
                    return {'error': 'Server not found'}, 404
            else:
                # Test connection using provided config
                server_config = {
                    'hostname': data.get('hostname'),
                    'port': data.get('port', 22),
                    'username': data.get('username'),
                    'auth_type': data.get('auth_type', 'password'),
                    'password': data.get('password'),
                    'private_key': data.get('private_key'),
                    'key_passphrase': data.get('key_passphrase'),
                    'totp_code': data.get('totp_code')  # For 2FA testing
                }
            
            # Test connection
            result = test_ssh_connection(server_config)
            return result
            
        except Exception as e:
            app.logger.error(f"Failed to test server connection: {e}")
            return {'error': str(e)}, 500


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
                'timestamp': datetime.now().isoformat()
            }
            _migration_cancel_flags[container_name] = False
            
            def check_cancel():
                """Check if migration was cancelled"""
                if _migration_cancel_flags.get(container_name, False):
                    raise Exception('Migration was cancelled by user')
            
            def update_progress(stage, progress, message):
                """Update migration progress"""
                if not _migration_cancel_flags.get(container_name, False):
                    _migration_progress[container_name] = {
                        'stage': stage,
                        'progress': progress,
                        'message': message,
                        'timestamp': datetime.now().isoformat()
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
                    
                    # Add callback to check for cancellation during download
                    def download_progress(transferred, total):
                        # Check if migration was cancelled during download
                        if _migration_cancel_flags.get(container_name, False):
                            app.logger.info(f"Migration cancelled during download at {transferred / (1024*1024):.2f} MB")
                            raise Exception('Migration was cancelled by user')
                    
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
                    # Source is remote, target is local - need to download and load image locally
                    update_progress('transferring', 50, 'Downloading image from remote source...')
                    check_cancel()
                    
                    # Download image tar from remote source via SCP
                    import paramiko
                    from io import BytesIO
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    try:
                        # Connect to source server
                        if source_server.get('auth_type') == 'password':
                            ssh.connect(
                                source_server.get('hostname'),
                                port=source_server.get('port', 22),
                                username=source_server.get('username'),
                                password=source_server.get('password'),
                                timeout=30
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
                                timeout=30
                            )
                        
                        # Download tar file via SFTP
                        sftp = ssh.open_sftp()
                        remote_tar_path = f"/tmp/{container_name}_migrated_{datetime.now().strftime('%Y%m%d%H%M%S')}.tar"
                        
                        # Create local temp file for download
                        import tempfile
                        local_tar_path = tempfile.NamedTemporaryFile(delete=False, suffix='.tar')
                        local_tar_path.close()
                        
                        try:
                            sftp.get(remote_tar_path, local_tar_path.name)
                            sftp.close()
                            ssh.close()
                            
                            # Clean up remote tar
                            execute_command_via_ssh(source_server, f"rm -f {remote_tar_path}", check_exit_status=False)
                            
                            # Load image locally
                            update_progress('loading', 70, 'Loading image on local server...')
                            check_cancel()
                            
                            result = subprocess.run(
                                ['docker', 'load', '-i', local_tar_path.name],
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
                            
                            # Clean up local tar
                            if os.path.exists(local_tar_path.name):
                                os.unlink(local_tar_path.name)
                                
                        except Exception as e:
                            if os.path.exists(local_tar_path.name):
                                os.unlink(local_tar_path.name)
                            raise
                    except Exception as e:
                        error_msg = f'Failed to download and load image from remote source: {str(e)}'
                        app.logger.error(f"Migration error during image download: {error_msg}", exc_info=True)
                        update_progress('failed', 0, error_msg)
                        return {'error': error_msg}, 500
                    finally:
                        try:
                            ssh.close()
                        except:
                            pass
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
            
            # Step 5: Pre-flight checks before creating container
            update_progress('validating', 85, 'Validating target server compatibility...')
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
                'target_server': target_server_id
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
        
        config = {
            'image_tag': attrs.get('Config', {}).get('Image', ''),
            'port_mapping': {},
            'environment': {},
            'volumes': {},
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
            
            # Also check ExposedPorts in Config (for ports that are exposed but not bound)
            if 'Config' in attrs:
                exposed_ports = attrs['Config'].get('ExposedPorts', {})
                if exposed_ports:
                    app.logger.debug(f"Found ExposedPorts in Config: {exposed_ports}")
                    for exposed_port in exposed_ports.keys():
                        port_num = exposed_port.split('/')[0]
                        # If port is exposed but not mapped, use the same port number for both
                        if port_num not in config['port_mapping']:
                            config['port_mapping'][port_num] = port_num
                            app.logger.debug(f"Added exposed port mapping: {port_num} -> {port_num}")
            
            app.logger.info(f"Final port_mapping: {config['port_mapping']}")
        
        # Extract environment
        env_list = attrs.get('Config', {}).get('Env', [])
        for env_var in env_list:
            if '=' in env_var:
                key, value = env_var.split('=', 1)
                config['environment'][key] = value
        
        # Extract volumes (skip host bind mounts during migration)
        mounts = attrs.get('Mounts', [])
        for mount in mounts:
            mount_type = mount.get('Type', '')
            source = mount.get('Source', '')
            destination = mount.get('Destination', '')
            volume_name = mount.get('Name', '')
            if not destination:
                continue
            if mount_type == 'bind':
                config['skipped_bind_mounts'].append({'source': source, 'destination': destination})
                continue
            if mount_type == 'volume':
                volume_source = volume_name or source
                if volume_source:
                    config['volumes'][volume_source] = destination
        
        # Extract restart policy
        host_config = attrs.get('HostConfig', {})
        restart_policy_config = host_config.get('RestartPolicy', {})
        if restart_policy_config:
            config['restart_policy'] = restart_policy_config.get('Name', 'no')
        
        # Extract network
        config['network'] = host_config.get('NetworkMode', 'bridge')
        
        # Extract resource limits
        if 'NanoCpus' in host_config:
            config['cpu_limit'] = str(host_config['NanoCpus'] / 1000000000)
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
            
            # Also check ExposedPorts in Config (for ports that are exposed but not bound)
            if 'Config' in attrs:
                exposed_ports = attrs['Config'].get('ExposedPorts', {})
                if exposed_ports:
                    app.logger.debug(f"Found ExposedPorts in Config: {exposed_ports}")
                    for exposed_port in exposed_ports.keys():
                        port_num = exposed_port.split('/')[0]
                        # If port is exposed but not mapped, use the same port number for both
                        if port_num not in config['port_mapping']:
                            config['port_mapping'][port_num] = port_num
                            app.logger.debug(f"Added exposed port mapping: {port_num} -> {port_num}")
            
            app.logger.info(f"Final port_mapping: {config['port_mapping']}")
        
        # Extract environment
        env_list = attrs.get('Config', {}).get('Env', [])
        for env_var in env_list:
            if '=' in env_var:
                key, value = env_var.split('=', 1)
                config['environment'][key] = value
        
        # Extract volumes (skip host bind mounts during migration)
        mounts = attrs.get('Mounts', [])
        for mount in mounts:
            mount_type = mount.get('Type', '')
            source = mount.get('Source', '')
            destination = mount.get('Destination', '')
            volume_name = mount.get('Name', '')
            if not destination:
                continue
            if mount_type == 'bind':
                config['skipped_bind_mounts'].append({'source': source, 'destination': destination})
                continue
            if mount_type == 'volume':
                volume_source = volume_name or source
                if volume_source:
                    config['volumes'][volume_source] = destination
        
        # Extract restart policy
        host_config = attrs.get('HostConfig', {})
        restart_policy_config = host_config.get('RestartPolicy', {})
        if restart_policy_config:
            config['restart_policy'] = restart_policy_config.get('Name', 'no')
        
        # Extract network
        config['network'] = host_config.get('NetworkMode', 'bridge')
        
        # Extract resource limits
        if 'NanoCpus' in host_config:
            config['cpu_limit'] = str(host_config['NanoCpus'] / 1000000000)
        if 'Memory' in host_config and host_config['Memory'] > 0:
            memory_mb = host_config['Memory'] / (1024 * 1024)
            if memory_mb >= 1024:
                config['memory_limit'] = f"{int(memory_mb / 1024)}Gi"
            else:
                config['memory_limit'] = f"{int(memory_mb)}Mi"
        
        # Privileged flag
        config['privileged'] = host_config.get('Privileged', False)
        
        return config
    
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
        
        # Add network
        if config.get('network') and config['network'] != 'bridge':
            cmd_parts.extend(['--network', config['network']])
        
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
            cmd_parts.extend(['--cpus', config['cpu_limit']])
        if config.get('memory_limit'):
            normalized_mem = _normalize_memory_limit(config['memory_limit'])
            if normalized_mem != config['memory_limit']:
                app.logger.info(f"Normalized memory limit for docker run: {config['memory_limit']} -> {normalized_mem}")
            cmd_parts.extend(['--memory', normalized_mem])
        
        # Add entrypoint if present
        if config.get('entrypoint'):
            entrypoint = config['entrypoint']
            if isinstance(entrypoint, list):
                entrypoint_str = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in entrypoint])
            else:
                entrypoint_str = entrypoint
            cmd_parts.extend(['--entrypoint', entrypoint_str])
        
        # Add image
        cmd_parts.append(image_tag)
        
        # Add command if present (after image, as it's the command argument)
        if config.get('command'):
            command = config['command']
            if isinstance(command, list):
                # Extend with command parts, properly quoted if needed
                for cmd_part in command:
                    # Quote if contains spaces or special characters
                    if ' ' in cmd_part or any(char in cmd_part for char in ['&', '|', ';', '<', '>']):
                        cmd_parts.append(f'"{cmd_part}"')
                    else:
                        cmd_parts.append(cmd_part)
            else:
                # If it's a string, use as-is (user's responsibility to quote properly)
                cmd_parts.append(str(command))
        
        # Join all parts with spaces
        # Note: This creates a shell command string, so proper quoting is important
        return ' '.join(cmd_parts)


class ServerSelect(Resource):
    """Select default server for current session"""
    def post(self):
        try:
            data = request.get_json()
            server_id = data.get('server_id', 'local')
            
            app.logger.info(f"Selecting server: {server_id}, session_id: {session.get('_id', 'no-id')}")
            
            # Store in session
            session['selected_server'] = server_id
            session.permanent = True  # Make session persistent
            
            # Also update global config default if specified
            if data.get('set_as_default'):
                config = load_servers_config()
                config['default_server'] = server_id
                save_servers_config(config)
            
            app.logger.info(f"Server {server_id} selected, session now has: {session.get('selected_server')}")
            
            return {
                'success': True,
                'message': f'Server {server_id} selected',
                'server_id': server_id
            }
        except Exception as e:
            app.logger.error(f"Failed to select server: {e}", exc_info=True)
            return {'error': str(e)}, 500
    
    def get(self):
        """Get currently selected server"""
        try:
            selected = session.get('selected_server', 'local')
            app.logger.debug(f"Getting selected server from session: {selected}, session_id: {session.get('_id', 'no-id')}")
            
            config = load_servers_config()
            default = config.get('default_server', 'local')
            
            server_id = selected if selected != 'local' else default
            
            # Return server info if not local
            if server_id != 'local':
                for server in config.get('servers', []):
                    if server.get('id') == server_id:
                        return {
                            'success': True,
                            'server_id': server_id,
                            'server': {
                                'id': server.get('id'),
                                'name': server.get('name'),
                                'hostname': server.get('hostname'),
                                'port': server.get('port', 22),
                                'username': server.get('username'),
                                'auth_type': server.get('auth_type')
                            }
                        }
            
            return {
                'success': True,
                'server_id': 'local',
                'server': None
            }
        except Exception as e:
            app.logger.error(f"Failed to get selected server: {e}", exc_info=True)
            return {
                'success': True,
                'server_id': 'local',
                'server': None
            }


# API Routes
api.add_resource(HealthCheck, '/api/health')
api.add_resource(PipelineGenerate, '/api/pipeline/generate')
api.add_resource(PipelineSave, '/api/pipeline/save')
api.add_resource(PipelineDeploymentConfig, '/api/pipeline/deployment-config')
api.add_resource(PipelineIntegration, '/api/pipeline/integrate')
api.add_resource(DeploymentConfig, '/api/deployment/config')
api.add_resource(DeploymentExecute, '/api/deployment/execute')
api.add_resource(DeploymentHistory, '/api/deployment/history')
api.add_resource(EnvironmentPromote, '/api/environment/promote')
api.add_resource(CancelPromotion, '/api/environment/cancel-promotion')
api.add_resource(CheckSudoRequired, '/api/environment/check-sudo')
api.add_resource(SudoPassword, '/api/environment/sudo-password')
api.add_resource(EnvironmentPromoteSingle, '/api/environment/promote-single')
api.add_resource(DeploymentProgress, '/api/environment/progress')
api.add_resource(EnvironmentStatus, '/api/environment/status')
api.add_resource(PrepareContainerConfig, '/api/environment/prepare-config')
api.add_resource(ImportDeploymentConfig, '/api/environment/import-config')
api.add_resource(StatusCheck, '/api/status')
api.add_resource(ContainerList, '/api/containers')
api.add_resource(DockerImages, '/api/docker/images')
api.add_resource(DockerfilePaths, '/api/docker/dockerfiles')
api.add_resource(FileBrowser, '/api/files/browse')
api.add_resource(ExecuteCommand, '/api/command/execute')
api.add_resource(GetCommandHelp, '/api/command/help')
api.add_resource(DockerPilotCommands, '/api/dockerpilot/commands')
api.add_resource(ServerList, '/api/servers')
api.add_resource(ServerCreate, '/api/servers/create')
api.add_resource(ServerUpdate, '/api/servers/<string:server_id>')
api.add_resource(ServerDelete, '/api/servers/<string:server_id>')
api.add_resource(ServerTest, '/api/servers/<string:server_id>/test', '/api/servers/test')
api.add_resource(ServerSelect, '/api/servers/select')
api.add_resource(ContainerMigrate, '/api/containers/migrate')
api.add_resource(MigrationProgress, '/api/containers/migration-progress')
api.add_resource(CancelMigration, '/api/containers/cancel-migration')


@app.route('/')
def index():
    """Serve React app"""
    return send_from_directory(app.static_folder, 'index.html')


@app.errorhandler(404)
def not_found(e):
    """Handle React Router routes"""
    return send_from_directory(app.static_folder, 'index.html')


if __name__ == '__main__':
    # Development server
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
