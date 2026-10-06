import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from dockerpilot import __version__


def test_project_version_matches_package_version():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as file_obj:
        pyproject = tomllib.load(file_obj)

    assert pyproject["project"]["version"] == __version__


def test_release_metadata_versions_stay_in_sync():
    root = Path(__file__).resolve().parents[1]
    version = __version__

    with (root / "DockerPilotExtras" / "frontend" / "package.json").open(encoding="utf-8") as file_obj:
        frontend = json.load(file_obj)
    with (root / "DockerPilotExtras" / "frontend" / "package-lock.json").open(encoding="utf-8") as file_obj:
        frontend_lock = json.load(file_obj)
    with (root / "components" / "dozeyguard" / "Cargo.toml").open("rb") as file_obj:
        dozeyguard = tomllib.load(file_obj)

    assert frontend["version"] == version
    assert frontend_lock["version"] == version
    assert frontend_lock["packages"][""]["version"] == version
    assert dozeyguard["package"]["version"] == version

    readme = (root / "README.md").read_text(encoding="utf-8")
    extras_client = (root / "DockerPilotExtras" / "backend" / "secure_deploy" / "broker_client.py").read_text(encoding="utf-8")
    canary_client = (root / "tools" / "secure_deploy" / "broker_canary_client.py").read_text(encoding="utf-8")

    assert f"- **Version**: {version}" in readme
    assert f'"{version}"' in extras_client
    assert f'DEFAULT_CLIENT_VERSION = "{version}"' in canary_client


def test_deployment_template_exists():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "dockerpilot"
        / "configs"
        / "deployment.yml.template"
    )

    assert template_path.exists()
