"""Environment bindings and deployment-history state helpers for DockerPilot Extras."""

from __future__ import annotations

from datetime import datetime


def load_env_servers_config(*, get_state_store, logger=None) -> dict:
    try:
        config = get_state_store().load_env_servers_config() or {}
        config.setdefault("env_servers", {})
        return config
    except Exception as exc:
        if logger:
            logger.error("Failed to load environments config: %s", exc)
        return {"env_servers": {}}


def save_env_servers_config(config: dict, *, get_state_store, logger=None) -> bool:
    try:
        return bool(get_state_store().save_env_servers_config(config))
    except Exception as exc:
        if logger:
            logger.error("Failed to save environments config: %s", exc)
        return False


def get_deployment_history_data(*, get_state_store, limit: int = 50, logger=None) -> list:
    try:
        return list(get_state_store().get_deployment_history(limit=limit))
    except TypeError:
        try:
            history = list(get_state_store().get_deployment_history())
            return history[-limit:]
        except Exception as exc:
            if logger:
                logger.error("Failed to load deployment history: %s", exc)
            return []
    except Exception as exc:
        if logger:
            logger.error("Failed to load deployment history: %s", exc)
        return []


def append_deployment_history_data(
    entry: dict,
    *,
    get_state_store,
    max_entries: int = 50,
    logger=None,
) -> bool:
    try:
        return bool(get_state_store().append_deployment_history(entry, max_entries=max_entries))
    except TypeError:
        try:
            history = get_deployment_history_data(
                get_state_store=get_state_store,
                limit=max_entries,
                logger=logger,
            )
            history.append(dict(entry))
            return bool(
                get_state_store().replace_deployment_history(
                    history[-max_entries:],
                    max_entries=max_entries,
                )
            )
        except Exception as exc:
            if logger:
                logger.error("Failed to append deployment history: %s", exc)
            return False
    except Exception as exc:
        if logger:
            logger.error("Failed to append deployment history: %s", exc)
        return False


def load_env_container_bindings(*, get_state_store, logger=None) -> dict:
    try:
        config = get_state_store().load_env_container_bindings() or {}
        config.setdefault("env_containers", {})
        return config
    except Exception as exc:
        if logger:
            logger.error("Failed to load env container bindings: %s", exc)
        return {"env_containers": {}}


def save_env_container_bindings(config: dict, *, get_state_store, logger=None) -> bool:
    try:
        return bool(get_state_store().save_env_container_bindings(config))
    except Exception as exc:
        if logger:
            logger.error("Failed to save env container bindings: %s", exc)
        return False


def normalize_env_container_bindings(config: dict, *, now=None) -> dict:
    cfg = config if isinstance(config, dict) else {}
    src_map = cfg.get("env_containers", {}) if isinstance(cfg.get("env_containers", {}), dict) else {}
    normalized = {}
    for env in ("dev", "staging", "prod"):
        values = src_map.get(env, [])
        if not isinstance(values, list):
            values = []
        deduped = []
        seen = set()
        for name in values:
            if not isinstance(name, str):
                continue
            clean = name.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            deduped.append(clean)
        normalized[env] = deduped
    timestamp = (now or datetime.now)().isoformat()
    return {"env_containers": normalized, "updated_at": timestamp}


def move_container_binding(
    container_name: str,
    from_env: str,
    to_env: str,
    *,
    load_bindings,
    save_bindings,
    invalidate_cache=lambda: None,
) -> bool:
    if not container_name:
        return False
    normalized = normalize_env_container_bindings(load_bindings())
    env_containers = normalized["env_containers"]
    for env in env_containers:
        env_containers[env] = [name for name in env_containers[env] if name != container_name]
    if to_env in env_containers:
        env_containers[to_env].append(container_name)
    saved = bool(save_bindings(normalize_env_container_bindings(normalized)))
    if saved:
        invalidate_cache()
    return saved


def move_many_container_bindings(
    container_names: list,
    from_env: str,
    to_env: str,
    *,
    load_bindings,
    save_bindings,
    invalidate_cache=lambda: None,
) -> bool:
    normalized = normalize_env_container_bindings(load_bindings())
    env_containers = normalized["env_containers"]
    unique_names = []
    seen = set()
    for name in container_names or []:
        if isinstance(name, str):
            clean = name.strip()
            if clean and clean not in seen:
                seen.add(clean)
                unique_names.append(clean)
    for container_name in unique_names:
        for env in env_containers:
            env_containers[env] = [existing for existing in env_containers[env] if existing != container_name]
        if to_env in env_containers:
            env_containers[to_env].append(container_name)
    saved = bool(save_bindings(normalize_env_container_bindings(normalized)))
    if saved:
        invalidate_cache()
    return saved


def load_legacy_file_state_snapshot(*, file_store_factory) -> dict:
    file_store = file_store_factory()
    return {
        "servers_config": file_store.load_servers_config(),
        "env_servers_config": file_store.load_env_servers_config(),
        "deployment_history": file_store.get_deployment_history(),
        "env_container_bindings": file_store.load_env_container_bindings(),
    }


def migrate_legacy_file_state_to_store(*, target_store, file_store_factory) -> dict:
    snapshot = load_legacy_file_state_snapshot(file_store_factory=file_store_factory)
    servers_cfg = snapshot.get("servers_config", {}) or {"servers": [], "default_server": "local"}
    env_cfg = snapshot.get("env_servers_config", {}) or {"env_servers": {}}
    history = list(snapshot.get("deployment_history", []) or [])
    bindings = snapshot.get("env_container_bindings", {}) or {"env_containers": {}}
    normalized_bindings = normalize_env_container_bindings(bindings)

    target_store.save_servers_config(servers_cfg)
    target_store.save_env_servers_config(env_cfg)
    target_store.replace_deployment_history(history, max_entries=50)
    target_store.save_env_container_bindings(normalized_bindings)

    return {
        "servers": len(servers_cfg.get("servers", [])),
        "env_mappings": len((env_cfg.get("env_servers") or {}).keys()),
        "history_entries": min(len(history), 50),
        "env_container_bindings": sum(
            len(values) for values in normalized_bindings.get("env_containers", {}).values()
        ),
    }


def resolve_server_id_for_env(env: str, *, load_env_servers) -> str:
    cfg = load_env_servers()
    env_servers = cfg.get("env_servers", {}) if isinstance(cfg, dict) else {}
    return env_servers.get(env, "local")
