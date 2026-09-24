import re
import shlex
from pathlib import Path

from dockerpilot.cli.parser import build_cli_parser


ROOT = Path(__file__).resolve().parents[1]


def test_readme_monitor_examples_match_nested_cli():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = [
        "dockerpilot monitor dashboard --duration 300",
        "dockerpilot monitor dashboard webapp database cache --duration 600",
    ]

    parser = build_cli_parser()
    for example in examples:
        assert example in readme
        parsed = parser.parse_args(shlex.split(example)[1:])
        assert parsed.command == "monitor"
        assert parsed.monitor_action == "dashboard"
    assert "dockerpilot monitor --duration 300" not in readme
    assert "dockerpilot monitor webapp database cache --duration 600" not in readme


def test_supported_python_version_is_consistent_in_user_entrypoints():
    paths = [
        "README.md",
        "DockerPilotExtras/README.md",
        "install.sh",
        "install.ps1",
        "install.bat",
        "DockerPilotExtras/setup_extras.sh",
        "DockerPilotExtras/check_web_setup.py",
        "src/dockerpilot/services/system_validation.py",
    ]

    for relative_path in paths:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Python 3.9" not in content, relative_path
        assert "Python 3.10" in content, relative_path

    assert '"$PYTHON_MINOR" -lt 10' in (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 10)" in (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 10)" in (ROOT / "install.bat").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 10)" in (ROOT / "DockerPilotExtras" / "setup_extras.sh").read_text(encoding="utf-8")
    assert "version.minor < 10" in (ROOT / "DockerPilotExtras" / "check_web_setup.py").read_text(encoding="utf-8")
    assert "python_version < (3, 10)" in (ROOT / "src" / "dockerpilot" / "services" / "system_validation.py").read_text(encoding="utf-8")


def test_readmes_explain_saved_pipeline_library_boundaries():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    extras_readme = (ROOT / "DockerPilotExtras" / "README.md").read_text(encoding="utf-8")

    assert "Saved Pipelines" in root_readme
    assert "does not execute a pipeline or push anything" in root_readme
    assert "Saved Pipeline Library" in extras_readme
    assert "`GET /api/pipeline/saved`" in extras_readme


def test_extras_configuration_docs_match_environment_loading_behavior():
    extras_readme = (ROOT / "DockerPilotExtras" / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(extras_readme.split())

    assert "cp .env.example .env" not in extras_readme
    assert "does not load a `.env` file automatically" in normalized
    assert 'export CORS_ORIGINS="https://extras.example.com"' in extras_readme


def test_readme_version_matches_package_metadata():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)

    assert version is not None
    assert f"**Version**: {version.group(1)}" in readme


def test_local_readme_links_resolve():
    for relative_path in ["README.md", "DockerPilotExtras/README.md"]:
        readme_path = ROOT / relative_path
        content = readme_path.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", content)
        for target in targets:
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            local_target = target.split("#", 1)[0]
            assert (readme_path.parent / local_target).exists(), (relative_path, target)
