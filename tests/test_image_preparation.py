"""Characterization tests for deployment image preparation behavior."""

from types import SimpleNamespace

import docker
import pytest

from dockerpilot.deployment_service import DeploymentServiceMixin


class RecordingLogger:
    def __init__(self) -> None:
        self.debugs: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def debug(self, message) -> None:
        self.debugs.append(str(message))

    def info(self, message) -> None:
        self.infos.append(str(message))

    def warning(self, message) -> None:
        self.warnings.append(str(message))

    def error(self, message) -> None:
        self.errors.append(str(message))


class FakeImage:
    def __init__(self, *, tags=None, tag_error: Exception | None = None) -> None:
        self.tags = list(tags or [])
        self.tag_error = tag_error
        self.tag_calls: list[tuple[str, str]] = []

    def tag(self, repository, tag="latest"):
        self.tag_calls.append((repository, tag))
        if self.tag_error is not None:
            raise self.tag_error
        return True


class FakeContainer:
    def __init__(self, image) -> None:
        self.image = image
        self.id = "abcdef1234567890"


class FakeContainers:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.get_calls: list[str] = []

    def get(self, name):
        self.get_calls.append(name)
        if self.error is not None:
            raise self.error
        return self.result


class FakeImages:
    def __init__(
        self,
        *,
        get_result=None,
        get_error: Exception | None = None,
        pull_result=None,
        pull_error: Exception | None = None,
    ) -> None:
        self.get_result = get_result
        self.get_error = get_error
        self.pull_result = pull_result
        self.pull_error = pull_error
        self.get_calls: list[str] = []
        self.pull_calls: list[str] = []

    def get(self, tag):
        self.get_calls.append(tag)
        if self.get_error is not None:
            raise self.get_error
        return self.get_result

    def pull(self, tag):
        self.pull_calls.append(tag)
        if self.pull_error is not None:
            raise self.pull_error
        return self.pull_result


def make_service(*, images=None, containers=None) -> DeploymentServiceMixin:
    service = DeploymentServiceMixin()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(
        images=images or FakeImages(),
        containers=containers or FakeContainers(),
    )
    return service


def test_ensure_existing_image_returns_false_without_container_name():
    containers = FakeContainers(error=AssertionError("container lookup must not happen"))
    service = make_service(containers=containers)

    assert service._ensure_image_from_existing_container("example:latest", None) is False
    assert containers.get_calls == []


def test_ensure_existing_image_returns_false_when_container_is_missing():
    containers = FakeContainers(error=docker.errors.NotFound("missing"))
    service = make_service(containers=containers)

    assert service._ensure_image_from_existing_container("example:latest", "app") is False
    assert containers.get_calls == ["app"]


def test_ensure_existing_image_logs_debug_and_returns_false_on_inspection_error():
    containers = FakeContainers(error=RuntimeError("daemon unavailable"))
    service = make_service(containers=containers)

    assert service._ensure_image_from_existing_container("example:latest", "app") is False
    assert "Could not inspect container app for image fallback: daemon unavailable" in service.logger.debugs


def test_ensure_existing_image_returns_false_when_container_has_no_image():
    service = make_service(containers=FakeContainers(result=FakeContainer(None)))

    assert service._ensure_image_from_existing_container("example:latest", "app") is False


def test_ensure_existing_image_reuses_matching_tag_without_retagging():
    image = FakeImage(tags=["example:latest"])
    service = make_service(containers=FakeContainers(result=FakeContainer(image)))

    assert service._ensure_image_from_existing_container("example:latest", "app") is True
    assert image.tag_calls == []
    assert "Using image example:latest from existing container app" in service.logger.infos


def test_ensure_existing_image_accepts_digest_without_retagging():
    image = FakeImage(tags=["other:latest"])
    service = make_service(containers=FakeContainers(result=FakeContainer(image)))

    assert service._ensure_image_from_existing_container("repo@example@sha256:abc", "app") is True
    assert image.tag_calls == []


def test_ensure_existing_image_retags_explicit_tag_and_defaults_latest():
    tagged = FakeImage(tags=[])
    tagged_service = make_service(containers=FakeContainers(result=FakeContainer(tagged)))

    assert tagged_service._ensure_image_from_existing_container("registry:5000/team/app:v2", "app") is True
    assert tagged.tag_calls == [("registry:5000/team/app", "v2")]

    latest = FakeImage(tags=[])
    latest_service = make_service(containers=FakeContainers(result=FakeContainer(latest)))

    assert latest_service._ensure_image_from_existing_container("registry:5000/team/app", "app") is True
    assert latest.tag_calls == [("registry:5000/team/app", "latest")]


