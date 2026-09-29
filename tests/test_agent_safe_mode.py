from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.safety import ToolBlocked
from dockerpilot.mcp.tools import DockerPilotTools

pytest.importorskip("flask")
pytest.importorskip("flask_restful")
pytest.importorskip("cryptography")

ROOT = Path(__file__).resolve().parents[1]


def _load_extras_app(monkeypatch, tmp_path: Path):
    extras = ROOT / "DockerPilotExtras"
    src = ROOT / "src"
    for path in (extras, src):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOCKERPILOT_AGENT_SAFE_MODE", "true")
    monkeypatch.setenv("DOCKERPILOT_DEMO", "false")
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    monkeypatch.setenv("WEB_AUTH_TOTP_SECRET", "")
    monkeypatch.setenv("SECRET_KEY", "agent-safe-test-key")
    monkeypatch.setenv("SECURE_DEPLOY_STORE_ROOT", str(tmp_path / "secure-deploy"))
    monkeypatch.setenv("FLASK_ENV", "development")

    for name in list(sys.modules):
        if name == "backend.app" or name == "backend.api" or name.startswith("backend.secure_deploy"):
            sys.modules.pop(name, None)

    module = importlib.import_module("backend.app")
    importlib.reload(module)
    return module


def test_agent_safe_mcp_forces_readonly_and_disables_destructive_flags():
    env = {
        "DOCKERPILOT_AGENT_SAFE_MODE": "true",
        "DOCKERPILOT_MCP_READONLY": "false",
        "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "true",
    }
    cfg = MCPConfig.from_env(env)

    assert cfg.agent_safe_mode is True
    assert cfg.readonly is True
    assert cfg.allow_destructive is False

    # The security decision is captured in the frozen startup config; changing
    # environment input afterwards cannot alter this already-running instance.
    env["DOCKERPILOT_AGENT_SAFE_MODE"] = "false"
    env["DOCKERPILOT_MCP_READONLY"] = "false"
    env["DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE"] = "true"
    assert cfg.agent_safe_mode is True
    assert cfg.readonly is True
    assert cfg.allow_destructive is False


def test_agent_safe_mcp_blocks_explicit_mutation_tools_before_docker_access():
    cfg = MCPConfig.from_env(
        {
            "DOCKERPILOT_AGENT_SAFE_MODE": "true",
            "DOCKERPILOT_MCP_READONLY": "false",
            "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "true",
        }
    )
    tools = DockerPilotTools(cfg)

    calls = [
        lambda: tools.dockerpilot_container_exec("demo", ["id"], confirm=True),
        lambda: tools.dockerpilot_container_start("demo", confirm=True, dry_run=False),
        lambda: tools.dockerpilot_container_stop("demo", confirm=True, dry_run=False),
        lambda: tools.dockerpilot_container_restart("demo", confirm=True, dry_run=False),
        lambda: tools.dockerpilot_container_remove("demo", force=True, confirm=True, dry_run=False),
        lambda: tools.dockerpilot_image_prune(dangling_only=False, confirm=True, dry_run=False),
        lambda: tools.dockerpilot_migration_export_bundle("demo", include_data=True, confirm=True),
        lambda: tools.dockerpilot_migration_import_bundle(
            "/tmp/does-not-matter.tar",
            target_name="demo",
            start=True,
            dry_run=False,
            on_conflict="replace",
            confirm=True,
        ),
    ]

    for call in calls:
        with pytest.raises(ToolBlocked, match="Write operations are disabled"):
            call()


def test_agent_safe_extras_blocks_legacy_mutation_matrix(monkeypatch, tmp_path):
    module = _load_extras_app(monkeypatch, tmp_path)
    client = module.app.test_client()

    legacy_mutations = [
        "/api/command/execute",
        "/api/deployment/execute",
        "/api/containers/blue-green-replace",
        "/api/environment/promote",
        "/api/environment/promote-single",
        "/api/containers/migrate",
        "/api/containers/migrations",
        "/api/storage/bootstrap-local-postgres",
        "/api/storage/configure",
        "/api/servers/create",
    ]

    for path in legacy_mutations:
        response = client.post(path, json={"image": "attacker.example/postgres:latest"})
        assert response.status_code == 403, path
        body = response.get_json()
        assert body["code"] == "agent_safe_mutation_blocked", path
        assert body["agent_safe_mode"] is True, path

    # The guard captures its startup decision. A compromised caller changing
    # os.environ in-process later must not turn legacy mutation back on.
    monkeypatch.setenv("DOCKERPILOT_AGENT_SAFE_MODE", "false")
    response = client.post("/api/storage/bootstrap-local-postgres", json={"image": "postgres:latest"})
    assert response.status_code == 403
    assert response.get_json()["code"] == "agent_safe_mutation_blocked"


def test_agent_safe_extras_allows_auth_and_secure_deploy_gate(monkeypatch, tmp_path):
    module = _load_extras_app(monkeypatch, tmp_path)
    client = module.app.test_client()

    # Session-only auth operations stay available.
    login = client.post("/api/auth/login", json={})
    assert login.status_code == 200
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    # Secure Deploy is the reviewed mutation contract and therefore reaches its
    # stricter auth/MFA gate rather than the legacy agent-safe blocker.
    response = client.post("/api/secure-deploy/validate", json={"spec": {}})
    assert response.status_code in {403, 503}
    body = response.get_json()
    assert body["error"]["code"] == "secure_deploy_auth_required"
