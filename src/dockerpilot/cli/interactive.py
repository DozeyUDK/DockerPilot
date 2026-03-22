"""Interactive CLI flows for DockerPilot."""

from rich.prompt import Confirm, Prompt


def run_interactive_menu(pilot):
    """Simple interactive menu for quick operations."""
    try:
        while True:
            choice = Prompt.ask(
                "\n[bold cyan]Docker Pilot - Interactive Menu[/bold cyan]\n"
                "Container: list, list-img, start, stop, restart, remove, pause, unpause, stop-remove, exec, exec-simple, policy, run_image, logs, remove-image, prune-images, json, build\n"
                "Monitor: monitor, live-monitor, stats, health-check\n"
                "Deploy: quick-deploy, deploy-init, deploy-config, history, promote\n"
                "System: validate, backup-create, backup-restore, alerts, test, pipeline, docs, checklist\n"
                "Config: export-config, import-config\n"
                "Select",
                default="list",
            ).strip().lower()

            if choice == "exit":
                pilot.console.print("[green]Bye![/green]")
                break

            if choice == "list":
                pilot.list_containers(show_all=True, format_output="table")
            elif choice == "list-img":
                hide_untagged = Confirm.ask("Hide untagged images (dangling)?", default=False)
                pilot.list_images(show_all=True, format_output="table", hide_untagged=hide_untagged)
            elif choice in ("start", "stop", "restart", "remove", "pause", "unpause"):
                pilot.list_containers()
                names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2)")
                containers = pilot._parse_multi_target(names_input)
                if not containers:
                    pilot.console.print("[red]No container names provided[/red]")
                    continue

                kwargs = {}
                if choice in ("stop", "restart"):
                    kwargs['timeout'] = int(Prompt.ask("Timeout seconds", default="10"))
                if choice == "remove":
                    kwargs['force'] = Confirm.ask("Force removal?", default=False)

                all_success = True
                for container in containers:
                    pilot.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                    success = pilot.container_operation(choice, container, **kwargs)
                    if not success:
                        all_success = False

                if not all_success:
                    pilot.console.print("[yellow]⚠️ Some operations failed[/yellow]")
                else:
                    pilot.console.print("[green]✅ All operations completed successfully[/green]")
            elif choice == "exec":
                pilot.list_containers()
                names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2)")
                containers = pilot._parse_multi_target(names_input)
                if not containers:
                    pilot.console.print("[red]No container names provided[/red]")
                    continue

                command = Prompt.ask("Command to execute", default="/bin/bash")
                for container in containers:
                    pilot.console.print(f"\n[cyan]Executing in container: {container}[/cyan]")
                    success = pilot.exec_container(container, command)
                    if not success:
                        pilot.console.print(f"[yellow]⚠️ Failed to exec in {container}, continuing...[/yellow]")
            elif choice == "stop-remove":
                pilot.list_containers()
                names_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple)")
                containers = pilot._parse_multi_target(names_input)
                if not containers:
                    pilot.console.print("[red]No container names provided[/red]")
                    continue

                timeout = int(Prompt.ask("Timeout seconds", default="10"))
                all_success = True
                for container in containers:
                    pilot.console.print(f"\n[cyan]Processing container: {container}[/cyan]")
                    success = pilot.stop_and_remove_container(container, timeout)
                    if not success:
                        all_success = False

                if not all_success:
                    pilot.console.print("[yellow]⚠️ Some operations failed[/yellow]")
                else:
                    pilot.console.print("[green]✅ All operations completed successfully[/green]")
            elif choice == "exec-simple":
                pilot.list_containers()
                container_name = Prompt.ask("Container name or ID")
                command = Prompt.ask("Command to execute (e.g., 'ls -la')")
                pilot.exec_command_non_interactive(container_name, command)
            elif choice == "monitor":
                pilot.list_containers()
                containers_input = Prompt.ask("Containers (comma separated, empty = all running)", default="").strip()
                containers = [c.strip() for c in containers_input.split(",")] if containers_input else None
                duration = int(Prompt.ask("Duration seconds", default="60"))
                pilot.monitor_containers_dashboard(containers, duration)
            elif choice == "live-monitor":
                pilot.list_containers()
                container_name = Prompt.ask("Container name")
                duration = int(Prompt.ask("Duration seconds", default="30"))
                pilot.monitor_container_live(container_name, duration)
            elif choice == "stats":
                pilot.list_containers()
                container_name = Prompt.ask("Container name")
                pilot.get_container_stats_once(container_name)
            elif choice == "health-check":
                port = int(Prompt.ask("Port number"))
                endpoint = Prompt.ask("Health check endpoint", default="/health")
                max_retries = int(Prompt.ask("Maximum retries", default="10"))
                pilot.health_check_standalone(port, endpoint, max_retries=max_retries)
            elif choice == "run_image":
                image_name = Prompt.ask("Image name (e.g., nginx:latest)")
                container_name = Prompt.ask("Container name")

                ports = {}
                ports_input = Prompt.ask("Port mapping (format: container:host, e.g., 80:8080, or multiple: 80:8080,443:8443, empty for none)", default="").strip()
                if ports_input:
                    try:
                        for port_pair in ports_input.split(','):
                            port_pair = port_pair.strip()
                            if ':' in port_pair:
                                container_port, host_port = port_pair.split(':')
                                ports[container_port.strip()] = host_port.strip()
                    except ValueError:
                        pilot.console.print("[red]Invalid port format. Use container:host (e.g., 80:8080)[/red]")
                        continue

                environment = {}
                if Confirm.ask("Add environment variables?", default=False):
                    while True:
                        env_input = Prompt.ask("Environment variable (KEY=VALUE, empty to finish)", default="").strip()
                        if not env_input:
                            break
                        if '=' in env_input:
                            key, value = env_input.split('=', 1)
                            environment[key.strip()] = value.strip()
                        else:
                            pilot.console.print("[yellow]Invalid format. Use KEY=VALUE[/yellow]")

                volumes = {}
                if Confirm.ask("Add volume mappings?", default=False):
                    while True:
                        vol_input = Prompt.ask("Volume mapping (host:container or host:container:mode, empty to finish)", default="").strip()
                        if not vol_input:
                            break
                        if ':' in vol_input:
                            parts = vol_input.split(':')
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
                                pilot.console.print("[yellow]Invalid format. Use host:container or host:container:mode[/yellow]")
                        else:
                            pilot.console.print("[yellow]Invalid format. Use host:container[/yellow]")

                command = Prompt.ask("Command to run (empty for default)", default="").strip() or None
                restart_policy = Prompt.ask("Restart policy (no/on-failure/always/unless-stopped)", default="unless-stopped")

                network = None
                if Confirm.ask("Use custom network?", default=False):
                    network = Prompt.ask("Network name (or 'host' for host network)", default="") or None

                privileged = Confirm.ask("Run in privileged mode?", default=False)

                cpu_limit = None
                if Confirm.ask("Set CPU limit?", default=False):
                    cpu_limit = Prompt.ask("CPU limit (e.g., 1.5 for 1.5 CPUs)", default="") or None

                memory_limit = None
                if Confirm.ask("Set memory limit?", default=False):
                    memory_limit = Prompt.ask("Memory limit (e.g., 1g for 1GB, 512m for 512MB)", default="") or None

                success = pilot.run_new_container(
                    image_name=image_name,
                    name=container_name,
                    ports=ports if ports else None,
                    command=command,
                    environment=environment if environment else None,
                    volumes=volumes if volumes else None,
                    restart_policy=restart_policy,
                    network=network,
                    privileged=privileged,
                    cpu_limit=cpu_limit,
                    memory_limit=memory_limit,
                )
                if not success:
                    pilot.console.print("[red]Failed to run container[/red]")
            elif choice == "build":
                dockerfile_path = Prompt.ask("Dockerfile path", default=".")
                image_tag = Prompt.ask("Image tag (e.g., myapp:latest)")
                no_cache = Confirm.ask("Build without cache?", default=False)
                pull = Confirm.ask("Pull base image updates?", default=True)
                pull_if_missing = False
                generate_template = None
                source_info = pilot.inspect_build_source(dockerfile_path)

                if source_info["status"] == "multiple":
                    pilot.console.print("[yellow]Found multiple local Dockerfile candidates:[/yellow]")
                    indexed_choices = {}
                    for index, candidate in enumerate(source_info["candidates"], start=1):
                        indexed_choices[str(index)] = str(candidate)
                        pilot.console.print(f"  {index}. {candidate}")

                    choice = Prompt.ask(
                        "Choose candidate number, or type pull / template / cancel",
                        choices=[*indexed_choices.keys(), "pull", "template", "cancel"],
                        default="cancel",
                    )
                    if choice in indexed_choices:
                        dockerfile_path = indexed_choices[choice]
                    elif choice == "pull":
                        pull_if_missing = True
                    elif choice == "template":
                        generate_template = Prompt.ask(
                            "Template type",
                            choices=pilot.get_build_template_choices(),
                            default="python",
                        )
                    else:
                        pilot.console.print("[yellow]Build cancelled[/yellow]")
                        continue
                elif source_info["status"] in {"missing", "invalid"}:
                    action = Prompt.ask(
                        "No local Dockerfile is ready. Choose fallback",
                        choices=["pull", "template", "cancel"],
                        default="cancel",
                    )
                    if action == "pull":
                        pull_if_missing = True
                    elif action == "template":
                        generate_template = Prompt.ask(
                            "Template type",
                            choices=pilot.get_build_template_choices(),
                            default="python",
                        )
                    else:
                        pilot.console.print("[yellow]Build cancelled[/yellow]")
                        continue

                success = pilot.build_image_standalone(
                    dockerfile_path,
                    image_tag,
                    no_cache,
                    pull,
                    pull_if_missing,
                    generate_template,
                )
                if not success:
                    pilot.console.print("[red]Image build failed[/red]")
            elif choice == "json":
                pilot.list_containers()
                container_name = Prompt.ask("Container name or ID")
                pilot.view_container_json(container_name)
            elif choice == "logs":
                pilot.list_containers()
                containers_input = Prompt.ask("Container name(s) or ID(s) (comma-separated for multiple, empty for interactive select)", default="").strip()
                if containers_input:
                    pilot.view_container_logs(containers_input)
                else:
                    pilot.view_container_logs()
            elif choice == "remove-image":
                pilot.list_images()
                images_input = Prompt.ask("Image name(s) or ID(s) to remove (comma-separated for multiple, e.g., img1:tag,img2:tag)")
                images = pilot._parse_multi_target(images_input)
                if not images:
                    pilot.console.print("[red]No image names provided[/red]")
                    continue

                force = Confirm.ask("Force removal?", default=False)
                all_success = True
                for image in images:
                    pilot.console.print(f"\n[cyan]Processing image: {image}[/cyan]")
                    success = pilot.remove_image(image, force)
                    if not success:
                        all_success = False

                if not all_success:
                    pilot.console.print("[yellow]⚠️ Some operations failed[/yellow]")
                else:
                    pilot.console.print("[green]✅ All operations completed successfully[/green]")
            elif choice == "prune-images":
                pilot.console.print("[cyan]🧹 Cleaning up dangling images (images without tags)...[/cyan]")
                dry_run = Confirm.ask("Dry run (show what would be removed)?", default=True)
                result = pilot.prune_dangling_images(dry_run=dry_run)
                if not dry_run and result['images_deleted'] > 0:
                    pilot.console.print(f"[green]✅ Cleanup completed! Removed {result['images_deleted']} images[/green]")
                elif dry_run:
                    if result['images_deleted'] > 0:
                        proceed = Confirm.ask("Proceed with removal?", default=False)
                        if proceed:
                            result = pilot.prune_dangling_images(dry_run=False)
                            if result['images_deleted'] > 0:
                                pilot.console.print(f"[green]✅ Cleanup completed! Removed {result['images_deleted']} images[/green]")
                    else:
                        pilot.console.print("[yellow]ℹ️ No dangling images to remove[/yellow]")
            elif choice == "quick-deploy":
                dockerfile_path = Prompt.ask("Dockerfile directory path", default=".")
                image_tag = Prompt.ask("Image tag (e.g., myapp:v1.2)")
                container_name = Prompt.ask("Container name")
                use_yaml = Confirm.ask("Load settings from YAML config?", default=False)
                yaml_config = Prompt.ask("YAML config file path") if use_yaml else None

                port_mapping = None
                if not use_yaml:
                    port_input = Prompt.ask("Port mapping (format: container:host, e.g., 80:8080, empty to skip)", default="").strip()
                    if port_input:
                        try:
                            container_port, host_port = port_input.split(':')
                            port_mapping = {container_port: host_port}
                        except ValueError:
                            pilot.console.print("[red]Invalid port format[/red]")
                            continue

                environment = None
                if not use_yaml and Confirm.ask("Add environment variables?", default=False):
                    environment = {}
                    while True:
                        env_var = Prompt.ask("Environment variable (KEY=VALUE, empty to finish)", default="").strip()
                        if not env_var:
                            break
                        try:
                            key, value = env_var.split('=', 1)
                            environment[key] = value
                        except ValueError:
                            pilot.console.print("[red]Invalid format. Use KEY=VALUE[/red]")

                volumes = None
                if not use_yaml and Confirm.ask("Add volume mappings?", default=False):
                    volumes = {}
                    while True:
                        volume = Prompt.ask("Volume mapping (host:container, empty to finish)", default="").strip()
                        if not volume:
                            break
                        try:
                            host_path, container_path = volume.split(':')
                            volumes[host_path] = {'bind': container_path, 'mode': 'rw'}
                        except ValueError:
                            pilot.console.print("[red]Invalid format. Use host:container[/red]")

                cleanup_old_image = Confirm.ask("Remove old image after deployment?", default=True)
                success = pilot.quick_deploy(
                    dockerfile_path=dockerfile_path,
                    image_tag=image_tag,
                    container_name=container_name,
                    port_mapping=port_mapping,
                    environment=environment,
                    volumes=volumes,
                    yaml_config=yaml_config,
                    cleanup_old_image=cleanup_old_image,
                )
                if not success:
                    pilot.console.print("[red]Quick deploy failed[/red]")
            elif choice == "deploy-init":
                output = Prompt.ask("Output file", default="deployment.yml")
                pilot.create_deployment_config(output)
            elif choice == "deploy-config":
                config_file = Prompt.ask("Config file path", default="deployment.yml")
                deploy_type = Prompt.ask("Type (rolling/blue-green/canary)", default="rolling")
                success = pilot.deploy_from_config(config_file, deploy_type)
                if not success:
                    pilot.console.print("[red]Deployment failed[/red]")
            elif choice == "history":
                limit = int(Prompt.ask("Number of records", default="10"))
                pilot.show_deployment_history(limit=limit)
            elif choice == "validate":
                success = pilot.validate_system_requirements()
                if not success:
                    pilot.console.print("[red]System validation failed[/red]")
            elif choice == "backup-create":
                backup_path = Prompt.ask("Backup path (empty for auto)", default="").strip() or None
                pilot.backup_deployment_state(backup_path)
            elif choice == "backup-restore":
                backup_path = Prompt.ask("Backup path")
                success = pilot.restore_deployment_state(backup_path)
                if not success:
                    pilot.console.print("[red]Restore failed[/red]")
            elif choice == "export-config":
                output = Prompt.ask("Output archive name", default="docker-pilot-config.tar.gz")
                pilot.export_configuration(output)
            elif choice == "import-config":
                archive = Prompt.ask("Archive path")
                success = pilot.import_configuration(archive)
                if not success:
                    pilot.console.print("[red]Import failed[/red]")
            elif choice == "pipeline":
                pipeline_type = Prompt.ask("Pipeline type (github/gitlab/jenkins)", default="github")
                output = Prompt.ask("Output path (empty for default)", default="").strip() or None
                pilot.create_pipeline_config(pipeline_type, output)
            elif choice == "test":
                test_config = Prompt.ask("Test config file", default="integration-tests.yml")
                success = pilot.run_integration_tests(test_config)
                if not success:
                    pilot.console.print("[red]Integration tests failed[/red]")
            elif choice == "promote":
                source = Prompt.ask("Source environment")
                target = Prompt.ask("Target environment")
                config_path = Prompt.ask("Config file (empty for auto)", default="").strip() or None
                success = pilot.environment_promotion(source, target, config_path)
                if not success:
                    pilot.console.print("[red]Environment promotion failed[/red]")
            elif choice == "alerts":
                config_path = Prompt.ask("Alert config file", default="alerts.yml")
                success = pilot.setup_monitoring_alerts(config_path)
                if not success:
                    pilot.console.print("[red]Alert setup failed[/red]")
            elif choice == "policy":
                pilot.list_containers()
                name = Prompt.ask("Container name or ID")
                policy = Prompt.ask("Restart policy (no/on-failure/always/unless-stopped)", default="always")
                success = pilot.update_restart_policy(name, policy)
                if not success:
                    pilot.console.print("[red]Failed to update restart policy[/red]")
            elif choice == "docs":
                output = Prompt.ask("Output directory", default="docs")
                success = pilot.generate_documentation(output)
                if not success:
                    pilot.console.print("[red]Documentation generation failed[/red]")
            elif choice == "checklist":
                output = Prompt.ask("Output file", default="production-checklist.md")
                success = pilot.create_production_checklist(output)
                if not success:
                    pilot.console.print("[red]Checklist generation failed[/red]")
            else:
                pilot.console.print("[yellow]Unknown option, try again[/yellow]")
    except KeyboardInterrupt:
        pilot.console.print("\n[yellow]Interrupted, exiting interactive mode[/yellow]")
    except Exception as e:
        pilot.logger.error(f"Interactive menu error: {e}")
        pilot.console.print(f"[red]Error: {e}[/red]")
