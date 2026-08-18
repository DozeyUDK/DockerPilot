"""Dockerfile source discovery helpers extracted from deployment services."""

from collections.abc import Callable
from pathlib import Path
from typing import Any


CandidatePredicate = Callable[[Path], bool]
CandidateDiscovery = Callable[[Path], list[Path]]


def is_dockerfile_candidate(candidate: Path) -> bool:
    """Return True when a filename matches the legacy Dockerfile pattern."""
    lowered = candidate.name.lower()
    return lowered == "dockerfile" or lowered.startswith("dockerfile.")


def discover_dockerfile_candidates(
    search_root: Path,
    *,
    is_candidate: CandidatePredicate,
) -> list[Path]:
    """Search the root and exactly one nested level for Dockerfile candidates."""
    if not search_root.exists() or not search_root.is_dir():
        return []

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(candidate: Path) -> None:
        resolved = candidate.resolve(strict=False)
        if resolved in seen or not candidate.is_file() or not is_candidate(candidate):
            return
        seen.add(resolved)
        candidates.append(candidate)

    for child in sorted(search_root.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file():
            add_candidate(child)

    for child in sorted(search_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        for nested in sorted(child.iterdir(), key=lambda item: item.name.lower()):
            if nested.is_file():
                add_candidate(nested)

    return candidates


def inspect_build_source(
    dockerfile_path: str,
    *,
    is_candidate: CandidatePredicate,
    discover_candidates: CandidateDiscovery,
) -> dict[str, Any]:
    """Inspect build source while preserving legacy callback dispatch behavior."""
    requested_path = Path(dockerfile_path).expanduser()
    if not requested_path.is_absolute():
        requested_path = Path.cwd() / requested_path
    requested_path = requested_path.resolve(strict=False)

    if requested_path.is_file():
        if is_candidate(requested_path):
            return {
                "status": "ready",
                "requested_path": requested_path,
                "context_path": requested_path.parent,
                "dockerfile_name": requested_path.name,
                "selected_path": requested_path,
                "auto_detected": False,
                "candidates": [requested_path],
                "message": f"Using Dockerfile file {requested_path}.",
            }
        return {
            "status": "invalid",
            "requested_path": requested_path,
            "context_path": requested_path.parent,
            "dockerfile_name": requested_path.name,
            "selected_path": None,
            "auto_detected": False,
            "candidates": [],
            "message": f"{requested_path} is a file, but it does not look like a Dockerfile.",
        }

    explicit_dockerfile = requested_path / "Dockerfile"
    if explicit_dockerfile.exists():
        return {
            "status": "ready",
            "requested_path": requested_path,
            "context_path": requested_path,
            "dockerfile_name": "Dockerfile",
            "selected_path": explicit_dockerfile,
            "auto_detected": False,
            "candidates": [explicit_dockerfile],
            "message": f"Using Dockerfile at {explicit_dockerfile}.",
        }

    candidates = discover_candidates(requested_path)
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "status": "ready",
            "requested_path": requested_path,
            "context_path": candidate.parent,
            "dockerfile_name": candidate.name,
            "selected_path": candidate,
            "auto_detected": True,
            "candidates": candidates,
            "message": f"No Dockerfile found directly in {requested_path}. Auto-detected {candidate}.",
        }

    if len(candidates) > 1:
        return {
            "status": "multiple",
            "requested_path": requested_path,
            "context_path": requested_path,
            "dockerfile_name": None,
            "selected_path": None,
            "auto_detected": False,
            "candidates": candidates,
            "message": f"Found multiple Dockerfile candidates under {requested_path}.",
        }

    return {
        "status": "missing",
        "requested_path": requested_path,
        "context_path": requested_path,
        "dockerfile_name": None,
        "selected_path": None,
        "auto_detected": False,
        "candidates": [],
        "message": f"No Dockerfile found in {requested_path}.",
    }
