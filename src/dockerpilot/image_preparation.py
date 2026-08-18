"""Image preparation helpers extracted from deployment services."""

from collections.abc import Callable
from typing import Any, Optional

import docker


EnsureExistingImage = Callable[[str, Optional[str]], bool]
BuildImage = Callable[[str, dict], bool]


def ensure_image_from_existing_container(
    image_tag: str,
    container_name: Optional[str],
    *,
    client: Any,
    logger: Any,
) -> bool:
    """Try to satisfy an image requirement using an existing container image."""
    if not container_name:
        return False

    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        return False
    except Exception as exc:
        logger.debug(f"Could not inspect container {container_name} for image fallback: {exc}")
        return False

    source_image = container.image
    if not source_image:
        return False

    if image_tag in (source_image.tags or []):
        logger.info(f"Using image {image_tag} from existing container {container_name}")
        return True

    if "@" in image_tag:
        logger.info(
            f"Using digest image from existing container {container_name} (skipping local retag for {image_tag})"
        )
        return True

    if ":" in image_tag and image_tag.rfind(":") > image_tag.rfind("/"):
        repository, tag = image_tag.rsplit(":", 1)
    else:
        repository, tag = image_tag, "latest"

    try:
        source_image.tag(repository, tag=tag)
        logger.info(
            f"Tagged existing container image {container.id[:12]} as {repository}:{tag} for deployment fallback"
        )
        return True
    except Exception as exc:
        logger.warning(
            f"Could not tag image from container {container_name} as {image_tag}: {exc}"
        )
        return False


def prepare_image(
    image_tag: str,
    build_config: dict | None = None,
    container_name: Optional[str] = None,
    *,
    client: Any,
    logger: Any,
    ensure_existing_image: EnsureExistingImage,
    build_image: BuildImage,
):
    """Prepare an image using the legacy local/build/pull/fallback sequence."""
    try:
        client.images.get(image_tag)
        logger.info(f"Image {image_tag} already exists locally")
        return True, "Image already exists"
    except docker.errors.ImageNotFound:
        pass

    if ensure_existing_image(image_tag, container_name):
        return True, "Image resolved from existing container"

    if build_config and build_config.get("dockerfile_path"):
        build_success = build_image(image_tag, build_config)
        if build_success:
            return True, "Image built successfully"
        logger.warning(f"Build failed, trying to pull image {image_tag}")

    try:
        logger.info(f"Pulling image {image_tag} from registry...")
        client.images.pull(image_tag)
        return True, "Image pulled successfully"
    except Exception as pull_error:
        if ensure_existing_image(image_tag, container_name):
            return True, "Image resolved from existing container after pull failure"
        error_msg = f"Failed to pull image {image_tag}: {pull_error}"
        logger.error(error_msg)
        return False, error_msg
