import os
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_migration_bundle_smoke_with_local_docker_cli(tmp_path):
    if (os.environ.get("DOCKERPILOT_MCP_SMOKE_DOCKER") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set DOCKERPILOT_MCP_SMOKE_DOCKER=true to enable Docker-based smoke test.")

    name = "dockerpilot-mcp-smoke-migrate"
    vol = "dockerpilot-mcp-smoke-vol"
    out_dir = tmp_path / "migrations"
    out_dir.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "DOCKERPILOT_MCP_READONLY": "false",
            "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "false",
            "DOCKERPILOT_MCP_ALLOWED_CONTAINERS": "dockerpilot-mcp-smoke-",
            "DOCKERPILOT_MCP_MIGRATIONS_DIR": str(out_dir),
            "DOCKERPILOT_MCP_MIGRATION_ALLOW_ARBITRARY_OUTPUT_DIR": "true",
            "DOCKERPILOT_MCP_MIGRATION_MAX_BUNDLE_BYTES": "200000000",
        }
    )

    try:
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", "-f", vol], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["docker", "volume", "create", vol], check=True)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-v",
                f"{vol}:/data",
                "busybox:latest",
                "sh",
                "-c",
                "echo hello > /data/hello.txt && sleep 300",
            ],
            check=True,
        )

        # Export bundle via Python API call (no MCP wire needed for smoke)
        code_export = (
            "from pathlib import Path; "
            "from dockerpilot.mcp.context import MCPConfig; "
            "from dockerpilot.mcp.migration import MigrationOps; "
            "import docker; "
            "cfg=MCPConfig.from_env(); "
            "ops=MigrationOps(docker.from_env(), cfg); "
            f"b=ops.export_bundle(container_name='{name}', include_data=True, output_dir=Path(r'{out_dir}'), confirm=True); "
            "print(b.path)"
        )
        p = subprocess.run([sys.executable, "-c", code_export], check=True, env=env, capture_output=True, text=True)
        bundle_path = p.stdout.strip().splitlines()[-1]
        assert bundle_path.endswith(".tar") or bundle_path.endswith(".tar.enc")

        # Import dry run (should fail fast because target container exists)
        code_import_dry = (
            "from pathlib import Path; "
            "from dockerpilot.mcp.context import MCPConfig; "
            "from dockerpilot.mcp.migration import MigrationOps; "
            "import docker; "
            "cfg=MCPConfig.from_env(); "
            "ops=MigrationOps(docker.from_env(), cfg); "
            f"out=ops.import_bundle(bundle_path=Path(r'{bundle_path}'), target_name='{name}', start=False, dry_run=True, confirm=True); "
            "print(out)"
        )
        out = subprocess.run([sys.executable, "-c", code_import_dry], check=True, env=env, capture_output=True, text=True)
        assert "target_exists" in out.stdout
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False)
        subprocess.run(["docker", "volume", "rm", "-f", vol], check=False)

