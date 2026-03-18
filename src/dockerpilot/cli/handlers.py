"""CLI command dispatching for DockerPilot."""

import sys

from .interactive import run_interactive_menu
from .parser import build_cli_parser


def run_cli(pilot) -> None:
    """Run the command-line interface for a pilot instance."""
    if not pilot.client or not pilot.container_manager:
        pilot.console.print("[bold red]❌ Docker is not available![/bold red]")
        pilot.console.print("[yellow]Please ensure Docker is running and accessible.[/yellow]")
        sys.exit(1)

    parser = build_cli_parser()
    args = parser.parse_args()
    has_container_action = hasattr(args, 'container_action') and args.container_action is not None

    if not args.command and not has_container_action:
        run_interactive_menu(pilot)
        return

    try:
        if args.command == 'container' or has_container_action:
            handle_container_cli(pilot, args)
        elif args.command == 'monitor':
            handle_monitor_cli(pilot, args)
        elif args.command == 'update_restart_policy':
            pilot.update_restart_policy(args.name, args.policy)
        elif args.command == 'run_image':
            pilot.run_image(args.image, args.name, args.ports, args.env, args.volumes, args.detach)
        elif args.command == 'deploy':
            handle_deploy_cli(pilot, args)
        elif args.command == 'validate':
            success = pilot.validate_system_requirements()
            if not success:
                sys.exit(1)
        elif args.command == 'backup':
            handle_backup_cli(pilot, args)
        elif args.command == 'config':
            handle_config_cli(pilot, args)
        elif args.command == 'pipeline':
            handle_pipeline_cli(pilot, args)
        elif args.command == 'test':
            success = pilot.run_integration_tests(args.config)
            if not success:
                sys.exit(1)
        elif args.command == 'promote':
            config_path = getattr(args, 'config', None)
            skip_backup = getattr(args, 'skip_backup', False)
            success = pilot.environment_promotion(args.source, args.target, config_path, skip_backup)
            if not success:
                sys.exit(1)
        elif args.command == 'alerts':
            success = pilot.setup_monitoring_alerts(args.config)
            if not success:
                sys.exit(1)
        elif args.command == 'docs':
            success = pilot.generate_documentation(args.output)
            if not success:
                sys.exit(1)
        elif args.command == 'checklist':
            success = pilot.create_production_checklist(args.output)
            if not success:
                sys.exit(1)
        elif args.command == 'build':
            success = pilot.build_image_standalone(args.dockerfile_path, args.tag, args.no_cache, args.pull)
            if not success:
                sys.exit(1)
        else:
            parser.print_help()
    except Exception as e:
        pilot.logger.error(f"CLI command failed: {e}")
        pilot.console.print(f"[red]❌ Command failed: {e}[/red]")
        sys.exit(1)


