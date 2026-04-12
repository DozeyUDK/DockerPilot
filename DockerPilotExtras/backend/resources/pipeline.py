"""Pipeline and deployment API resources."""

from __future__ import annotations

from pathlib import Path
import subprocess
import yaml


def create_pipeline_resources(
    *,
    Resource,
    app,
    request,
    datetime_cls,
    PipelineGenerator,
    parse_env_vars,
    generate_deployment_config_for_environment,
    save_deployment_config,
    append_deployment_history_data,
    get_deployment_history_data,
):
    """Return pipeline/deployment resource classes with injected dependencies."""

    class PipelineGenerate(Resource):
        """Generate CI/CD pipeline."""

        def post(self):
            try:
                data = request.get_json()

                pipeline_type = data.get("type", "gitlab")
                project_name = data.get("project_name", "myapp")
                docker_image = data.get("docker_image", "myapp:latest")
                dockerfile = data.get("dockerfile", "./Dockerfile")
                stages = data.get("stages", ["build", "test", "deploy"])
                env_vars_text = data.get("env_vars", "ENV=production")
                deploy_strategy = data.get("deploy_strategy", "rolling")
                image_tag_strategy = data.get("image_tag_strategy", "branch-sha")
                scan_severity = data.get("scan_severity", "HIGH,CRITICAL")
                scan_fail_on_findings = data.get("scan_fail_on_findings", True)
                smoke_test_url = data.get("smoke_test_url")
                enable_rollback_job = data.get("enable_rollback_job", True)
                try:
                    smoke_test_retries = int(data.get("smoke_test_retries", 10) or 10)
                except (TypeError, ValueError):
                    smoke_test_retries = 10
                test_commands_data = data.get("test_commands")
                if isinstance(test_commands_data, list):
                    test_commands = [str(cmd).strip() for cmd in test_commands_data if str(cmd).strip()]
                else:
                    test_commands_text = str(test_commands_data or "")
                    test_commands = [line.strip() for line in test_commands_text.splitlines() if line.strip()]

                env_vars = parse_env_vars(env_vars_text)
                generator = PipelineGenerator()

                if pipeline_type == "gitlab":
                    runner_tags = data.get("runner_tags", "docker,linux").split(",")
                    use_cache = data.get("use_cache", True)
                    registry_url = data.get("registry_url")
                    enable_environments = data.get("enable_environments", True)
                    deployment_config_path = data.get("deployment_config_path", "deployment.yml")

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
                        deployment_config_path=deployment_config_path,
                        test_commands=test_commands,
                        image_tag_strategy=image_tag_strategy,
                        scan_severity=scan_severity,
                        scan_fail_on_findings=scan_fail_on_findings,
                        smoke_test_url=smoke_test_url,
                        smoke_test_retries=smoke_test_retries,
                        enable_rollback_job=enable_rollback_job,
                    )
                    filename = ".gitlab-ci.yml"

                elif pipeline_type == "jenkins":
                    agent = data.get("agent", "any")
                    credentials_id = data.get("credentials_id", "docker-credentials")
                    enable_environments = data.get("enable_environments", True)
                    deployment_config_path = data.get("deployment_config_path", "deployment.yml")

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
                        deployment_config_path=deployment_config_path,
                        test_commands=test_commands,
                        scan_severity=scan_severity,
                        scan_fail_on_findings=scan_fail_on_findings,
                        smoke_test_url=smoke_test_url,
                        smoke_test_retries=smoke_test_retries,
                        enable_rollback_job=enable_rollback_job,
                    )
                    filename = "Jenkinsfile"
                else:
                    return {"error": "Invalid pipeline type"}, 400

                return {
                    "success": True,
                    "content": pipeline_content,
                    "filename": filename,
                    "type": pipeline_type,
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class PipelineSave(Resource):
        """Save pipeline to file."""

        def post(self):
            try:
                data = request.get_json()
                content = data.get("content")
                filename = data.get("filename", "pipeline.yml")

                if not content:
                    return {"error": "No content provided"}, 400

                filepath = app.config["PIPELINES_DIR"] / filename
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "success": True,
                    "message": f"Pipeline saved to {filepath}",
                    "path": str(filepath),
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class PipelineDeploymentConfig(Resource):
        """Generate deployment config for environments from pipeline config."""

        def post(self):
            try:
                data = request.get_json()
                base_config = data.get("base_config", {})
                image_tag = data.get("image_tag", "myapp:latest")
                container_name = data.get("container_name", "myapp")
                environments = data.get("environments", ["dev", "staging", "prod"])

                if not base_config:
                    config_path = app.config["CONFIG_DIR"] / "deployment.yml"
                    if config_path.exists():
                        with open(config_path, "r", encoding="utf-8") as f:
                            base_config = yaml.safe_load(f) or {}

                configs = {}
                for env in environments:
                    env_config = generate_deployment_config_for_environment(
                        base_config, env, image_tag, container_name
                    )
                    configs[env] = env_config
                    save_deployment_config(container_name, env_config, env=env, image_tag=image_tag)

                default_config = configs.get("dev", configs.get(list(configs.keys())[0] if configs else {}))
                save_deployment_config(container_name, default_config, image_tag=image_tag)

                return {
                    "success": True,
                    "configs": configs,
                    "message": f"Generated deployment configs for {len(environments)} environments",
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class PipelineIntegration(Resource):
        """Integrate pipeline with deployment - generate both pipeline and deployment configs."""

        def post(self):
            try:
                data = request.get_json()

                pipeline_type = data.get("type", "gitlab")
                project_name = data.get("project_name", "myapp")
                docker_image = data.get("docker_image", "myapp:latest")
                dockerfile = data.get("dockerfile", "./Dockerfile")
                stages = data.get("stages", ["build", "test", "deploy"])
                env_vars_text = data.get("env_vars", "ENV=production")
                deploy_strategy = data.get("deploy_strategy", "rolling")
                enable_environments = data.get("enable_environments", True)
                image_tag_strategy = data.get("image_tag_strategy", "branch-sha")
                scan_severity = data.get("scan_severity", "HIGH,CRITICAL")
                scan_fail_on_findings = data.get("scan_fail_on_findings", True)
                smoke_test_url = data.get("smoke_test_url")
                enable_rollback_job = data.get("enable_rollback_job", True)
                try:
                    smoke_test_retries = int(data.get("smoke_test_retries", 10) or 10)
                except (TypeError, ValueError):
                    smoke_test_retries = 10
                test_commands_data = data.get("test_commands")
                if isinstance(test_commands_data, list):
                    test_commands = [str(cmd).strip() for cmd in test_commands_data if str(cmd).strip()]
                else:
                    test_commands_text = str(test_commands_data or "")
                    test_commands = [line.strip() for line in test_commands_text.splitlines() if line.strip()]

                env_vars = parse_env_vars(env_vars_text)
                generator = PipelineGenerator()

                if pipeline_type == "gitlab":
                    runner_tags = data.get("runner_tags", "docker,linux").split(",")
                    use_cache = data.get("use_cache", True)
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
                        deployment_config_path="deployment.yml",
                        test_commands=test_commands,
                        image_tag_strategy=image_tag_strategy,
                        scan_severity=scan_severity,
                        scan_fail_on_findings=scan_fail_on_findings,
                        smoke_test_url=smoke_test_url,
                        smoke_test_retries=smoke_test_retries,
                        enable_rollback_job=enable_rollback_job,
                    )
                    filename = ".gitlab-ci.yml"
                elif pipeline_type == "jenkins":
                    agent = data.get("agent", "any")
                    credentials_id = data.get("credentials_id", "docker-credentials")
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
                        deployment_config_path="deployment.yml",
                        test_commands=test_commands,
                        scan_severity=scan_severity,
                        scan_fail_on_findings=scan_fail_on_findings,
                        smoke_test_url=smoke_test_url,
                        smoke_test_retries=smoke_test_retries,
                        enable_rollback_job=enable_rollback_job,
                    )
                    filename = "Jenkinsfile"
                else:
                    return {"error": "Invalid pipeline type"}, 400

                base_config = data.get(
                    "base_deployment_config",
                    {
                        "deployment": {
                            "image_tag": docker_image,
                            "container_name": project_name,
                            "port_mapping": {"8080": "8080"},
                            "environment": env_vars,
                            "restart_policy": "unless-stopped",
                            "health_check_endpoint": "/health",
                            "health_check_timeout": 30,
                            "health_check_retries": 10,
                            "network": "bridge",
                        }
                    },
                )

                environments = ["dev", "staging", "prod"] if enable_environments else ["prod"]
                deployment_configs = {}

                for env in environments:
                    env_config = generate_deployment_config_for_environment(
                        base_config, env, docker_image, project_name
                    )
                    deployment_configs[env] = env_config
                    save_deployment_config(project_name, env_config, env=env, image_tag=docker_image)

                default_deployment = deployment_configs.get("dev", deployment_configs.get("prod", {}))
                save_deployment_config(project_name, default_deployment, image_tag=docker_image)

                pipeline_path = app.config["PIPELINES_DIR"] / filename
                with open(pipeline_path, "w", encoding="utf-8") as f:
                    f.write(pipeline_content)

                return {
                    "success": True,
                    "pipeline": {
                        "content": pipeline_content,
                        "filename": filename,
                        "path": str(pipeline_path),
                    },
                    "deployment_configs": deployment_configs,
                    "environments": environments,
                    "message": "Pipeline and deployment configs generated successfully",
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class DeploymentConfig(Resource):
        """Get or update deployment configuration."""

        def get(self):
            default_config = {
                "deployment": {
                    "image_tag": "myapp:latest",
                    "container_name": "myapp",
                    "port_mapping": {"8080": "8080"},
                    "environment": {"ENV": "production"},
                    "volumes": {},
                    "restart_policy": "unless-stopped",
                    "health_check_endpoint": "/health",
                    "health_check_timeout": 30,
                    "health_check_retries": 10,
                    "cpu_limit": "0.5",
                    "memory_limit": "512m",
                    "network": "bridge",
                }
            }

            config_path = app.config["CONFIG_DIR"] / "deployment.yml"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        loaded_config = yaml.safe_load(f) or default_config
                        if "deployment" in loaded_config:
                            deployment = loaded_config["deployment"]
                            if "resources" in deployment:
                                resources = deployment.pop("resources")
                                if "cpu_limit" not in deployment:
                                    deployment["cpu_limit"] = resources.get("cpu_limit")
                                if "memory_limit" not in deployment:
                                    deployment["memory_limit"] = resources.get("memory_limit")
                            if "volumes" not in deployment:
                                deployment["volumes"] = {}
                            if "port_mapping" not in deployment:
                                deployment["port_mapping"] = {}
                            if "environment" not in deployment:
                                deployment["environment"] = {}
                        default_config = loaded_config
                except Exception:
                    pass

            return {"config": default_config}

        def post(self):
            try:
                data = request.get_json()
                config = data.get("config")

                if not config:
                    return {"error": "No config provided"}, 400

                if "deployment" in config:
                    deployment = config["deployment"]
                    if "resources" in deployment:
                        resources = deployment.pop("resources")
                        if "cpu_limit" not in deployment:
                            deployment["cpu_limit"] = resources.get("cpu_limit")
                        if "memory_limit" not in deployment:
                            deployment["memory_limit"] = resources.get("memory_limit")
                    if "volumes" not in deployment or not deployment["volumes"]:
                        deployment["volumes"] = {}
                    if "port_mapping" not in deployment or not deployment["port_mapping"]:
                        deployment["port_mapping"] = {}
                    if "environment" not in deployment or not deployment["environment"]:
                        deployment["environment"] = {}

                container_name = config.get("deployment", {}).get("container_name", "myapp")
                image_tag = config.get("deployment", {}).get("image_tag", "latest")
                config_path = save_deployment_config(container_name, config, image_tag=image_tag)

                return {
                    "success": True,
                    "message": "Configuration saved",
                    "path": str(config_path),
                    "deployment_id": config_path.parent.name,
                }
            except Exception as exc:
                return {"error": str(exc)}, 500

    class DeploymentExecute(Resource):
        """Execute deployment using DockerPilot."""

        def post(self):
            try:
                data = request.get_json()
                config = data.get("config")
                strategy = data.get("strategy", "rolling")

                if not config:
                    return {"error": "No deployment config provided"}, 400

                if "deployment" in config:
                    deployment = config["deployment"]
                    if "resources" in deployment:
                        resources = deployment.pop("resources")
                        if "cpu_limit" not in deployment:
                            deployment["cpu_limit"] = resources.get("cpu_limit")
                        if "memory_limit" not in deployment:
                            deployment["memory_limit"] = resources.get("memory_limit")
                    if "volumes" not in deployment or not deployment["volumes"]:
                        deployment["volumes"] = {}
                    if "port_mapping" not in deployment or not deployment["port_mapping"]:
                        deployment["port_mapping"] = {}
                    if "environment" not in deployment or not deployment["environment"]:
                        deployment["environment"] = {}
                    if "restart_policy" not in deployment:
                        deployment["restart_policy"] = "unless-stopped"
                    if "health_check_endpoint" not in deployment:
                        deployment["health_check_endpoint"] = "/health"
                    if "health_check_timeout" not in deployment:
                        deployment["health_check_timeout"] = 30
                    if "health_check_retries" not in deployment:
                        deployment["health_check_retries"] = 10
                    if "network" not in deployment:
                        deployment["network"] = "bridge"

                container_name = config.get("deployment", {}).get("container_name", "myapp")
                image_tag = config.get("deployment", {}).get("image_tag", "latest")
                config_path = save_deployment_config(container_name, config, image_tag=image_tag)

                try:
                    result = subprocess.run(
                        ["dockerpilot", "deploy", "config", str(config_path), "--type", strategy],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )

                    if result.returncode == 0:
                        append_deployment_history_data(
                            {
                                "timestamp": datetime_cls.now().isoformat(),
                                "strategy": strategy,
                                "status": "success",
                                "output": result.stdout,
                                "config_path": str(config_path),
                            },
                            max_entries=50,
                        )

                        return {
                            "success": True,
                            "message": "Deployment executed successfully",
                            "output": result.stdout,
                            "config_path": str(config_path),
                        }

                    return {
                        "success": False,
                        "error": result.stderr,
                        "output": result.stdout,
                        "config_path": str(config_path),
                    }, 500

                except subprocess.TimeoutExpired:
                    return {"error": "Deployment timeout"}, 500
                except FileNotFoundError:
                    return {"error": "DockerPilot not found. Please install DockerPilot."}, 500

            except Exception as exc:
                return {"error": str(exc)}, 500

    class DeploymentHistory(Resource):
        """Get deployment history."""

        def get(self):
            return {"history": get_deployment_history_data(limit=50)}

    return (
        PipelineGenerate,
        PipelineSave,
        PipelineDeploymentConfig,
        PipelineIntegration,
        DeploymentConfig,
        DeploymentExecute,
        DeploymentHistory,
    )
