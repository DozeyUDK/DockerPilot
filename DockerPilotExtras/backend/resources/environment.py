"""Environment and Docker utility API resources."""

from __future__ import annotations

from pathlib import Path
import subprocess
import yaml


def create_environment_resources(
    *,
    Resource,
    app,
    request,
    load_env_servers_config,
    save_env_servers_config,
    load_env_container_bindings,
    save_env_container_bindings,
    normalize_env_container_bindings,
    invalidate_environment_status_cache,
    get_selected_server_config,
    get_server_config_by_id,
    get_dockerpilot,
    execute_docker_command_via_ssh,
    resolve_server_id_for_env,
    detect_health_check_endpoint,
    infer_port_mapping_for_host_network,
    save_deployment_config,
    format_env_name,
):
    """Return environment-related resource classes with injected dependencies."""

    class ContainerList(Resource):
        """Get container status summary using DockerPilot API."""

        def get(self):
            try:
                server_config = get_selected_server_config()
                containers = []

                if server_config:
                    try:
                        app.logger.info(
                            f"Getting containers from remote server: {server_config.get('hostname')}"
                        )
                        containers_output = execute_docker_command_via_ssh(
                            server_config,
                            r"ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}'",
                        )
                        app.logger.debug(
                            "Containers output from remote server (first 500 chars): "
                            f"{containers_output[:500]}"
                        )

                        for line in containers_output.strip().split("\n"):
                            line = line.strip()
                            if line:
                                parts = line.split("\t")
                                if len(parts) >= 4:
                                    container_name = parts[0].lstrip("/")
                                    state = parts[2].lower()
                                    containers.append(
                                        {
                                            "name": container_name,
                                            "status": parts[3],
                                            "state": state,
                                            "image": parts[1],
                                        }
                                    )

                        app.logger.info(f"Found {len(containers)} containers on remote server")
                    except Exception as exc:
                        app.logger.error(
                            f"Failed to get containers from remote server: {exc}",
                            exc_info=True,
                        )
                        return {
                            "success": False,
                            "error": f"Failed to connect to remote server: {str(exc)}",
                            "containers": [],
                            "running": 0,
                            "stopped": 0,
                        }, 500
                else:
                    pilot = get_dockerpilot()
                    if not pilot.client or not pilot.container_manager:
                        return {
                            "error": "Docker client not initialized",
                            "containers": [],
                            "running": 0,
                            "stopped": 0,
                        }, 500

                    containers_data = pilot.list_containers(show_all=True, format_output="json")
                    if isinstance(containers_data, list):
                        for container in containers_data:
                            if isinstance(container, dict):
                                state = container.get("state", "").lower()
                                status = container.get("status", "")
                                name = container.get("name", "")
                                containers.append(
                                    {
                                        "name": name,
                                        "status": status,
                                        "state": state,
                                        "image": container.get("image", ""),
                                    }
                                )

                running = 0
                stopped = 0
                for container in containers:
                    state = container.get("state", "").lower()
                    if state == "running":
                        running += 1
                    else:
                        stopped += 1

                return {
                    "success": True,
                    "summary": {
                        "total": len(containers),
                        "running": running,
                        "stopped": stopped,
                    },
                    "containers": containers,
                }
            except Exception as exc:
                app.logger.error(f"Failed to get containers: {exc}")
                return {
                    "error": str(exc),
                    "containers": [],
                    "running": 0,
                    "stopped": 0,
                }, 500

    class DockerImages(Resource):
        """List available Docker images."""

        def get(self):
            try:
                result = subprocess.run(
                    ["docker", "images", "--format", "{{.Repository}}|{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedAt}}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    images = []
                    images_full = []

                    for line in result.stdout.strip().split("\n"):
                        if not line.strip():
                            continue

                        parts = line.split("|")
                        if len(parts) >= 2:
                            repo = parts[0] if parts[0] else "<none>"
                            tag = parts[1] if parts[1] else "<none>"
                            image_id = parts[2] if len(parts) > 2 else ""
                            size = parts[3] if len(parts) > 3 else ""
                            created = parts[4] if len(parts) > 4 else ""

                            image_name = f"{repo}:{tag}"
                            images.append(image_name)
                            images_full.append(
                                {
                                    "name": image_name,
                                    "repository": repo,
                                    "tag": tag,
                                    "id": image_id[:12] if image_id else "",
                                    "size": size,
                                    "created": created,
                                }
                            )

                    images = sorted(list(set(images)))
                    seen = set()
                    unique_full = []
                    for img in images_full:
                        if img["name"] not in seen:
                            seen.add(img["name"])
                            unique_full.append(img)
                    images_full = sorted(unique_full, key=lambda x: x["name"])

                    return {"success": True, "images": images, "images_full": images_full}

                return {
                    "success": False,
                    "error": result.stderr or "Failed to get image list",
                }, 500
            except FileNotFoundError:
                return {"error": "Docker not found"}, 500
            except Exception as exc:
                return {"error": str(exc)}, 500

    class DockerfilePaths(Resource):
        """Find Dockerfile paths in the system."""

        def get(self):
            try:
                dockerfiles = []
                dockerfiles_full = []
                current_dir = Path.cwd()
                max_depth = 3

                for depth in range(max_depth + 1):
                    pattern = "**/" * depth + "Dockerfile*"
                    for dockerfile_path in current_dir.glob(pattern):
                        if dockerfile_path.is_file():
                            rel_path = dockerfile_path.relative_to(current_dir)
                            rel_str = f"./{rel_path}"
                            full_str = str(dockerfile_path.resolve())
                            dockerfiles.append(rel_str)
                            dockerfiles_full.append(
                                {"relative": rel_str, "full": full_str, "name": dockerfile_path.name}
                            )

                try:
                    config_dir = app.config["CONFIG_DIR"]
                    for dockerfile_path in config_dir.rglob("Dockerfile*"):
                        if dockerfile_path.is_file():
                            full_str = str(dockerfile_path.resolve())
                            dockerfiles.append(full_str)
                            dockerfiles_full.append(
                                {"relative": full_str, "full": full_str, "name": dockerfile_path.name}
                            )
                except Exception:
                    pass

                dockerfiles = sorted(list(set(dockerfiles)))
                seen = set()
                unique_full = []
                for df in dockerfiles_full:
                    if df["full"] not in seen:
                        seen.add(df["full"])
                        unique_full.append(df)
                dockerfiles_full = sorted(unique_full, key=lambda x: x["full"])

                if not dockerfiles:
                    dockerfiles = ["./Dockerfile", "./docker/Dockerfile", "./build/Dockerfile"]

                return {
                    "success": True,
                    "dockerfiles": dockerfiles,
                    "dockerfiles_full": dockerfiles_full,
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class FileBrowser(Resource):
        """Browse files and directories."""

        def get(self):
            try:
                path = request.args.get("path", str(Path.home()))
                path_obj = Path(path)
                home = Path.home()
                try:
                    path_obj = path_obj.resolve()
                    if not str(path_obj).startswith(str(home)):
                        path_obj = home
                except Exception:
                    path_obj = home

                if not path_obj.exists():
                    path_obj = home

                items = []
                if path_obj.is_dir():
                    for item in sorted(path_obj.iterdir()):
                        try:
                            items.append(
                                {
                                    "name": item.name,
                                    "path": str(item),
                                    "is_dir": item.is_dir(),
                                    "is_file": item.is_file(),
                                    "size": item.stat().st_size if item.is_file() else None,
                                }
                            )
                        except (PermissionError, OSError):
                            continue

                return {
                    "success": True,
                    "current_path": str(path_obj),
                    "parent_path": str(path_obj.parent) if path_obj.parent != path_obj else None,
                    "items": items,
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class PrepareContainerConfig(Resource):
        """Prepare deployment configuration for a container from running container."""

        def post(self):
            try:
                data = request.get_json()
                container_name = data.get("container_name")
                target_env = data.get("target_env", "dev")
                source_env = data.get("source_env") or target_env

                if not container_name:
                    return {"error": "container_name is required"}, 400
                if target_env not in ["dev", "staging", "prod"]:
                    return {"error": "target_env must be dev, staging, or prod"}, 400
                if source_env not in ["dev", "staging", "prod"]:
                    return {"error": "source_env must be dev, staging, or prod"}, 400

                def extract_deployment_from_attrs(attrs: dict) -> dict:
                    image_tag = (attrs.get("Config", {}) or {}).get("Image", "") or ""
                    if not image_tag:
                        image_tag = attrs.get("Image", "") or ""

                    port_mapping = {}
                    ports = (attrs.get("NetworkSettings", {}) or {}).get("Ports", {}) or {}
                    for container_port, host_bindings in ports.items():
                        if host_bindings:
                            port_num = str(container_port).split("/")[0]
                            host_port = (
                                (host_bindings[0] or {}).get("HostPort", "")
                                if isinstance(host_bindings, list)
                                else ""
                            )
                            if host_port:
                                port_mapping[port_num] = host_port

                    environment = {}
                    env_list = (attrs.get("Config", {}) or {}).get("Env", []) or []
                    for env_var in env_list:
                        if isinstance(env_var, str) and "=" in env_var:
                            key, value = env_var.split("=", 1)
                            environment[key] = value

                    volumes = {}
                    mounts = attrs.get("Mounts", []) or []
                    for mount in mounts:
                        source = mount.get("Source", "")
                        destination = mount.get("Destination", "")
                        volume_name = mount.get("Name", "")
                        if destination:
                            if volume_name:
                                volumes[volume_name] = destination
                            elif source and not str(source).startswith("/var/lib/docker/volumes/"):
                                volumes[source] = destination

                    host_config = attrs.get("HostConfig", {}) or {}
                    restart_policy = "no"
                    if isinstance(host_config, dict):
                        restart_policy = (host_config.get("RestartPolicy") or {}).get("Name", "no")

                    network_mode = (
                        host_config.get("NetworkMode", "bridge")
                        if isinstance(host_config, dict)
                        else "bridge"
                    )
                    if network_mode == "default":
                        network_mode = "bridge"
                    if not port_mapping and network_mode == "host":
                        inferred = infer_port_mapping_for_host_network(attrs, image_tag)
                        if inferred:
                            port_mapping.update(inferred)
                            app.logger.info(
                                "Inferred host-network port mapping while preparing config for "
                                f"{container_name}: {port_mapping}"
                            )

                    cpu_limit = None
                    memory_limit = None
                    if isinstance(host_config, dict) and "NanoCpus" in host_config:
                        try:
                            nano_cpus = int(host_config.get("NanoCpus", 0) or 0)
                            if nano_cpus > 0:
                                cpu_limit = str(nano_cpus / 1000000000)
                        except Exception:
                            pass
                    if (
                        isinstance(host_config, dict)
                        and host_config.get("Memory", 0)
                        and host_config.get("Memory", 0) > 0
                    ):
                        try:
                            memory_mb = host_config["Memory"] / (1024 * 1024)
                            memory_limit = (
                                f"{int(memory_mb / 1024)}Gi" if memory_mb >= 1024 else f"{int(memory_mb)}Mi"
                            )
                        except Exception:
                            pass

                    deployment = {
                        "image_tag": image_tag,
                        "container_name": container_name,
                        "port_mapping": port_mapping,
                        "environment": environment,
                        "volumes": volumes,
                        "restart_policy": restart_policy,
                        "network": network_mode,
                        "health_check_endpoint": detect_health_check_endpoint(image_tag),
                        "health_check_timeout": 30,
                        "health_check_retries": 10,
                    }
                    if cpu_limit:
                        deployment["cpu_limit"] = cpu_limit
                    if memory_limit:
                        deployment["memory_limit"] = memory_limit
                    return deployment

                source_server_id = resolve_server_id_for_env(source_env)
                source_server_config = get_server_config_by_id(source_server_id)

                attrs = None
                if source_server_id == "local":
                    pilot = get_dockerpilot()
                    client = pilot.client
                    try:
                        container = client.containers.get(container_name)
                    except Exception as exc:
                        if "NotFound" in str(type(exc).__name__):
                            return {
                                "error": (
                                    f"Container {container_name} not found on source env "
                                    f"{source_env} (local)"
                                )
                            }, 404
                        raise
                    attrs = container.attrs
                    if not (attrs.get("Config", {}) or {}).get("Image"):
                        try:
                            image = container.image
                            tags = getattr(image, "tags", None) or []
                            attrs.setdefault("Config", {})["Image"] = (
                                tags[0] if tags else getattr(image, "id", "")
                            )
                        except Exception:
                            pass
                else:
                    import json as _json

                    try:
                        out = execute_docker_command_via_ssh(
                            source_server_config,
                            f"inspect {container_name}",
                            check_exit_status=True,
                        )
                        inspected = _json.loads(out)
                        attrs = inspected[0] if isinstance(inspected, list) and inspected else inspected
                    except Exception as exc:
                        return {
                            "error": (
                                f"Container {container_name} not found or inspect failed on source env "
                                f"{source_env} ({source_server_id}): {exc}"
                            )
                        }, 404

                deployment = extract_deployment_from_attrs(attrs or {})
                deployment_config = {"deployment": {**deployment}}

                if target_env == "dev":
                    if "cpu_limit" in deployment_config["deployment"]:
                        try:
                            cpu = float(deployment_config["deployment"]["cpu_limit"])
                            deployment_config["deployment"]["cpu_limit"] = str(max(0.5, cpu * 0.5))
                        except Exception:
                            pass
                    if "memory_limit" in deployment_config["deployment"]:
                        mem_str = deployment_config["deployment"]["memory_limit"]
                        if "Gi" in mem_str:
                            mem_gb = float(mem_str.replace("Gi", ""))
                            deployment_config["deployment"]["memory_limit"] = f"{max(0.5, mem_gb * 0.5)}Gi"
                        elif "Mi" in mem_str:
                            mem_mb = float(mem_str.replace("Mi", ""))
                            deployment_config["deployment"]["memory_limit"] = f"{max(512, mem_mb * 0.5)}Mi"

                image_tag = deployment.get("image_tag")
                saved_config_path = save_deployment_config(
                    container_name,
                    deployment_config,
                    env=target_env,
                    image_tag=image_tag,
                )

                return {
                    "success": True,
                    "message": f"Konfiguracja utworzona dla środowiska {format_env_name(target_env)}",
                    "container_name": container_name,
                    "image_tag": deployment.get("image_tag"),
                    "config_path": str(saved_config_path),
                    "config": deployment_config,
                }
            except Exception as exc:
                app.logger.error(f"Failed to prepare container config: {exc}")
                return {"error": str(exc)}, 500

    class ImportDeploymentConfig(Resource):
        """Import deployment configuration from existing file."""

        def post(self):
            try:
                data = request.get_json()
                config_file_path = data.get("config_file_path")
                target_env = data.get("target_env", "dev")
                container_name_override = data.get("container_name")

                if not config_file_path:
                    return {"error": "config_file_path is required"}, 400
                if target_env not in ["dev", "staging", "prod"]:
                    return {"error": "target_env must be dev, staging, or prod"}, 400

                config_path = Path(config_file_path)
                if not config_path.exists():
                    return {"error": f"Config file not found: {config_file_path}"}, 404
                if config_path.suffix.lower() not in [".yml", ".yaml"]:
                    return {"error": "File must be a YAML file (.yml or .yaml)"}, 400

                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        deployment_config = yaml.safe_load(f) or {}
                except Exception as exc:
                    return {"error": f"Failed to parse YAML file: {str(exc)}"}, 400

                if "deployment" not in deployment_config:
                    if "image_tag" in deployment_config or "container_name" in deployment_config:
                        deployment_config = {"deployment": deployment_config}
                    else:
                        return {"error": 'Invalid config structure: missing "deployment" section'}, 400

                deployment = deployment_config.get("deployment", {})
                container_name = container_name_override or deployment.get("container_name")
                image_tag = deployment.get("image_tag")

                if not container_name:
                    return {
                        "error": (
                            "container_name is required (provide container_name parameter "
                            "or ensure it exists in deployment config)"
                        )
                    }, 400
                if not image_tag:
                    return {"error": "image_tag is required in deployment config"}, 400

                if container_name_override and container_name_override != deployment.get("container_name"):
                    deployment_config["deployment"]["container_name"] = container_name_override
                    app.logger.info(
                        "Overriding container_name from "
                        f"'{deployment.get('container_name')}' to '{container_name_override}'"
                    )

                saved_config_path = save_deployment_config(
                    container_name,
                    deployment_config,
                    env=target_env,
                    image_tag=image_tag,
                )

                return {
                    "success": True,
                    "message": (
                        f"Configuration from {config_path.name} imported for container "
                        f"{container_name} in environment {format_env_name(target_env)}"
                    ),
                    "container_name": container_name,
                    "image_tag": image_tag,
                    "config_path": str(saved_config_path),
                    "source_file": str(config_path),
                }
            except Exception as exc:
                app.logger.error(f"Failed to import deployment config: {exc}")
                return {"error": str(exc)}, 500

    class EnvServersMap(Resource):
        """GET/PUT environment -> server_id mapping (dev/staging/prod each map to a server)."""

        def get(self):
            try:
                cfg = load_env_servers_config()
                env_servers = cfg.get("env_servers", {}) if isinstance(cfg, dict) else {}
                return {"success": True, "env_servers": env_servers}
            except Exception as exc:
                app.logger.error(f"Failed to load env servers map: {exc}")
                return {"success": False, "error": str(exc), "env_servers": {}}, 500

        def put(self):
            try:
                data = request.get_json() or {}
                env_servers = data.get("env_servers")
                if env_servers is not None and not isinstance(env_servers, dict):
                    return {"success": False, "error": "env_servers must be an object"}, 400
                if env_servers is None:
                    env_servers = {}
                cfg = load_env_servers_config()
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["env_servers"] = env_servers
                if not save_env_servers_config(cfg):
                    return {"success": False, "error": "Failed to save config"}, 500
                invalidate_environment_status_cache()
                return {"success": True, "env_servers": env_servers}
            except Exception as exc:
                app.logger.error(f"Failed to save env servers map: {exc}")
                return {"success": False, "error": str(exc)}, 500

    class EnvContainerBindings(Resource):
        """GET/PUT explicit environment -> container bindings."""

        def get(self):
            try:
                cfg = normalize_env_container_bindings(load_env_container_bindings())
                return {"success": True, "env_containers": cfg.get("env_containers", {})}
            except Exception as exc:
                app.logger.error(f"Failed to load env container bindings: {exc}")
                return {"success": False, "error": str(exc), "env_containers": {}}, 500

        def put(self):
            try:
                data = request.get_json() or {}
                env_containers = data.get("env_containers")
                if env_containers is None or not isinstance(env_containers, dict):
                    return {"success": False, "error": "env_containers must be an object"}, 400
                normalized = normalize_env_container_bindings({"env_containers": env_containers})
                if not save_env_container_bindings(normalized):
                    return {"success": False, "error": "Failed to save container bindings"}, 500
                invalidate_environment_status_cache()
                return {"success": True, "env_containers": normalized.get("env_containers", {})}
            except Exception as exc:
                app.logger.error(f"Failed to save env container bindings: {exc}")
                return {"success": False, "error": str(exc)}, 500

    class BlueGreenReplace(Resource):
        """Blue-green replace for running container."""

        def post(self):
            try:
                data = request.get_json() or {}
                container_name = data.get("container_name")
                image_tag = data.get("image_tag")
                if not container_name or not image_tag:
                    return {"success": False, "error": "container_name and image_tag are required"}, 400
                pilot = get_dockerpilot()
                client = pilot.client
                try:
                    container = client.containers.get(container_name)
                except Exception as exc:
                    if "NotFound" in str(type(exc).__name__):
                        return {"success": False, "error": f"Container {container_name} not found"}, 404
                    raise
                attrs = container.attrs
                port_mapping = {}
                if "NetworkSettings" in attrs:
                    ports = attrs["NetworkSettings"].get("Ports", {})
                    for container_port, host_bindings in ports.items():
                        if host_bindings:
                            port_num = container_port.split("/")[0]
                            host_port = host_bindings[0].get("HostPort", "")
                            if host_port:
                                port_mapping[port_num] = host_port
                environment = {}
                for env_var in attrs.get("Config", {}).get("Env", []):
                    if "=" in env_var:
                        k, v = env_var.split("=", 1)
                        environment[k] = v
                volumes = {}
                for mount in attrs.get("Mounts", []):
                    src, dest = mount.get("Source", ""), mount.get("Destination", "")
                    if dest:
                        volumes[src or mount.get("Name", "")] = dest
                host_config = attrs.get("HostConfig", {})
                restart_policy = (host_config.get("RestartPolicy") or {}).get("Name", "no")
                network_mode = host_config.get("NetworkMode", "bridge") or "bridge"
                if network_mode == "default":
                    network_mode = "bridge"
                if not port_mapping and network_mode == "host":
                    inferred = infer_port_mapping_for_host_network(attrs, image_tag)
                    if inferred:
                        port_mapping.update(inferred)
                        app.logger.info(
                            "Inferred host-network port mapping for blue-green replace of "
                            f"{container_name}: {port_mapping}"
                        )
                deployment_config = {
                    "deployment": {
                        "image_tag": image_tag,
                        "container_name": container_name,
                        "port_mapping": port_mapping,
                        "environment": environment,
                        "volumes": volumes,
                        "restart_policy": restart_policy,
                        "network": network_mode,
                        "health_check_endpoint": detect_health_check_endpoint(image_tag),
                        "health_check_timeout": 30,
                        "health_check_retries": 10,
                    }
                }
                config_path = save_deployment_config(container_name, deployment_config, image_tag=image_tag)
                result = subprocess.run(
                    ["dockerpilot", "deploy", "config", str(config_path), "--type", "blue-green"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode == 0:
                    return {
                        "success": True,
                        "message": f"Blue-green deploy zakończony: {container_name} na obrazie {image_tag}",
                        "output": result.stdout,
                    }
                return {
                    "success": False,
                    "error": result.stderr or "Deploy failed",
                    "output": result.stdout,
                }, 500
            except Exception as exc:
                app.logger.error(f"BlueGreenReplace failed: {exc}", exc_info=True)
                return {"success": False, "error": str(exc)}, 500

    return (
        ContainerList,
        DockerImages,
        DockerfilePaths,
        FileBrowser,
        PrepareContainerConfig,
        ImportDeploymentConfig,
        EnvServersMap,
        EnvContainerBindings,
        BlueGreenReplace,
    )
