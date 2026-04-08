"""Tests for DockerPilotExtras pipeline generator enhancements."""

import importlib.util
from pathlib import Path

import yaml


def _load_pipeline_generator_module():
    module_path = Path(__file__).resolve().parents[1] / "DockerPilotExtras" / "utils" / "pipeline_generator.py"
    spec = importlib.util.spec_from_file_location("dockerpilotextras_pipeline_generator", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_gitlab_pipeline_includes_scan_smoke_and_rollback():
    module = _load_pipeline_generator_module()

    content = module.PipelineGenerator.generate_gitlab_pipeline(
        project_name="demo",
        docker_image="myrepo/myapp:latest",
        dockerfile="./Dockerfile",
        runner_tags=["docker"],
        stages=["build", "test", "scan", "deploy", "smoke"],
        env_vars={"ENV": "production"},
        enable_environments=True,
        test_commands=["pytest -q", "flake8 ."],
        image_tag_strategy="sha",
        scan_severity="HIGH,CRITICAL",
        scan_fail_on_findings=True,
        smoke_test_url="https://{env}.example.com/health",
        smoke_test_retries=5,
        enable_rollback_job=True,
    )

    parsed = yaml.safe_load(content)

    assert parsed["stages"] == ["build", "test", "scan", "deploy", "smoke"]
    assert "scan" in parsed
    assert "rollback:prod" in parsed
    assert "smoke:dev" in parsed
    assert any("trivy" in line for line in parsed["scan"]["script"])
    assert any("pytest -q" in line for line in parsed["test"]["script"])


def test_gitlab_pipeline_single_deploy_mode_has_single_deploy_job():
    module = _load_pipeline_generator_module()

    content = module.PipelineGenerator.generate_gitlab_pipeline(
        project_name="demo",
        docker_image="myrepo/myapp:latest",
        dockerfile="./Dockerfile",
        runner_tags=["docker"],
        stages=["build", "deploy"],
        env_vars={},
        enable_environments=False,
        deploy_strategy="blue-green",
        enable_rollback_job=False,
    )

    parsed = yaml.safe_load(content)

    assert "deploy" in parsed
    assert "deploy:dev" not in parsed
    assert "rollback:prod" not in parsed
    assert any("--type blue-green" in line for line in parsed["deploy"]["script"])


def test_jenkins_pipeline_includes_scan_and_smoke_steps():
    module = _load_pipeline_generator_module()

    content = module.PipelineGenerator.generate_jenkins_pipeline(
        project_name="demo",
        docker_image="myrepo/myapp:latest",
        dockerfile="./Dockerfile",
        agent="any",
        credentials_id="docker-credentials",
        stages=["build", "test", "scan", "deploy", "smoke"],
        env_vars={"ENV": "production"},
        test_commands=["pytest -q"],
        scan_severity="CRITICAL",
        scan_fail_on_findings=False,
        smoke_test_url="https://demo.example.com/health",
        smoke_test_retries=3,
    )

    assert "stage('Scan')" in content
    assert "trivy:latest image --severity CRITICAL --exit-code 0" in content
    assert "stage('Smoke test')" in content
    assert "pytest -q" in content
