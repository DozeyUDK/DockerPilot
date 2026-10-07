"""Security regressions for Docker daemon access guidance."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docs_do_not_recommend_mutating_docker_socket_permissions():
    paths = (
        ROOT / "README.md",
        ROOT / "src" / "dockerpilot" / "configs" / "docs-troubleshooting.md.template",
        ROOT / "src" / "dockerpilot" / "services" / "system_validation.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    forbidden = (
        "sudo chown $USER:docker /var/run/docker.sock",
        "sudo chmod 660 /var/run/docker.sock",
        "sudo usermod -aG docker $USER",
    )
    for snippet in forbidden:
        assert snippet not in combined

    assert "root-equivalent" in combined
