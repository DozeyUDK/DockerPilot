"""Image build orchestration extracted from DeploymentServiceMixin."""

from typing import Any, Optional

import docker


def build_image_enhanced(host: Any, image_tag: str, build_config: dict) -> bool:
    """Enhanced image building with advanced features"""
    dockerfile_path = build_config.get('dockerfile_path', '.')
    no_cache = build_config.get('no_cache', False)
    pull = build_config.get('pull', True)
    build_args = build_config.get('build_args', {})

    try:
        source_info = host.inspect_build_source(dockerfile_path)
        if source_info["status"] != "ready":
            host.console.print(f"[bold red]❌ {source_info['message']}[/bold red]")
            if source_info["status"] == "multiple":
                host.console.print("[yellow]Select one of these paths explicitly:[/yellow]")
                for candidate in source_info["candidates"]:
                    host.console.print(f"  - {candidate}")
            return False

        context = source_info["context_path"]
        dockerfile = source_info["selected_path"]
        dockerfile_name = source_info["dockerfile_name"]

        if source_info["auto_detected"]:
            host.console.print(f"[yellow]Auto-detected Dockerfile: {dockerfile}[/yellow]")

        # Build with enhanced logging
        host.logger.info(f"Building image {image_tag} from {context} using {dockerfile_name}")

        build_kwargs = {
            'path': str(context),
            'tag': image_tag,
            'rm': True,
            'nocache': no_cache,
            'pull': pull,
            'buildargs': build_args
        }
        if dockerfile_name and dockerfile_name != "Dockerfile":
            build_kwargs['dockerfile'] = dockerfile_name

        # Show loading indicator during build
        with host._with_loading("Building image"):
            image, build_logs = host.client.images.build(**build_kwargs)

        # Process build logs
        for log in build_logs:
            if 'stream' in log:
                # Filter out verbose output for cleaner display
                stream = log['stream'].strip()
                if stream and not stream.startswith('Step'):
                    continue  # Only show steps in production

        return True

    except docker.errors.BuildError as e:
        host.logger.error(f"Build error: {e}")
        for log in e.build_log:
            if 'stream' in log:
                host.console.print(f"[red]{log['stream']}[/red]", end="")
        return False
    except Exception as e:
        host.logger.error(f"Unexpected build error: {e}")
        return False


def build_image_standalone(
    host: Any,
    dockerfile_path: str,
    tag: str,
    no_cache: bool = False,
    pull: bool = True,
    pull_if_missing: bool = False,
    generate_template: Optional[str] = None,
) -> bool:
    """Standalone image building function"""
    source_info = host.inspect_build_source(dockerfile_path)

    if source_info["status"] != "ready":
        host.console.print(f"[yellow]{source_info['message']}[/yellow]")
        if source_info["status"] == "multiple":
            host.console.print("[yellow]Available Dockerfile candidates:[/yellow]")
            for candidate in source_info["candidates"]:
                host.console.print(f"  - {candidate}")

        if generate_template:
            created = host.create_dockerfile_template(str(source_info["requested_path"]), generate_template)
            if created:
                source_info = host.inspect_build_source(dockerfile_path)

        if source_info["status"] != "ready" and pull_if_missing:
            try:
                host.console.print(f"[cyan]Pulling image {tag} from registry...[/cyan]")
                host.client.images.pull(tag)
                host.console.print(f"[green]✅ Pulled image {tag} successfully[/green]")
                host.logger.info(f"Pulled image {tag} because no buildable Dockerfile was available")
                return True
            except Exception as exc:
                host.console.print(f"[red]❌ Failed to pull image {tag}: {exc}[/red]")
                host.logger.error(f"Failed to pull image {tag}: {exc}")
                return False

        if source_info["status"] != "ready":
            host.console.print("[yellow]Hints:[/yellow]")
            host.console.print("  - point build at a directory or file that contains a Dockerfile")
            host.console.print(f"  - rerun with --pull-if-missing to pull {tag} from a registry instead")
            host.console.print(f"  - rerun with --generate-template {{{', '.join(host.get_build_template_choices())}}} to create a starter Dockerfile")
            return False

    build_config = {
        'dockerfile_path': str(source_info["selected_path"]),
        'context': str(source_info["context_path"]),
        'no_cache': no_cache,
        'pull': pull,
        'build_args': {}
    }

    host.console.print(f"[cyan]Building image {tag} from {source_info['context_path']}...[/cyan]")

    success = host._build_image_enhanced(tag, build_config)

    if success:
        host.console.print(f"[green]✅ Image {tag} built successfully[/green]")
    else:
        host.console.print(f"[red]❌ Failed to build image {tag}[/red]")

    return success
