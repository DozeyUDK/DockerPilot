"""CLI parser construction for DockerPilot."""

import argparse


def build_cli_parser() -> argparse.ArgumentParser:
    """Create comprehensive CLI parser."""
    try:
        from .. import __version__
    except ImportError:
        __version__ = "Enhanced"

    parser = argparse.ArgumentParser(
        description="Docker Pilot Enhanced - Professional Docker Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--version', action='version', version=f'DockerPilot {__version__}')
    parser.add_argument('--config', '-c', type=str, help='Configuration file path')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO', help='Logging level')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    container_parser = subparsers.add_parser('container', help='Container operations')
    container_subparsers = container_parser.add_subparsers(dest='container_action')

    list_parser = container_subparsers.add_parser('list', help='List containers')
    list_parser.add_argument('--all', '-a', action='store_true', help='Show all containers')
    list_parser.add_argument('--format', choices=['table', 'json'], default='table')

    images_parser = container_subparsers.add_parser('list-images', help='List Docker images')
    images_parser.add_argument('--all', '-a', action='store_true', help='Show all images')
    images_parser.add_argument('--format', choices=['table', 'json'], default='table')
    images_parser.add_argument('--hide-untagged', action='store_true', help='Hide images without tags (dangling images)')

    remove_img_parser = container_subparsers.add_parser('remove-image', help='Remove Docker image(s)')
    remove_img_parser.add_argument('name', help='Image name(s) or ID(s), comma-separated (e.g., image1:tag,image2:tag)')
    remove_img_parser.add_argument('--force', '-f', action='store_true', help='Force removal')

    prune_img_parser = container_subparsers.add_parser('prune-images', help='Remove all dangling images (images without tags)')
    prune_img_parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without actually removing')

    for action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
        action_parser = container_subparsers.add_parser(action, help=f'{action.title()} container(s)')
        action_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2 or id1,id2)')
        if action in ['stop', 'restart']:
            action_parser.add_argument('--timeout', '-t', type=int, default=10, help='Timeout seconds')
        if action == 'remove':
            action_parser.add_argument('--force', '-f', action='store_true', help='Force removal')

    stop_remove_parser = container_subparsers.add_parser('stop-remove', help='Stop and remove container(s) in one operation')
    stop_remove_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated')
    stop_remove_parser.add_argument('--timeout', '-t', type=int, default=10, help='Timeout seconds')

    run_parser = container_subparsers.add_parser('run', help='Run a new container from image')
    run_parser.add_argument('image', nargs='?', help='Docker image name/tag (e.g., nginx:latest)')
    run_parser.add_argument('--name', '-n', help='Container name')
    run_parser.add_argument('--port', '-p', action='append', help='Port mapping (format: container:host, e.g., 80:8080). Can be used multiple times')
    run_parser.add_argument('--env', '-e', action='append', help='Environment variable (format: KEY=VALUE). Can be used multiple times')
    run_parser.add_argument('--volume', '-v', action='append', help='Volume mapping (format: host:container or host:container:mode). Can be used multiple times')
    run_parser.add_argument('--command', '-c', help='Command to run in container')
    run_parser.add_argument('--restart', default='unless-stopped', choices=['no', 'on-failure', 'always', 'unless-stopped'], help='Restart policy')
    run_parser.add_argument('--network', help='Network name or "host" for host network')
    run_parser.add_argument('--privileged', action='store_true', help='Run container in privileged mode')
    run_parser.add_argument('--cpu-limit', help='CPU limit (e.g., 1.5 for 1.5 CPUs)')
    run_parser.add_argument('--memory-limit', '-m', help='Memory limit (e.g., 1g for 1GB, 512m for 512MB)')
    run_parser.add_argument('--interactive', '--more', '-i', action='store_true', help='Interactive mode: ask for all parameters one by one')

    exec_simple_parser = container_subparsers.add_parser('exec-simple', help='Execute command non-interactively')
    exec_simple_parser.add_argument('name', help='Container name or ID')
    exec_simple_parser.add_argument('command', help='Command to execute (e.g., "ls -la")')

    exec_parser = container_subparsers.add_parser('exec', help='Execute interactive command in container(s)')
    exec_parser.add_argument('name', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2)')
    exec_parser.add_argument('--command', '-c', default='/bin/bash', help='Command to execute (default: /bin/bash)')

    logs_parser = container_subparsers.add_parser('logs', help='View container logs')
    logs_parser.add_argument('name', nargs='?', help='Container name(s) or ID(s), comma-separated (e.g., app1,app2)')
    logs_parser.add_argument('--tail', '-n', type=int, default=50, help='Number of lines to show (default: 50)')

    monitor_parser = subparsers.add_parser('monitor', help='Container monitoring')
    monitor_subparsers = monitor_parser.add_subparsers(dest='monitor_action')

    dashboard_parser = monitor_subparsers.add_parser('dashboard', help='Multi-container dashboard')
    dashboard_parser.add_argument('containers', nargs='*', help='Container names (empty for all running)')
    dashboard_parser.add_argument('--duration', '-d', type=int, default=300, help='Monitor duration in seconds')

    live_parser = monitor_subparsers.add_parser('live', help='Live monitoring with screen clearing')
    live_parser.add_argument('container', help='Container name')
    live_parser.add_argument('--duration', '-d', type=int, default=30, help='Monitor duration in seconds')

    stats_parser = monitor_subparsers.add_parser('stats', help='Get one-time container statistics')
    stats_parser.add_argument('container', help='Container name')

    health_parser = monitor_subparsers.add_parser('health', help='Test health check endpoint')
    health_parser.add_argument('port', type=int, help='Port number')
    health_parser.add_argument('--endpoint', '-e', default='/health', help='Health check endpoint')
    health_parser.add_argument('--retries', '-r', type=int, default=10, help='Maximum retries')

    deploy_parser = subparsers.add_parser('deploy', help='Deployment operations')
    deploy_subparsers = deploy_parser.add_subparsers(dest='deploy_action')

    config_deploy_parser = deploy_subparsers.add_parser('config', help='Deploy from configuration file')
    config_deploy_parser.add_argument('config_file', help='Deployment configuration file')
    config_deploy_parser.add_argument('--type', choices=['rolling', 'blue-green', 'canary'], default='rolling', help='Deployment type')

    template_parser = deploy_subparsers.add_parser('init', help='Create deployment configuration template')
    template_parser.add_argument('--output', '-o', default='deployment.yml', help='Output file name')

    history_parser = deploy_subparsers.add_parser('history', help='Show deployment history')
    history_parser.add_argument('--limit', '-l', type=int, default=10, help='Number of records to show')

    quick_deploy_parser = deploy_subparsers.add_parser('quick', help='Quick deployment (build + replace)')
    quick_deploy_parser.add_argument('--dockerfile-path', '-d', default='.', help='Path to Dockerfile directory')
    quick_deploy_parser.add_argument('--image-tag', '-t', required=True, help='Image tag (e.g., myapp:v1.2)')
    quick_deploy_parser.add_argument('--container-name', '-n', required=True, help='Container name')
    quick_deploy_parser.add_argument('--port', '-p', help='Port mapping (format: container:host, e.g., 80:8080)')
    quick_deploy_parser.add_argument('--env', '-e', action='append', help='Environment variable (format: KEY=VALUE)')
    quick_deploy_parser.add_argument('--volume', '-v', action='append', help='Volume mapping (format: host:container)')
    quick_deploy_parser.add_argument('--yaml-config', '-y', help='YAML config file with container settings')
    quick_deploy_parser.add_argument('--no-cleanup', action='store_true', help='Do not remove old image')

    subparsers.add_parser('validate', help='Validate system requirements')

    backup_parser = subparsers.add_parser('backup', help='Backup and restore operations')
    backup_subparsers = backup_parser.add_subparsers(dest='backup_action')
    backup_create_parser = backup_subparsers.add_parser('create', help='Create deployment backup')
    backup_create_parser.add_argument('--path', '-p', help='Backup path')
    backup_restore_parser = backup_subparsers.add_parser('restore', help='Restore from backup')
    backup_restore_parser.add_argument('backup_path', help='Path to backup directory')
    backup_data_parser = backup_subparsers.add_parser('container-data', help='Backup container data (volumes)')
    backup_data_parser.add_argument('container', help='Container name to backup')
    backup_data_parser.add_argument('--path', '-p', help='Backup path (auto-generated if not provided)')
    restore_data_parser = backup_subparsers.add_parser('restore-data', help='Restore container data from backup')
    restore_data_parser.add_argument('container', help='Container name to restore data to')
    restore_data_parser.add_argument('backup_path', help='Path to backup directory')

    config_parser = subparsers.add_parser('config', help='Configuration management')
    config_subparsers = config_parser.add_subparsers(dest='config_action')
    config_export_parser = config_subparsers.add_parser('export', help='Export configuration')
    config_export_parser.add_argument('--output', '-o', default='docker-pilot-config.tar.gz', help='Output archive name')
    config_import_parser = config_subparsers.add_parser('import', help='Import configuration')
    config_import_parser.add_argument('archive', help='Configuration archive path')

    pipeline_parser = subparsers.add_parser('pipeline', help='CI/CD pipeline operations')
    pipeline_subparsers = pipeline_parser.add_subparsers(dest='pipeline_action')
    pipeline_create_parser = pipeline_subparsers.add_parser('create', help='Create CI/CD pipeline')
    pipeline_create_parser.add_argument('--type', choices=['github', 'gitlab', 'jenkins'], default='github', help='Pipeline type')
    pipeline_create_parser.add_argument('--output', '-o', help='Output path')

    test_parser = subparsers.add_parser('test', help='Integration testing')
    test_parser.add_argument('--config', default='integration-tests.yml', help='Test configuration file')

    promote_parser = subparsers.add_parser('promote', help='Environment promotion')
    promote_parser.add_argument('source', help='Source environment')
    promote_parser.add_argument('target', help='Target environment')
    promote_parser.add_argument('--config', help='Deployment configuration path')

    alerts_parser = subparsers.add_parser('alerts', help='Setup monitoring alerts')
    alerts_parser.add_argument('--config', default='alerts.yml', help='Alert configuration file')

    docs_parser = subparsers.add_parser('docs', help='Generate documentation')
    docs_parser.add_argument('--output', '-o', default='docs', help='Output directory')

    build_parser = subparsers.add_parser('build', help='Build Docker image from Dockerfile')
    build_parser.add_argument('dockerfile_path', help='Path to Dockerfile directory')
    build_parser.add_argument('tag', help='Image tag (e.g., myapp:latest)')
    build_parser.add_argument('--no-cache', action='store_true', help='Build without cache')
    build_parser.add_argument('--pull', action='store_true', default=True, help='Pull base image updates')

    checklist_parser = subparsers.add_parser('checklist', help='Generate production checklist')
    checklist_parser.add_argument('--output', '-o', default='production-checklist.md', help='Output file')

    return parser
