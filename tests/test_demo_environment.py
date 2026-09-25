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
    assert values["SESSION_COOKIE_SECURE"] == "false"
    if os.name == "posix":
        assert env_path.stat().st_mode & 0o777 == 0o600

    codespaces_state = tmp_path / "codespaces-state"
    codespaces_values = runtime.create_runtime(codespaces_state, codespaces=True)
    assert codespaces_values["SESSION_COOKIE_SECURE"] == "true"
    assert codespaces_values["WEB_AUTH_PASSWORD"] != values["WEB_AUTH_PASSWORD"]
    assert codespaces_values["SECRET_KEY"] != values["SECRET_KEY"]


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