def handle_container_cli(pilot, args):
    """Handle container CLI commands with support for multiple targets."""
    if args.container_action == 'list':
        pilot.list_containers(show_all=args.all, format_output=args.format)
    elif args.container_action == 'stop-remove':
        containers = pilot._parse_multi_target(args.name)
        if not containers:
            pilot.console.print("[red]❌ No container names provided[/red]")
            sys.exit(1)

        timeout = args.timeout if hasattr(args, 'timeout') else 10
        all_success = True
        for container in containers:
            pilot.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
            success = pilot.stop_and_remove_container(container, timeout)
            if not success:
                all_success = False

        if not all_success:
            pilot.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
            sys.exit(1)
        pilot.console.print("\n[green]✅ All operations completed successfully[/green]")

    elif args.container_action == 'exec-simple':
        success = pilot.exec_command_non_interactive(args.name, args.command)
        if not success:
            sys.exit(1)

    elif args.container_action in ['start', 'stop', 'restart', 'remove', 'pause', 'unpause']:
        containers = pilot._parse_multi_target(args.name)
        if not containers:
            pilot.console.print("[red]❌ No container names provided[/red]")
            sys.exit(1)

        kwargs = {}
        if hasattr(args, 'timeout'):
            kwargs['timeout'] = args.timeout
        if hasattr(args, 'force'):
            kwargs['force'] = args.force

        all_success = True
        for container in containers:
            pilot.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
            success = pilot.container_operation(args.container_action, container, **kwargs)
            if not success:
                all_success = False

        if not all_success:
            pilot.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
            sys.exit(1)
        pilot.console.print("\n[green]✅ All operations completed successfully[/green]")

    elif args.container_action == 'exec':
        containers = pilot._parse_multi_target(args.name)
        if not containers:
            pilot.console.print("[red]❌ No container names provided[/red]")
            sys.exit(1)

        command = args.command if hasattr(args, 'command') else '/bin/bash'
        for container in containers:
            pilot.console.print(f"\n[cyan]Executing in container: {container}[/cyan]")
            success = pilot.exec_container(container, command)
            if not success:
                pilot.console.print(f"[yellow]⚠️ Failed to exec in {container}, continuing...[/yellow]")

    elif args.container_action == 'logs':
        tail = args.tail if hasattr(args, 'tail') else 50
        if args.name:
            pilot.view_container_logs(args.name, tail)
        else:
            pilot.view_container_logs(None, tail)

    elif args.container_action == 'run':
        interactive = getattr(args, 'interactive', False)
        missing_required = not args.image or not args.name
        if interactive or missing_required:
            pilot._run_container_interactive(args)
        else:
            ports = {}
            if hasattr(args, 'port') and args.port:
                for port_mapping in args.port:
                    try:
                        if ':' in port_mapping:
                            container_port, host_port = port_mapping.split(':')
                            ports[container_port.strip()] = host_port.strip()
                        else:
                            pilot.console.print(f"[yellow]Invalid port format: {port_mapping}. Use container:host[/yellow]")
                    except ValueError:
                        pilot.console.print(f"[yellow]Invalid port format: {port_mapping}[/yellow]")

            environment = {}
            if hasattr(args, 'env') and args.env:
                for env_var in args.env:
                    if '=' in env_var:
                        key, value = env_var.split('=', 1)
                        environment[key.strip()] = value.strip()
                    else:
                        pilot.console.print(f"[yellow]Invalid env format: {env_var}. Use KEY=VALUE[/yellow]")

            volumes = {}
            if hasattr(args, 'volume') and args.volume:
                for volume_mapping in args.volume:
                    if ':' in volume_mapping:
                        parts = volume_mapping.split(':')
                        if len(parts) == 2:
                            host_path, container_path = parts
                            volumes[host_path.strip()] = container_path.strip()
                        elif len(parts) == 3:
                            host_path, container_path, mode = parts
                            volumes[host_path.strip()] = {
                                'bind': container_path.strip(),
                                'mode': mode.strip(),
                            }
                        else:
                            pilot.console.print(f"[yellow]Invalid volume format: {volume_mapping}[/yellow]")
                    else:
                        pilot.console.print(f"[yellow]Invalid volume format: {volume_mapping}[/yellow]")

            success = pilot.run_new_container(
                image_name=args.image,
                name=args.name,
                ports=ports if ports else None,
                command=getattr(args, 'command', None),
                environment=environment if environment else None,
                volumes=volumes if volumes else None,
                restart_policy=getattr(args, 'restart', 'unless-stopped'),
                network=getattr(args, 'network', None),
                privileged=getattr(args, 'privileged', False),
                cpu_limit=getattr(args, 'cpu_limit', None),
                memory_limit=getattr(args, 'memory_limit', None),
            )
            if not success:
                sys.exit(1)

    elif args.container_action == 'list-images':
        hide_untagged = getattr(args, 'hide_untagged', False)
        pilot.list_images(show_all=args.all, format_output=args.format, hide_untagged=hide_untagged)

    elif args.container_action == 'remove-image':
        images = pilot._parse_multi_target(args.name)
        if not images:
            pilot.console.print("[red]❌ No image names provided[/red]")
            sys.exit(1)

        all_success = True
        for image in images:
            pilot.console.print(f"\n[cyan]Processing image: {image}[/cyan]")
            success = pilot.remove_image(image, args.force)
            if not success:
                all_success = False

        if not all_success:
            pilot.console.print("\n[yellow]⚠️ Some operations failed[/yellow]")
            sys.exit(1)
        pilot.console.print("\n[green]✅ All operations completed successfully[/green]")

    elif args.container_action == 'prune-images':
        dry_run = getattr(args, 'dry_run', False)
        result = pilot.prune_dangling_images(dry_run=dry_run)
        if not dry_run and result['images_deleted'] > 0:
            pilot.console.print(f"\n[green]✅ Cleanup completed! Removed {result['images_deleted']} images[/green]")
        elif dry_run:
            if result['images_deleted'] > 0:
                pilot.console.print(f"\n[cyan]ℹ️ Use without --dry-run to actually remove {result['images_deleted']} images[/cyan]")
            else:
                pilot.console.print("\n[yellow]ℹ️ No dangling images to remove[/yellow]")
        elif result['images_deleted'] == 0:
            pilot.console.print("\n[yellow]ℹ️ No dangling images were removed[/yellow]")


