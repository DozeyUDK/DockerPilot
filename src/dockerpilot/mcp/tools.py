from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .context import MCPConfig
from .diagnostics import detect_unhealthy, diagnose_container, diagnose_stack, explain_container_state
from .docker_ops import DockerClientProvider, DockerOps
from .migration import MigrationOps
from .safety import (
    ToolBlocked,
    require_confirm,
    require_container_allowed,
    require_destructive_allowed,
    require_not_readonly,
)


class DockerPilotTools:
    def __init__(self, config: MCPConfig):
        self._config = config
        self._provider = DockerClientProvider(config)
        self._ops = DockerOps(self._provider)
        self._migration: Optional[MigrationOps] = None

    def _migration_ops(self) -> MigrationOps:
        if self._migration is None:
            self._migration = MigrationOps(self._provider.client(), self._config)
        return self._migration

    # ---- Read-only tools ----

    def dockerpilot_system_summary(self) -> dict[str, Any]:
        return self._ops.system_summary()

    def dockerpilot_list_containers(
        self, all: bool = True, include_ports: bool = True, include_labels: bool = False
    ) -> dict[str, Any]:
        data = self._ops.list_containers(all_containers=all, include_ports=include_ports, include_labels=include_labels)
        containers = data.get("containers") or []
        filtered = []
        for c in containers:
            name = c.get("name") or ""
            if self._config.is_container_denied(name):
                continue
            if self._config.allowed_containers and not self._config.is_container_allowed(name):
                continue
            filtered.append(c)
        return {"containers": filtered}

    def dockerpilot_container_inspect(self, name: str, redact: bool = True) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return self._ops.inspect_container_curated(name=name, redact=redact)

    def dockerpilot_container_logs(
        self,
        name: str,
        tail: int = 200,
        since: Optional[str] = None,
        timestamps: bool = True,
        redact: bool = True,
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return self._ops.container_logs(name=name, tail=tail, since=since, timestamps=timestamps, redact=redact)

    def dockerpilot_container_stats(self, name: str) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return self._ops.container_stats(name=name)

    def dockerpilot_list_images(self, all: bool = False, hide_untagged: bool = True) -> dict[str, Any]:
        return self._ops.list_images(all_images=all, hide_untagged=hide_untagged)

    def dockerpilot_list_networks(self) -> dict[str, Any]:
        return self._ops.list_networks()

    def dockerpilot_list_volumes(self) -> dict[str, Any]:
        return self._ops.list_volumes(redact=True)

    def dockerpilot_detect_unhealthy(self, include_exited: bool = True) -> dict[str, Any]:
        return detect_unhealthy(self._ops, include_exited=include_exited)

    def dockerpilot_explain_container_state(self, name: str) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return explain_container_state(self._ops, name=name)

    def dockerpilot_diagnose_container(self, name: str, include_logs: bool = True, tail: int = 200) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return diagnose_container(self._ops, name=name, include_logs=include_logs, tail=tail)

    def dockerpilot_diagnose_stack(self, include_logs: bool = False, tail: int = 100) -> dict[str, Any]:
        return diagnose_stack(self._ops, include_logs=include_logs, tail=tail)

    # ---- Migration (bundle export/import) ----

    def dockerpilot_migration_plan(self, name: str, include_data: bool = True) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        return self._migration_ops().plan(container_name=name, include_data=include_data)

    def dockerpilot_migration_export_bundle(
        self,
        name: str,
        include_data: bool = True,
        output_dir: Optional[str] = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_confirm(confirm)
        out = output_dir or os.environ.get("DOCKERPILOT_MCP_MIGRATIONS_DIR") or str(Path.home() / ".dockerpilot" / "migrations")
        bundle = self._migration_ops().export_bundle(
            container_name=name,
            include_data=include_data,
            output_dir=Path(out),
            confirm=True,
        )
        return {
            "bundle_path": str(bundle.path),
            "manifest": bundle.manifest,
        }

    def dockerpilot_migration_import_bundle(
        self,
        bundle_path: str,
        target_name: Optional[str] = None,
        start: bool = False,
        dry_run: bool = True,
        on_conflict: str = "fail",
        confirm: bool = False,
    ) -> dict[str, Any]:
        require_not_readonly(self._config)
        require_confirm(confirm)
        if on_conflict == "replace":
            require_destructive_allowed(self._config)
        return self._migration_ops().import_bundle(
            bundle_path=Path(bundle_path),
            target_name=target_name,
            start=start,
            dry_run=dry_run,
            on_conflict=on_conflict,
            allow_replace=bool(self._config.allow_destructive),
            confirm=True,
        )

    # ---- Controlled action tools ----

    def dockerpilot_container_start(self, name: str, confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_confirm(confirm)
        changed, message = self._ops.container_start(name=name, dry_run=dry_run)
        return {"changed": bool(changed), "dry_run": bool(dry_run), "message": message}

    def dockerpilot_container_stop(
        self, name: str, timeout: int = 10, confirm: bool = False, dry_run: bool = True
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_confirm(confirm)
        changed, message = self._ops.container_stop(name=name, timeout=timeout, dry_run=dry_run)
        return {"changed": bool(changed), "dry_run": bool(dry_run), "message": message}

    def dockerpilot_container_restart(
        self, name: str, timeout: int = 10, confirm: bool = False, dry_run: bool = True
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_confirm(confirm)
        changed, message = self._ops.container_restart(name=name, timeout=timeout, dry_run=dry_run)
        return {"changed": bool(changed), "dry_run": bool(dry_run), "message": message}

    def dockerpilot_container_exec(
        self, name: str, command: list[str], timeout: Optional[int] = None, confirm: bool = False
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_confirm(confirm)
        return self._ops.container_exec(name=name, command=command, timeout=timeout)

    def dockerpilot_container_remove(
        self, name: str, force: bool = False, confirm: bool = False, dry_run: bool = True
    ) -> dict[str, Any]:
        require_container_allowed(self._config, name)
        require_not_readonly(self._config)
        require_destructive_allowed(self._config)
        require_confirm(confirm)
        changed, message = self._ops.container_remove(name=name, force=force, dry_run=dry_run)
        return {"changed": bool(changed), "dry_run": bool(dry_run), "message": message}

    def dockerpilot_image_prune(
        self, dangling_only: bool = True, confirm: bool = False, dry_run: bool = True
    ) -> dict[str, Any]:
        require_not_readonly(self._config)
        require_destructive_allowed(self._config)
        require_confirm(confirm)
        if dry_run:
            return self._ops.image_prune(dangling_only=dangling_only, dry_run=True)
        return self._ops.image_prune(dangling_only=dangling_only, dry_run=False)
