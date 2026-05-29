from __future__ import annotations

import logging
import sys
from typing import Optional

from .context import MCPConfig
from .safety import ToolBlocked
from .tools import DockerPilotTools


def _require_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "DockerPilot MCP support is not installed. Install with: pip install -e '.[mcp]'"
        ) from exc
    return FastMCP, ToolError


def create_app(config: MCPConfig | None = None):
    FastMCP, ToolError = _require_mcp()
    cfg = MCPConfig.from_env() if config is None else config
    tools = DockerPilotTools(cfg)

    app = FastMCP(
        name="DockerPilot",
        instructions=(
            "DockerPilot MCP server for safe Docker state inspection and controlled container actions. "
            "Write operations are disabled by default via DOCKERPILOT_MCP_READONLY=true."
        ),
    )

    def _call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolBlocked as exc:
            raise ToolError(str(exc))
        except Exception as exc:
            raise ToolError(str(exc))

    # ---- Read-only tools ----

    @app.tool(name="dockerpilot_system_summary")
    def dockerpilot_system_summary() -> dict:
        return _call(tools.dockerpilot_system_summary)

    @app.tool(name="dockerpilot_list_containers")
    def dockerpilot_list_containers(all: bool = True, include_ports: bool = True, include_labels: bool = False) -> dict:
        return _call(tools.dockerpilot_list_containers, all=all, include_ports=include_ports, include_labels=include_labels)

    @app.tool(name="dockerpilot_container_inspect")
    def dockerpilot_container_inspect(name: str, redact: bool = True) -> dict:
        return _call(tools.dockerpilot_container_inspect, name=name, redact=redact)

    @app.tool(name="dockerpilot_container_logs")
    def dockerpilot_container_logs(
        name: str,
        tail: int = 200,
        since: Optional[str] = None,
        timestamps: bool = True,
        redact: bool = True,
    ) -> dict:
        return _call(tools.dockerpilot_container_logs, name=name, tail=tail, since=since, timestamps=timestamps, redact=redact)

    @app.tool(name="dockerpilot_container_stats")
    def dockerpilot_container_stats(name: str) -> dict:
        return _call(tools.dockerpilot_container_stats, name=name)

    @app.tool(name="dockerpilot_list_images")
    def dockerpilot_list_images(all: bool = False, hide_untagged: bool = True) -> dict:
        return _call(tools.dockerpilot_list_images, all=all, hide_untagged=hide_untagged)

    @app.tool(name="dockerpilot_list_networks")
    def dockerpilot_list_networks() -> dict:
        return _call(tools.dockerpilot_list_networks)

    @app.tool(name="dockerpilot_list_volumes")
    def dockerpilot_list_volumes() -> dict:
        return _call(tools.dockerpilot_list_volumes)

    @app.tool(name="dockerpilot_detect_unhealthy")
    def dockerpilot_detect_unhealthy(include_exited: bool = True) -> dict:
        return _call(tools.dockerpilot_detect_unhealthy, include_exited=include_exited)

    @app.tool(name="dockerpilot_explain_container_state")
    def dockerpilot_explain_container_state(name: str) -> dict:
        return _call(tools.dockerpilot_explain_container_state, name=name)

    @app.tool(name="dockerpilot_diagnose_container")
    def dockerpilot_diagnose_container(name: str, include_logs: bool = True, tail: int = 200) -> dict:
        return _call(tools.dockerpilot_diagnose_container, name=name, include_logs=include_logs, tail=tail)

    @app.tool(name="dockerpilot_diagnose_stack")
    def dockerpilot_diagnose_stack(include_logs: bool = False, tail: int = 100) -> dict:
        return _call(tools.dockerpilot_diagnose_stack, include_logs=include_logs, tail=tail)

    # ---- Controlled actions ----

    @app.tool(name="dockerpilot_container_start")
    def dockerpilot_container_start(name: str, confirm: bool = False, dry_run: bool = True) -> dict:
        return _call(tools.dockerpilot_container_start, name=name, confirm=confirm, dry_run=dry_run)

    @app.tool(name="dockerpilot_container_stop")
    def dockerpilot_container_stop(name: str, timeout: int = 10, confirm: bool = False, dry_run: bool = True) -> dict:
        return _call(tools.dockerpilot_container_stop, name=name, timeout=timeout, confirm=confirm, dry_run=dry_run)

    @app.tool(name="dockerpilot_container_restart")
    def dockerpilot_container_restart(name: str, timeout: int = 10, confirm: bool = False, dry_run: bool = True) -> dict:
        return _call(tools.dockerpilot_container_restart, name=name, timeout=timeout, confirm=confirm, dry_run=dry_run)

    @app.tool(name="dockerpilot_container_exec")
    def dockerpilot_container_exec(
        name: str,
        command: list[str],
        timeout: Optional[int] = None,
        confirm: bool = False,
    ) -> dict:
        return _call(tools.dockerpilot_container_exec, name=name, command=command, timeout=timeout, confirm=confirm)

    @app.tool(name="dockerpilot_container_remove")
    def dockerpilot_container_remove(name: str, force: bool = False, confirm: bool = False, dry_run: bool = True) -> dict:
        return _call(tools.dockerpilot_container_remove, name=name, force=force, confirm=confirm, dry_run=dry_run)

    @app.tool(name="dockerpilot_image_prune")
    def dockerpilot_image_prune(dangling_only: bool = True, confirm: bool = False, dry_run: bool = True) -> dict:
        return _call(tools.dockerpilot_image_prune, dangling_only=dangling_only, confirm=confirm, dry_run=dry_run)

    # ---- Migration (bundle export/import) ----

    @app.tool(name="dockerpilot_migration_plan")
    def dockerpilot_migration_plan(name: str, include_data: bool = True) -> dict:
        return _call(tools.dockerpilot_migration_plan, name=name, include_data=include_data)

    @app.tool(name="dockerpilot_migration_export_bundle")
    def dockerpilot_migration_export_bundle(
        name: str,
        include_data: bool = True,
        output_dir: Optional[str] = None,
        confirm: bool = False,
    ) -> dict:
        return _call(
            tools.dockerpilot_migration_export_bundle,
            name=name,
            include_data=include_data,
            output_dir=output_dir,
            confirm=confirm,
        )

    @app.tool(name="dockerpilot_migration_import_bundle")
    def dockerpilot_migration_import_bundle(
        bundle_path: str,
        target_name: Optional[str] = None,
        start: bool = False,
        dry_run: bool = True,
        on_conflict: str = "fail",
        confirm: bool = False,
    ) -> dict:
        return _call(
            tools.dockerpilot_migration_import_bundle,
            bundle_path=bundle_path,
            target_name=target_name,
            start=start,
            dry_run=dry_run,
            on_conflict=on_conflict,
            confirm=confirm,
        )

    return app


def main(argv: list[str] | None = None) -> None:
    _ = argv  # stdio transport does not need args in v1
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    app = create_app()
    app.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
