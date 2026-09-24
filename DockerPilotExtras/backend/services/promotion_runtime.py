"""Environment promotion runtime helpers for DockerPilot Extras."""

from __future__ import annotations

from pathlib import Path

import yaml


_ENV_RESOURCE_PRESETS = {
    "dev": {"cpu": "0.5", "memory": "512Mi"},
    "staging": {"cpu": "1.0", "memory": "1Gi"},
    "prod": {"cpu": "2.0", "memory": "2Gi"},
}


def apply_env_resource_presets(deployment: dict, target_env: str) -> None:
    preset = _ENV_RESOURCE_PRESETS.get(target_env)
    if not preset:
        return
    deployment["cpu_limit"] = preset["cpu"]
    deployment["memory_limit"] = preset["memory"]


def write_remote_file(
    server_config: dict,
    remote_path: str,
    content: str,
    *,
    build_remote_file_write_command,
    execute_command,
) -> None:
    command = build_remote_file_write_command(remote_path, content)
    execute_command(server_config, command, check_exit_status=True)


def promote_config_to_server(
    server_id: str,
    config_path_str: str,
    from_env: str,
    to_env: str,
    skip_backup: bool = False,
    *,
    get_dockerpilot,
    get_server_config_by_id,
    write_remote_file_fn,
    build_dockerpilot_deploy_command,
    execute_command,
    logger,
) -> bool:
    try:
        config_path = Path(config_path_str or "")
        if not config_path_str or not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path_str}")

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        deployment = config.get("deployment") or {}
        if not isinstance(deployment, dict):
            raise ValueError("Invalid deployment config format (deployment must be a dict)")

        container_name = deployment.get("container_name") or config_path.parent.name.split("_")[0]
        apply_env_resource_presets(deployment, to_env)
        deployment_type = "blue-green" if to_env == "prod" else "rolling"

        if server_id == "local":
            pilot = get_dockerpilot()
            return bool(
                pilot.environment_promotion(
                    from_env,
                    to_env,
                    config_path_str,
                    skip_backup,
                )
            )

        server_config = get_server_config_by_id(server_id)
        if not server_config:
            raise ValueError(f"Target server '{server_id}' not found in servers config")

        username = server_config.get("username", "root")
        remote_config_path = (
            f"/home/{username}/.dockerpilot_extras/deployments/"
            f"{container_name}/deployment-{to_env}.yml"
        )
        promoted_yaml = yaml.dump(config, default_flow_style=False, allow_unicode=True)
        write_remote_file_fn(server_config, remote_config_path, promoted_yaml)

        command = build_dockerpilot_deploy_command(
            remote_config_path,
            deployment_type,
            skip_backup=skip_backup,
        )
        execute_command(server_config, command, check_exit_status=True)
        return True
    except Exception as exc:
        logger.error(
            "Remote promotion failed (%s->%s on %s): %s",
            from_env,
            to_env,
            server_id,
            exc,
            exc_info=True,
        )
        return False
