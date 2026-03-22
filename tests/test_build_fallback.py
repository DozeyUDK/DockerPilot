"""Tests for Dockerfile resolution and build fallbacks."""

from pathlib import Path

from dockerpilot.deployment_service import DeploymentServiceMixin


class _DummyConsole:
    def __init__(self):
        self.messages = []

    def print(self, *parts, **_kwargs):
        self.messages.append(" ".join(str(part) for part in parts))


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _DummyImages:
    def __init__(self):
        self.pull_calls = []

    def pull(self, tag):
        self.pull_calls.append(tag)


class _DummyClient:
    def __init__(self):
        self.images = _DummyImages()


class _DummyDeploymentService(DeploymentServiceMixin):
    def __init__(self):
        self.console = _DummyConsole()
        self.logger = _DummyLogger()
        self.client = _DummyClient()
        self.build_calls = []

    def _build_image_enhanced(self, image_tag, build_config):
        self.build_calls.append((image_tag, build_config))
        return True


def test_inspect_build_source_auto_detects_single_nested_dockerfile(tmp_path):
    service = _DummyDeploymentService()
    nested_dir = tmp_path / "api"
    nested_dir.mkdir()
    dockerfile = nested_dir / "Dockerfile.dev"
    dockerfile.write_text("FROM alpine:latest\n", encoding="utf-8")

    source_info = service.inspect_build_source(str(tmp_path))

    assert source_info["status"] == "ready"
    assert source_info["auto_detected"] is True
    assert source_info["selected_path"] == dockerfile
    assert source_info["context_path"] == nested_dir


def test_build_image_standalone_pulls_requested_tag_when_enabled(tmp_path):
    service = _DummyDeploymentService()

    success = service.build_image_standalone(
        str(tmp_path),
        "mongo:latest",
        pull_if_missing=True,
    )

    assert success is True
    assert service.client.images.pull_calls == ["mongo:latest"]
    assert service.build_calls == []


def test_build_image_standalone_generates_template_before_building(tmp_path):
    service = _DummyDeploymentService()

    success = service.build_image_standalone(
        str(tmp_path),
        "myapp:latest",
        generate_template="python",
    )

    dockerfile = tmp_path / "Dockerfile"
    assert success is True
    assert dockerfile.exists()
    assert dockerfile.read_text(encoding="utf-8").startswith("FROM python:3.12-slim")
    assert service.client.images.pull_calls == []
    assert service.build_calls == [
        (
            "myapp:latest",
            {
                "dockerfile_path": str(dockerfile),
                "context": str(tmp_path),
                "no_cache": False,
                "pull": True,
                "build_args": {},
            },
        )
    ]
