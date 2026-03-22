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


def test_deployment_template_exists():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "dockerpilot"
        / "configs"
        / "deployment.yml.template"
    )

    assert template_path.exists()
