"""CI/CD pipeline generation and Git integration helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def integrate_with_git(console: Any, logger: Any, repo_path: str = ".") -> bool:
    """Display basic Git branch/commit information for a repository."""
    try:
        import git

        repo = git.Repo(repo_path)
        current_branch = repo.active_branch.name
        commit_hash = repo.head.commit.hexsha[:8]
        commit_message = repo.head.commit.message.strip()

        console.print(f"[cyan]Git Integration:[/cyan] {current_branch}@{commit_hash}")
        console.print(f"[cyan]Latest commit:[/cyan] {commit_message}")
        return True
    except ImportError:
        console.print("[yellow]GitPython not installed. Run: pip install GitPython[/yellow]")
        return False
    except Exception as exc:
        logger.error(f"Git integration failed: {exc}")
        return False


def create_pipeline_config(
    console: Any,
    logger: Any,
    pipeline_type: str = "github",
    output_path: str | None = None,
    *,
    templates_dir: Path | None = None,
) -> bool:
    """Generate one supported CI/CD pipeline configuration."""
    normalized = pipeline_type.lower()
    if normalized == "github":
        return _create_github_actions_config(console, logger, output_path, templates_dir=templates_dir)
    if normalized == "gitlab":
        return _create_gitlab_ci_config(console, logger, output_path, templates_dir=templates_dir)
    if normalized == "jenkins":
        return _create_jenkins_config(console, logger, output_path, templates_dir=templates_dir)

    console.print(f"[red]Unsupported pipeline type: {pipeline_type}[/red]")
    return False


def _template_root(templates_dir: Path | None) -> Path:
    return templates_dir or (Path(__file__).resolve().parents[1] / "configs")


def _create_github_actions_config(
    console: Any,
    logger: Any,
    output_path: str | None = None,
    *,
    templates_dir: Path | None = None,
) -> bool:
    if not output_path:
        output_path = ".github/workflows"
    os.makedirs(output_path, exist_ok=True)
    template_path = _template_root(templates_dir) / "github-actions.yml.template"

    try:
        if not template_path.exists():
            logger.error(f"Template file not found: {template_path}")
            console.print(f"[red]Template file not found: {template_path}[/red]")
            return False
        with open(template_path, "r") as source:
            workflow_content = source.read()
        config_file = Path(output_path) / "docker-pilot.yml"
        with open(config_file, "w") as target:
            target.write(workflow_content)
        console.print(f"[green]GitHub Actions workflow created: {config_file}[/green]")
        return True
    except Exception as exc:
        logger.error(f"Failed to create GitHub Actions config: {exc}")
        return False


def _create_gitlab_ci_config(
    console: Any,
    logger: Any,
    output_path: str | None = None,
    *,
    templates_dir: Path | None = None,
) -> bool:
    template_path = _template_root(templates_dir) / "gitlab-ci.yml.template"
    try:
        if not template_path.exists():
            logger.error(f"Template file not found: {template_path}")
            console.print(f"[red]Template file not found: {template_path}[/red]")
            return False
        with open(template_path, "r") as source:
            config_content = source.read()
        config_file = ".gitlab-ci.yml" if not output_path else Path(output_path) / ".gitlab-ci.yml"
        with open(config_file, "w") as target:
            target.write(config_content)
        console.print(f"[green]GitLab CI configuration created: {config_file}[/green]")
        return True
    except Exception as exc:
        logger.error(f"Failed to create GitLab CI config: {exc}")
        return False


def _create_jenkins_config(
    console: Any,
    logger: Any,
    output_path: str | None = None,
    *,
    templates_dir: Path | None = None,
) -> bool:
    template_path = _template_root(templates_dir) / "jenkinsfile.template"
    try:
        if not template_path.exists():
            logger.error(f"Template file not found: {template_path}")
            console.print(f"[red]Template file not found: {template_path}[/red]")
            return False
        with open(template_path, "r") as source:
            pipeline_content = source.read()
        config_file = "Jenkinsfile" if not output_path else Path(output_path) / "Jenkinsfile"
        with open(config_file, "w") as target:
            target.write(pipeline_content)
        console.print(f"[green]Jenkins pipeline created: {config_file}[/green]")
        return True
    except Exception as exc:
        logger.error(f"Failed to create Jenkins config: {exc}")
        return False
