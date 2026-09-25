from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_devcontainer_boots_real_checkout_with_docker_in_docker():
    config = json.loads((ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))

    assert config["forwardPorts"] == [5000]
    assert set(config["portsAttributes"]) == {"5000"}
    assert "docker-in-docker" in " ".join(config["features"])
    assert "node" in " ".join(config["features"])
    assert config["postCreateCommand"] == "bash demo/bootstrap.sh"
    assert config["postStartCommand"] == "bash demo/start.sh"


def test_demo_compose_has_no_host_docker_socket_or_privileged_services():
    compose = yaml.safe_load((DEMO / "compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"web-dev", "web-staging", "web-prod", "cache"} <= set(services)

    for service in services.values():
        assert service.get("privileged") is not True
        assert not service.get("ports")
        mounts = service.get("volumes", []) or []
        assert all("/var/run/docker.sock" not in str(mount) for mount in mounts)
        labels = service.get("labels", {}) or {}
        assert labels.get("com.dockerpilot.demo") == "true"


def test_demo_bootstrap_uses_current_checkout_instead_of_copying_source():
    bootstrap = (DEMO / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'pip install -e ".[tui]"' in bootstrap
    assert "npm run build --prefix DockerPilotExtras/frontend" in bootstrap
    assert "git clone" not in bootstrap
    assert not (DEMO / "src").exists()
    assert not (DEMO / "DockerPilotExtras").exists()


def test_demo_runtime_credentials_are_generated_outside_repo(tmp_path):
    runtime = _load_module("demo_prepare_runtime", DEMO / "prepare_runtime.py")
    state_dir = tmp_path / "state"
    values = runtime.create_runtime(state_dir, codespaces=False)

    env_path = state_dir / "runtime.env"
    assert env_path.exists()
    assert values["WEB_AUTH_USERNAME"] == "demo"
    assert values["WEB_AUTH_PASSWORD"]
    assert values["WEB_AUTH_PASSWORD"] not in (DEMO / "README.md").read_text(encoding="utf-8")
    assert values["DOCKERPILOT_DEMO"] == "true"
    assert values["DOCKERPILOT_DEMO_ALLOW_MUTATIONS"] == "false"
    assert values["SESSION_COOKIE_SECURE"] == "false"
    if os.name == "posix":
        assert env_path.stat().st_mode & 0o777 == 0o600

    codespaces_state = tmp_path / "codespaces-state"
    codespaces_values = runtime.create_runtime(codespaces_state, codespaces=True)
    assert codespaces_values["SESSION_COOKIE_SECURE"] == "true"
    assert codespaces_values["DOCKERPILOT_DEMO_ALLOW_MUTATIONS"] == "false"
    assert codespaces_values["WEB_AUTH_PASSWORD"] != values["WEB_AUTH_PASSWORD"]
    assert codespaces_values["SECRET_KEY"] != values["SECRET_KEY"]


def test_public_share_script_refuses_interactive_demo():
    script = (DEMO / "public.sh").read_text(encoding="utf-8")
    assert "DOCKERPILOT_DEMO_ALLOW_MUTATIONS" in script
    assert "refusing to expose an interactive demo publicly" in script
    assert "gh auth status" in script
    assert "gh codespace ports visibility 5000:public" in script


def test_demo_seed_uses_isolated_home_and_environment_bindings(tmp_path):
    seed = _load_module("demo_seed", DEMO / "seed_demo.py")
    config_dir = seed.seed_demo(tmp_path)

    assert config_dir == tmp_path.resolve() / ".dockerpilot_extras"
    environments = json.loads((config_dir / "environments.json").read_text(encoding="utf-8"))
    bindings = json.loads((config_dir / "env_container_bindings.json").read_text(encoding="utf-8"))
    servers = json.loads((config_dir / "servers" / "servers.json").read_text(encoding="utf-8"))

    assert environments["env_servers"] == {"dev": "local", "staging": "local", "prod": "local"}
    assert bindings["env_containers"] == seed.ENV_CONTAINERS
    assert servers == {"servers": [], "default_server": "local"}


def test_interactive_start_forces_codespaces_port_private_before_backend():
    script = (DEMO / "start.sh").read_text(encoding="utf-8")
    assert "DOCKERPILOT_DEMO_ALLOW_MUTATIONS" in script
    assert 'bash "$ROOT/demo/private.sh"' in script
    assert "refusing interactive startup" in script
    assert script.index('bash "$ROOT/demo/private.sh"') < script.index('docker compose -p dockerpilot-demo')


def test_interactive_start_normalizes_mutation_flag_like_backend():
    script = (DEMO / "start.sh").read_text(encoding="utf-8")
    assert 'MUTATIONS_FLAG="${DOCKERPILOT_DEMO_ALLOW_MUTATIONS:-false}"' in script
    assert 'MUTATIONS_FLAG="${MUTATIONS_FLAG,,}"' in script
    assert 'if [[ "$MUTATIONS_FLAG" == "true"' in script


def test_start_restarts_backend_when_live_access_mode_differs():
    script = (DEMO / "start.sh").read_text(encoding="utf-8")
    assert "/api/auth/status" in script
    assert 'if [[ "$LIVE_MODE" != "$MUTATIONS_FLAG" ]]' in script
    assert "configured access mode changed; restarting DockerPilotExtras" in script


def test_public_generator_has_resource_bounds():
    app_source = (ROOT / "DockerPilotExtras" / "backend" / "app.py").read_text(encoding="utf-8")
    demo_mode_source = (ROOT / "DockerPilotExtras" / "backend" / "services" / "demo_mode.py").read_text(encoding="utf-8")
    assert "DEMO_GENERATOR_MAX_REQUEST_BYTES" in app_source
    assert "validate_demo_generator_payload" in app_source
    assert "DEMO_GENERATOR_MAX_TEST_COMMANDS = 32" in demo_mode_source
    assert "DEMO_GENERATOR_MAX_STRING_LENGTH = 4096" in demo_mode_source


def test_backend_demo_import_is_real_multiline_python():
    source = (ROOT / "DockerPilotExtras" / "backend" / "app.py").read_text(encoding="utf-8")
    assert "from backend.services.demo_mode import (\\\\n" not in source
    compile(source, "backend/app.py", "exec")


def test_public_generator_rejects_unknown_length_before_json_buffering():
    source = (ROOT / "DockerPilotExtras" / "backend" / "app.py").read_text(encoding="utf-8")
    guard = source[source.index("def enforce_demo_read_only"):source.index("def require_auth_for_api")]
    assert "request.content_length is None" in guard
    assert "411" in guard
    assert guard.index("request.content_length is None") < guard.index("request.get_json")


def test_reset_rejects_root_home_repo_and_non_dedicated_state_paths():
    script = (DEMO / "reset.sh").read_text(encoding="utf-8")
    assert 'STATE_REAL="$(realpath -m "$STATE_DIR")"' in script
    assert '"$STATE_REAL" == "/"' in script
    assert '"$STATE_REAL" == "$HOME_REAL"' in script
    assert '"$STATE_REAL" == "$ROOT_REAL"' in script
    assert '$(basename "$STATE_REAL")" != ".dockerpilot_demo"' in script
    assert 'rm -rf -- "$STATE_REAL/home"' in script
