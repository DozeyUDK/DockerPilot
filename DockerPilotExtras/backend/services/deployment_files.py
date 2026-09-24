"""Filesystem-backed deployment config helpers for DockerPilot Extras."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import yaml


def format_env_name(env: str) -> str:
    labels = {"dev": "DEV", "staging": "Pre-Prod", "prod": "PROD"}
    return labels.get(env.lower(), env.upper())


def generate_deployment_id(container_name: str, image_tag: str | None = None) -> str:
    timestamp = datetime.now().isoformat()
    hash_input = f"{container_name}_{image_tag or 'latest'}_{timestamp}"
    hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", container_name.lower())
    unique_id = f"{hash_value[:4]}!{hash_value[4:8]}{hash_value[8:12]}"
    return f"{safe_name}_{unique_id}"


def find_active_deployment_dir(deployments_dir: Path, container_name: str) -> Path | None:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", container_name.lower())
    if not deployments_dir.exists():
        return None

    matching_dirs = []
    for deployment_dir in deployments_dir.iterdir():
        if not deployment_dir.is_dir() or not deployment_dir.name.startswith(safe_name + "_"):
            continue
        metadata_path = deployment_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if metadata.get("container_name", "").lower() == container_name.lower():
            matching_dirs.append((deployment_dir, metadata.get("created_at", "")))

    if not matching_dirs:
        return None
    matching_dirs.sort(key=lambda item: item[1], reverse=True)
    return matching_dirs[0][0]


def get_or_create_deployment_dir(
    deployments_dir: Path,
    container_name: str,
    image_tag: str | None = None,
    deployment_id: str | None = None,
) -> Path:
    if deployment_id:
        return deployments_dir / deployment_id

    deployment_dir = find_active_deployment_dir(deployments_dir, container_name)
    if deployment_dir:
        return deployment_dir

    deployment_id = generate_deployment_id(container_name, image_tag)
    deployment_dir = deployments_dir / deployment_id
    deployment_dir.mkdir(exist_ok=True, parents=True)
    metadata = {
        "container_name": container_name,
        "image_tag": image_tag,
        "created_at": datetime.now().isoformat(),
        "deployment_id": deployment_id,
    }
    (deployment_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    return deployment_dir


def find_all_deployment_dirs(deployments_dir: Path, container_name: str | None = None) -> list:
    deployments = []
    if not deployments_dir.exists():
        return deployments

    for deployment_dir in deployments_dir.iterdir():
        if not deployment_dir.is_dir():
            continue
        metadata_path = deployment_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not container_name or metadata.get("container_name", "").lower() == container_name.lower():
            deployments.append((deployment_dir, metadata))

    deployments.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
    return deployments


def save_deployment_config(
    deployments_dir: Path,
    container_name: str,
    config: dict,
    env: str | None = None,
    image_tag: str | None = None,
) -> Path:
    deployment_dir = get_or_create_deployment_dir(deployments_dir, container_name, image_tag)
    config_filename = f"deployment-{env}.yml" if env else "deployment.yml"
    config_path = deployment_dir / config_filename
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    metadata_path = deployment_dir / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    else:
        metadata = {
            "container_name": container_name,
            "image_tag": image_tag,
            "created_at": datetime.now().isoformat(),
            "deployment_id": deployment_dir.name,
        }
    metadata["last_updated"] = datetime.now().isoformat()
    if env:
        metadata[f"env_{env}_config"] = str(config_path)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return config_path


def load_deployment_config(deployments_dir: Path, container_name: str, env: str | None = None) -> dict | None:
    deployment_dir = find_active_deployment_dir(deployments_dir, container_name)
    if not deployment_dir:
        return None
    config_filename = f"deployment-{env}.yml" if env else "deployment.yml"
    config_path = deployment_dir / config_filename
    if not config_path.exists():
        return None
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