def test_ensure_existing_image_logs_warning_and_returns_false_when_retag_fails():
    image = FakeImage(tags=[], tag_error=RuntimeError("tag failed"))
    service = make_service(containers=FakeContainers(result=FakeContainer(image)))

    assert service._ensure_image_from_existing_container("example:v2", "app") is False
    assert any("Could not tag image from container app as example:v2: tag failed" in msg for msg in service.logger.warnings)


def test_prepare_image_returns_immediately_when_image_exists_locally():
    images = FakeImages(get_result=object())

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            raise AssertionError("fallback must not run")

        def _build_image_enhanced(self, image_tag, build_config):
            raise AssertionError("build must not run")

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)

    assert service._prepare_image("example:latest", {"dockerfile_path": "."}, "app") == (
        True,
        "Image already exists",
    )
    assert images.pull_calls == []


def test_prepare_image_uses_existing_container_fallback_before_build_or_pull():
    images = FakeImages(get_error=docker.errors.ImageNotFound("missing"))
    calls: list[tuple[str, object]] = []

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            calls.append(("ensure", (image_tag, container_name)))
            return True

        def _build_image_enhanced(self, image_tag, build_config):
            calls.append(("build", (image_tag, build_config)))
            return True

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)

    assert service._prepare_image("example:latest", {"dockerfile_path": "."}, "app") == (
        True,
        "Image resolved from existing container",
    )
    assert calls == [("ensure", ("example:latest", "app"))]
    assert images.pull_calls == []


def test_prepare_image_preserves_dynamic_dispatch_for_build_override():
    images = FakeImages(get_error=docker.errors.ImageNotFound("missing"))
    build_calls: list[tuple[str, dict]] = []

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            return False

        def _build_image_enhanced(self, image_tag, build_config):
            build_calls.append((image_tag, build_config))
            return True

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)
    build_config = {"dockerfile_path": "Dockerfile", "pull": False}

    assert service._prepare_image("example:v2", build_config, "app") == (
        True,
        "Image built successfully",
    )
    assert build_calls == [("example:v2", build_config)]
    assert images.pull_calls == []


def test_prepare_image_pulls_after_build_failure():
    images = FakeImages(
        get_error=docker.errors.ImageNotFound("missing"),
        pull_result=object(),
    )

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            return False

        def _build_image_enhanced(self, image_tag, build_config):
            return False

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)

    assert service._prepare_image("example:v2", {"dockerfile_path": "."}, "app") == (
        True,
        "Image pulled successfully",
    )
    assert images.pull_calls == ["example:v2"]
    assert "Build failed, trying to pull image example:v2" in service.logger.warnings


def test_prepare_image_retries_existing_container_fallback_after_pull_failure():
    images = FakeImages(
        get_error=docker.errors.ImageNotFound("missing"),
        pull_error=RuntimeError("registry down"),
    )
    ensure_results = iter([False, True])
    ensure_calls: list[tuple[str, str | None]] = []

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            ensure_calls.append((image_tag, container_name))
            return next(ensure_results)

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)

    assert service._prepare_image("example:v2", None, "app") == (
        True,
        "Image resolved from existing container after pull failure",
    )
    assert ensure_calls == [("example:v2", "app"), ("example:v2", "app")]


def test_prepare_image_returns_error_after_pull_and_fallback_failure():
    images = FakeImages(
        get_error=docker.errors.ImageNotFound("missing"),
        pull_error=RuntimeError("registry down"),
    )

    class Service(DeploymentServiceMixin):
        def _ensure_image_from_existing_container(self, image_tag, container_name):
            return False

    service = Service()
    service.logger = RecordingLogger()
    service.client = SimpleNamespace(images=images)

    assert service._prepare_image("example:v2", None, "app") == (
        False,
        "Failed to pull image example:v2: registry down",
    )
    assert service.logger.errors == ["Failed to pull image example:v2: registry down"]


def test_prepare_image_does_not_swallow_non_not_found_lookup_errors():
    images = FakeImages(get_error=RuntimeError("daemon unavailable"))
    service = make_service(images=images)

    with pytest.raises(RuntimeError, match="daemon unavailable"):
        service._prepare_image("example:v2", None, "app")