def handle_monitor_cli(pilot, args):
    """Handle monitoring CLI commands."""
    if args.monitor_action == 'dashboard' or not args.monitor_action:
        containers = args.containers if hasattr(args, 'containers') and args.containers else None
        duration = args.duration if hasattr(args, 'duration') else 300
        pilot.monitor_containers_dashboard(containers, duration)
    elif args.monitor_action == 'live':
        success = pilot.monitor_container_live(args.container, args.duration)
        if not success:
            sys.exit(1)
    elif args.monitor_action == 'stats':
        success = pilot.get_container_stats_once(args.container)
        if not success:
            sys.exit(1)
    elif args.monitor_action == 'health':
        success = pilot.health_check_standalone(args.port, args.endpoint, timeout=30, max_retries=args.retries)
        if not success:
            sys.exit(1)


def handle_deploy_cli(pilot, args):
    """Handle deployment CLI commands."""
    if args.deploy_action == 'config':
        success = pilot.deploy_from_config(args.config_file, args.type)
        if not success:
            sys.exit(1)
    elif args.deploy_action == 'init':
        output = getattr(args, 'output', 'deployment.yml')
        success = pilot.create_deployment_config(output)
        if not success:
            sys.exit(1)
    elif args.deploy_action == 'history':
        pilot.show_deployment_history(limit=getattr(args, 'limit', 10))
    elif args.deploy_action == 'quick':
        port_mapping = None
        if args.port:
            try:
                container_port, host_port = args.port.split(':')
                port_mapping = {container_port: host_port}
            except ValueError:
                pilot.console.print("[red]Invalid port format. Use container:host (e.g., 80:8080)[/red]")
                sys.exit(1)

        environment = {}
        if args.env:
            for env_var in args.env:
                try:
                    key, value = env_var.split('=', 1)
                    environment[key] = value
                except ValueError:
                    pilot.console.print(f"[red]Invalid env format: {env_var}. Use KEY=VALUE[/red]")
                    sys.exit(1)

        volumes = {}
        if args.volume:
            for volume in args.volume:
                try:
                    host_path, container_path = volume.split(':')
                    volumes[host_path] = {'bind': container_path, 'mode': 'rw'}
                except ValueError:
                    pilot.console.print(f"[red]Invalid volume format: {volume}. Use host:container[/red]")
                    sys.exit(1)

        success = pilot.quick_deploy(
            dockerfile_path=args.dockerfile_path,
            image_tag=args.image_tag,
            container_name=args.container_name,
            port_mapping=port_mapping,
            environment=environment if environment else None,
            volumes=volumes if volumes else None,
            yaml_config=args.yaml_config,
            cleanup_old_image=not args.no_cleanup,
        )
        if not success:
            sys.exit(1)
    else:
        pilot.console.print("[yellow]⚠️ Unknown deploy action[/yellow]")


def handle_backup_cli(pilot, args):
    """Handle backup CLI commands."""
    if args.backup_action == 'create':
        backup_path = getattr(args, 'path', None)
        success = pilot.backup_deployment_state(backup_path)
        if not success:
            sys.exit(1)
    elif args.backup_action == 'restore':
        success = pilot.restore_deployment_state(args.backup_path)
        if not success:
            sys.exit(1)
    elif args.backup_action == 'container-data':
        backup_path = getattr(args, 'path', None)
        success = pilot.backup_container_data(args.container, backup_path)
        if not success:
            sys.exit(1)
    elif args.backup_action == 'restore-data':
        success = pilot.restore_container_data(args.container, args.backup_path)
        if not success:
            sys.exit(1)
    else:
        pilot.console.print("[yellow]⚠️ Unknown backup action[/yellow]")


def handle_config_cli(pilot, args):
    """Handle configuration CLI commands."""
    if args.config_action == 'export':
        success = pilot.export_configuration(args.output)
        if not success:
            sys.exit(1)
    elif args.config_action == 'import':
        success = pilot.import_configuration(args.archive)
        if not success:
            sys.exit(1)
    else:
        pilot.console.print("[yellow]⚠️ Unknown config action[/yellow]")


def handle_pipeline_cli(pilot, args):
    """Handle pipeline CLI commands."""
    if args.pipeline_action == 'create':
        success = pilot.create_pipeline_config(args.type, args.output)
        if not success:
            sys.exit(1)
    else:
        pilot.console.print("[yellow]⚠️ Unknown pipeline action[/yellow]")
