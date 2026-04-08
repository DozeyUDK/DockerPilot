"""
Utility module for pipeline generation
"""

import yaml
from typing import Dict, List, Optional


class PipelineGenerator:
    """Generator for CI/CD pipelines."""

    @staticmethod
    def _split_image_name(docker_image: str) -> tuple[str, str]:
        """Split docker image into repository and tag.

        Supports registries with ports, e.g. registry:5000/app:tag.
        """
        image = (docker_image or "myapp:latest").strip()
        last_slash = image.rfind("/")
        last_colon = image.rfind(":")
        if last_colon > last_slash:
            return image[:last_colon], image[last_colon + 1 :]
        return image, "latest"

    @staticmethod
    def _normalize_stages(stages: List[str]) -> List[str]:
        allowed = ["build", "test", "scan", "deploy", "smoke"]
        normalized: List[str] = []
        for stage in stages or []:
            stage_name = str(stage).strip().lower()
            if stage_name in allowed and stage_name not in normalized:
                normalized.append(stage_name)
        if not normalized:
            normalized = ["build", "test", "deploy"]
        return normalized

    @staticmethod
    def _normalize_test_commands(test_commands: Optional[List[str]]) -> List[str]:
        if not test_commands:
            return ["npm test", "npm run lint"]

        normalized = [cmd.strip() for cmd in test_commands if str(cmd).strip()]
        return normalized or ["npm test", "npm run lint"]

    @staticmethod
    def _gitlab_tag_assignment(image_tag_strategy: str, fallback_tag: str) -> str:
        strategy = (image_tag_strategy or "branch-sha").strip().lower()
        if strategy == "sha":
            return 'IMAGE_TAG="${CI_COMMIT_SHORT_SHA}"'
        if strategy == "latest":
            return 'IMAGE_TAG="latest"'
        if strategy == "tag-or-sha":
            return 'IMAGE_TAG="${CI_COMMIT_TAG:-${CI_COMMIT_SHORT_SHA}}"'
        if strategy == "static":
            return f'IMAGE_TAG="{fallback_tag}"'
        return 'IMAGE_TAG="${CI_COMMIT_REF_SLUG}-${CI_COMMIT_SHORT_SHA}"'

    @staticmethod
    def _gitlab_smoke_script(smoke_test_url: str, retries: int) -> List[str]:
        safe_retries = max(1, int(retries))
        return [
            f'SMOKE_URL="{smoke_test_url}"',
            f"SMOKE_RETRIES={safe_retries}",
            'for i in $(seq 1 "$SMOKE_RETRIES"); do curl -fsS "$SMOKE_URL" && exit 0; echo "Smoke attempt $i failed"; sleep 5; done; exit 1',
        ]

    @staticmethod
    def generate_gitlab_pipeline(
        project_name: str,
        docker_image: str,
        dockerfile: str,
        runner_tags: List[str],
        stages: List[str],
        env_vars: Dict[str, str],
        deploy_strategy: str = "rolling",
        use_cache: bool = True,
        registry_url: Optional[str] = None,
        enable_environments: bool = True,
        deployment_config_path: str = "deployment.yml",
        test_commands: Optional[List[str]] = None,
        image_tag_strategy: str = "branch-sha",
        scan_severity: str = "HIGH,CRITICAL",
        scan_fail_on_findings: bool = True,
        smoke_test_url: Optional[str] = None,
        smoke_test_retries: int = 10,
        enable_rollback_job: bool = True,
    ) -> str:
        """Generate GitLab CI pipeline YAML."""
        normalized_stages = PipelineGenerator._normalize_stages(stages)
        normalized_test_commands = PipelineGenerator._normalize_test_commands(test_commands)
        runtime_repo, runtime_tag = PipelineGenerator._split_image_name(docker_image)
        tag_assignment = PipelineGenerator._gitlab_tag_assignment(image_tag_strategy, runtime_tag)

        pipeline = {
            "image": "docker:latest",
            "services": ["docker:dind"],
            "variables": {
                "DOCKER_DRIVER": "overlay2",
                "DOCKER_TLS_CERTDIR": "/certs",
            },
            "stages": normalized_stages,
            "before_script": [
                "docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY"
            ],
        }

        if use_cache:
            pipeline["cache"] = {"paths": ["~/.docker/"]}

        if "build" in normalized_stages:
            build_script = [
                tag_assignment,
                'echo "Using image tag: ${IMAGE_TAG}"',
                f'docker build -t "$CI_REGISTRY_IMAGE:${{IMAGE_TAG}}" -f {dockerfile} .',
                'docker push "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
            ]

            pipeline["build"] = {
                "stage": "build",
                "tags": runner_tags,
                "script": build_script,
                "only": ["main", "master", "develop", "staging"],
            }
            if env_vars:
                pipeline["build"]["variables"] = env_vars

        if "test" in normalized_stages:
            test_script = [
                tag_assignment,
                'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
            ]
            for command in normalized_test_commands:
                escaped = command.replace('"', '\\"')
                test_script.append(
                    f'docker run --rm "$CI_REGISTRY_IMAGE:${{IMAGE_TAG}}" sh -lc "{escaped}"'
                )

            pipeline["test"] = {
                "stage": "test",
                "tags": runner_tags,
                "script": test_script,
                "only": ["main", "master", "develop", "staging"],
            }
            if "build" in normalized_stages:
                pipeline["test"]["needs"] = ["build"]

        if "scan" in normalized_stages:
            scan_exit_code = 1 if scan_fail_on_findings else 0
            pipeline["scan"] = {
                "stage": "scan",
                "tags": runner_tags,
                "script": [
                    tag_assignment,
                    'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
                    (
                        "docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "
                        f"aquasec/trivy:latest image --severity {scan_severity} --exit-code {scan_exit_code} "
                        '"$CI_REGISTRY_IMAGE:${IMAGE_TAG}"'
                    ),
                ],
                "only": ["main", "master", "develop", "staging"],
            }
            if "build" in normalized_stages:
                pipeline["scan"]["needs"] = ["build"]

        if "deploy" in normalized_stages:
            set_runtime_image = f'RUNTIME_IMAGE="{runtime_repo}:{runtime_tag}"'

            if enable_environments:
                deploy_dev = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        tag_assignment,
                        set_runtime_image,
                        'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
                        'docker tag "$CI_REGISTRY_IMAGE:${IMAGE_TAG}" "${RUNTIME_IMAGE}"',
                        f"dockerpilot deploy config {deployment_config_path} --type rolling",
                    ],
                    "environment": {
                        "name": "development",
                        "url": f"https://dev.{project_name}.example.com",
                    },
                    "only": ["develop"],
                }
                dev_needs = [
                    stage_name
                    for stage_name in ["build", "test", "scan"]
                    if stage_name in normalized_stages
                ]
                if dev_needs:
                    deploy_dev["needs"] = dev_needs
                pipeline["deploy:dev"] = deploy_dev

                pipeline["deploy:staging"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        tag_assignment,
                        set_runtime_image,
                        'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
                        'docker tag "$CI_REGISTRY_IMAGE:${IMAGE_TAG}" "${RUNTIME_IMAGE}"',
                        f"dockerpilot promote dev staging --config {deployment_config_path}",
                    ],
                    "environment": {
                        "name": "staging",
                        "url": f"https://staging.{project_name}.example.com",
                    },
                    "only": ["staging"],
                    "when": "manual",
                    "needs": ["deploy:dev"],
                }

                pipeline["deploy:prod"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": [
                        tag_assignment,
                        set_runtime_image,
                        'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
                        'docker tag "$CI_REGISTRY_IMAGE:${IMAGE_TAG}" "${RUNTIME_IMAGE}"',
                        f"dockerpilot promote staging prod --config {deployment_config_path}",
                    ],
                    "environment": {
                        "name": "production",
                        "url": f"https://{project_name}.example.com",
                    },
                    "only": ["main", "master"],
                    "when": "manual",
                    "needs": ["deploy:staging"],
                }

                if enable_rollback_job:
                    pipeline["rollback:prod"] = {
                        "stage": "deploy",
                        "tags": runner_tags,
                        "script": [
                            set_runtime_image,
                            'test -n "$ROLLBACK_TAG" || (echo "Set ROLLBACK_TAG CI variable" && exit 1)',
                            'docker pull "$CI_REGISTRY_IMAGE:${ROLLBACK_TAG}"',
                            'docker tag "$CI_REGISTRY_IMAGE:${ROLLBACK_TAG}" "${RUNTIME_IMAGE}"',
                            f"dockerpilot deploy config {deployment_config_path} --type rolling",
                        ],
                        "environment": {"name": "production"},
                        "only": ["main", "master"],
                        "when": "manual",
                        "allow_failure": False,
                    }

                if "smoke" in normalized_stages and smoke_test_url:
                    pipeline["smoke:dev"] = {
                        "stage": "smoke",
                        "tags": runner_tags,
                        "script": PipelineGenerator._gitlab_smoke_script(
                            smoke_test_url.replace("{env}", "dev"), smoke_test_retries
                        ),
                        "only": ["develop"],
                        "needs": ["deploy:dev"],
                    }
                    pipeline["smoke:staging"] = {
                        "stage": "smoke",
                        "tags": runner_tags,
                        "script": PipelineGenerator._gitlab_smoke_script(
                            smoke_test_url.replace("{env}", "staging"), smoke_test_retries
                        ),
                        "only": ["staging"],
                        "needs": ["deploy:staging"],
                    }
                    pipeline["smoke:prod"] = {
                        "stage": "smoke",
                        "tags": runner_tags,
                        "script": PipelineGenerator._gitlab_smoke_script(
                            smoke_test_url.replace("{env}", "prod"), smoke_test_retries
                        ),
                        "only": ["main", "master"],
                        "needs": ["deploy:prod"],
                    }
            else:
                deploy_script = [
                    tag_assignment,
                    set_runtime_image,
                    'docker pull "$CI_REGISTRY_IMAGE:${IMAGE_TAG}"',
                    'docker tag "$CI_REGISTRY_IMAGE:${IMAGE_TAG}" "${RUNTIME_IMAGE}"',
                    f"dockerpilot deploy config {deployment_config_path} --type {deploy_strategy}",
                ]

                pipeline["deploy"] = {
                    "stage": "deploy",
                    "tags": runner_tags,
                    "script": deploy_script,
                    "environment": {
                        "name": "production",
                        "url": f"https://{project_name}.example.com",
                    },
                    "only": ["main", "master"],
                }

                deploy_needs = [
                    stage_name
                    for stage_name in ["build", "test", "scan"]
                    if stage_name in normalized_stages
                ]
                if deploy_needs:
                    pipeline["deploy"]["needs"] = deploy_needs

                if "smoke" in normalized_stages and smoke_test_url:
                    pipeline["smoke"] = {
                        "stage": "smoke",
                        "tags": runner_tags,
                        "script": PipelineGenerator._gitlab_smoke_script(smoke_test_url, smoke_test_retries),
                        "only": ["main", "master"],
                        "needs": ["deploy"],
                    }

        return yaml.dump(pipeline, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @staticmethod
    def generate_jenkins_pipeline(
        project_name: str,
        docker_image: str,
        dockerfile: str,
        agent: str,
        credentials_id: str,
        stages: List[str],
        env_vars: Dict[str, str],
        deploy_strategy: str = "rolling",
        enable_environments: bool = True,
        deployment_config_path: str = "deployment.yml",
        test_commands: Optional[List[str]] = None,
        scan_severity: str = "HIGH,CRITICAL",
        scan_fail_on_findings: bool = True,
        smoke_test_url: Optional[str] = None,
        smoke_test_retries: int = 10,
        enable_rollback_job: bool = True,
    ) -> str:
        """Generate Jenkins Pipeline (Jenkinsfile)."""
        normalized_stages = PipelineGenerator._normalize_stages(stages)
        normalized_test_commands = PipelineGenerator._normalize_test_commands(test_commands)
        scan_exit_code = 1 if scan_fail_on_findings else 0
        safe_retries = max(1, int(smoke_test_retries))

        pipeline = f"""pipeline {{
    agent {{ label '{agent}' }}

    environment {{
        DOCKER_IMAGE = '{docker_image}'
        DOCKERFILE = '{dockerfile}'
"""

        for key, value in env_vars.items():
            pipeline += f"        {key} = '{value}'\n"

        if smoke_test_url:
            pipeline += f"        SMOKE_URL = '{smoke_test_url}'\n"

        pipeline += """    }

    stages {
"""

        if "build" in normalized_stages:
            pipeline += f"""        stage('Build') {{
            steps {{
                script {{
                    def image = docker.build("${{DOCKER_IMAGE}}", "-f ${{DOCKERFILE}} .")
                    docker.withRegistry('', '{credentials_id}') {{
                        image.push()
                    }}
                }}
            }}
        }}
"""

        if "test" in normalized_stages:
            pipeline += """        stage('Test') {
            steps {
"""
            for command in normalized_test_commands:
                escaped = command.replace("'", "'\"'\"'")
                pipeline += f"                sh 'docker run --rm ${{DOCKER_IMAGE}} sh -lc \'{escaped}\''\n"
            pipeline += """            }
        }
"""

        if "scan" in normalized_stages:
            pipeline += f"""        stage('Scan') {{
            steps {{
                sh 'docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image --severity {scan_severity} --exit-code {scan_exit_code} ${{DOCKER_IMAGE}}'
            }}
        }}
"""

        if "deploy" in normalized_stages:
            if enable_environments:
                pipeline += f"""        stage('Deploy to DEV') {{
            when {{
                branch 'develop'
            }}
            steps {{
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot deploy config {deployment_config_path} --type rolling'
            }}
        }}

        stage('Deploy to STAGING') {{
            when {{
                branch 'staging'
            }}
            steps {{
                input message: 'Deploy to STAGING?', ok: 'Deploy'
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot promote dev staging --config {deployment_config_path}'
            }}
        }}

        stage('Deploy to PROD') {{
            when {{
                branch 'main'
            }}
            steps {{
                input message: 'Deploy to PRODUCTION?', ok: 'Deploy'
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot promote staging prod --config {deployment_config_path}'
            }}
        }}
"""
                if enable_rollback_job:
                    pipeline += f"""        stage('Rollback PROD') {{
            when {{
                branch 'main'
            }}
            steps {{
                input message: 'Rollback production?', ok: 'Rollback'
                sh 'test -n "${{ROLLBACK_IMAGE}}" || (echo "Set ROLLBACK_IMAGE env var" && exit 1)'
                sh 'docker pull ${{ROLLBACK_IMAGE}}'
                sh 'dockerpilot deploy config {deployment_config_path} --type rolling'
            }}
        }}
"""
            else:
                pipeline += f"""        stage('Deploy') {{
            steps {{
                sh 'docker pull ${{DOCKER_IMAGE}}'
                sh 'dockerpilot deploy config {deployment_config_path} --type {deploy_strategy}'
            }}
        }}
"""

        if "smoke" in normalized_stages and smoke_test_url:
            pipeline += f"""        stage('Smoke test') {{
            steps {{
                sh 'for i in $(seq 1 {safe_retries}); do curl -fsS "${{SMOKE_URL}}" && exit 0; echo "Smoke attempt $i failed"; sleep 5; done; exit 1'
            }}
        }}
"""

        pipeline += """    }

    post {
        success {
            echo 'Pipeline succeeded!'
            archiveArtifacts artifacts: '**/*.log', allowEmptyArchive: true
        }
        failure {
            echo 'Pipeline failed!'
        }
        always {
            cleanWs()
        }
    }
}
"""

        return pipeline


def parse_env_vars(env_text: str) -> Dict[str, str]:
    """Parse environment variables from text."""
    env_vars = {}
    for line in env_text.strip().split("\n"):
        line = line.strip()
        if line and "=" in line:
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip()
    return env_vars


def generate_deployment_config_for_environment(
    base_config: Dict,
    environment: str,
    image_tag: str,
    container_name: str,
) -> Dict:
    """Generate deployment configuration for specific environment."""

    env_configs = {
        "dev": {
            "replicas": 1,
            "cpu_limit": "0.5",
            "memory_limit": "512m",
            "image_tag_suffix": "-dev",
            "port_mapping": {"8080": "8080"},
            "environment": {"ENV": "development", "LOG_LEVEL": "debug"},
        },
        "staging": {
            "replicas": 2,
            "cpu_limit": "1.0",
            "memory_limit": "1g",
            "image_tag_suffix": "-staging",
            "port_mapping": {"8080": "8081"},
            "environment": {"ENV": "staging", "LOG_LEVEL": "info"},
        },
        "prod": {
            "replicas": 3,
            "cpu_limit": "2.0",
            "memory_limit": "2g",
            "image_tag_suffix": "",
            "port_mapping": {"8080": "8080"},
            "environment": {"ENV": "production", "LOG_LEVEL": "warn"},
        },
    }

    if environment not in env_configs:
        environment = "dev"

    env_config = env_configs[environment]

    deployment = base_config.get("deployment", {})
    deployment.update(
        {
            "image_tag": f"{image_tag}{env_config['image_tag_suffix']}",
            "container_name": f"{container_name}-{environment}",
            "cpu_limit": env_config["cpu_limit"],
            "memory_limit": env_config["memory_limit"],
            "port_mapping": env_config["port_mapping"],
            "environment": {
                **deployment.get("environment", {}),
                **env_config["environment"],
            },
            "restart_policy": deployment.get("restart_policy", "unless-stopped"),
            "health_check_endpoint": deployment.get("health_check_endpoint", "/health"),
            "health_check_timeout": deployment.get("health_check_timeout", 30),
            "health_check_retries": deployment.get("health_check_retries", 10),
            "network": deployment.get("network", "bridge"),
        }
    )

    return {"deployment": deployment, "build": base_config.get("build", {})}
