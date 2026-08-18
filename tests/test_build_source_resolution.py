"""Characterization tests for Dockerfile/build-source resolution behavior."""

from pathlib import Path

from dockerpilot.deployment_service import DeploymentServiceMixin


def make_service() -> DeploymentServiceMixin:
    return DeploymentServiceMixin()


def test_is_dockerfile_candidate_is_case_insensitive_and_requires_supported_name():
    service = make_service()

    assert service._is_dockerfile_candidate(Path("Dockerfile")) is True
    assert service._is_dockerfile_candidate(Path("dockerFILE")) is True
    assert service._is_dockerfile_candidate(Path("Dockerfile.dev")) is True
    assert service._is_dockerfile_candidate(Path("DOCKERFILE.PROD")) is True
    assert service._is_dockerfile_candidate(Path("Dockerfilex")) is False
    assert service._is_dockerfile_candidate(Path("Containerfile")) is False


def test_inspect_build_source_accepts_explicit_dockerfile_style_file(tmp_path):
    service = make_service()
    dockerfile = tmp_path / "Dockerfile.dev"
    dockerfile.write_text("FROM alpine\n", encoding="utf-8")

    result = service.inspect_build_source(str(dockerfile))

    assert result == {
        "status": "ready",
        "requested_path": dockerfile,
        "context_path": tmp_path,
        "dockerfile_name": "Dockerfile.dev",
        "selected_path": dockerfile,
        "auto_detected": False,
        "candidates": [dockerfile],
        "message": f"Using Dockerfile file {dockerfile}.",
    }


def test_inspect_build_source_rejects_explicit_non_dockerfile_file(tmp_path):
    service = make_service()
    source = tmp_path / "build.txt"
    source.write_text("FROM alpine\n", encoding="utf-8")

    result = service.inspect_build_source(str(source))

    assert result["status"] == "invalid"
    assert result["requested_path"] == source
    assert result["selected_path"] is None
    assert result["candidates"] == []


def test_inspect_build_source_prefers_direct_standard_dockerfile(tmp_path):
    service = make_service()
    direct = tmp_path / "Dockerfile"
    direct.write_text("FROM alpine\n", encoding="utf-8")
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    (nested_dir / "Dockerfile.dev").write_text("FROM busybox\n", encoding="utf-8")

    result = service.inspect_build_source(str(tmp_path))

    assert result["status"] == "ready"
    assert result["selected_path"] == direct
    assert result["context_path"] == tmp_path
    assert result["dockerfile_name"] == "Dockerfile"
    assert result["auto_detected"] is False
    assert result["candidates"] == [direct]


def test_discovery_scans_root_and_exactly_one_nested_level(tmp_path):
    service = make_service()
    nested = tmp_path / "api"
    nested.mkdir()
    candidate = nested / "Dockerfile.dev"
    candidate.write_text("FROM alpine\n", encoding="utf-8")
    deeper = nested / "deeper"
    deeper.mkdir()
    (deeper / "Dockerfile.prod").write_text("FROM busybox\n", encoding="utf-8")

    result = service._discover_dockerfile_candidates(tmp_path)

    assert result == [candidate]


def test_discovery_keeps_root_candidates_before_nested_candidates_and_sorts_each_level(tmp_path):
    service = make_service()
    root_b = tmp_path / "Dockerfile.z"
    root_a = tmp_path / "dockerfile.A"
    root_b.write_text("FROM alpine\n", encoding="utf-8")
    root_a.write_text("FROM alpine\n", encoding="utf-8")

    sub_b = tmp_path / "z-sub"
    sub_a = tmp_path / "A-sub"
    sub_b.mkdir()
    sub_a.mkdir()
    nested_b = sub_b / "Dockerfile.b"
    nested_a = sub_a / "Dockerfile.c"
    nested_b.write_text("FROM alpine\n", encoding="utf-8")
    nested_a.write_text("FROM alpine\n", encoding="utf-8")

    result = service._discover_dockerfile_candidates(tmp_path)

    assert result == [root_a, root_b, nested_a, nested_b]


def test_inspect_build_source_reports_multiple_candidates_in_discovery_order(tmp_path):
    service = make_service()
    first = tmp_path / "Dockerfile.dev"
    first.write_text("FROM alpine\n", encoding="utf-8")
    nested = tmp_path / "svc"
    nested.mkdir()
    second = nested / "Dockerfile.prod"
    second.write_text("FROM busybox\n", encoding="utf-8")

    result = service.inspect_build_source(str(tmp_path))

    assert result["status"] == "multiple"
    assert result["selected_path"] is None
    assert result["candidates"] == [first, second]


def test_inspect_build_source_reports_missing_when_no_candidate_exists(tmp_path):
    service = make_service()
    (tmp_path / "README.md").write_text("nothing here\n", encoding="utf-8")

    result = service.inspect_build_source(str(tmp_path))

    assert result["status"] == "missing"
    assert result["selected_path"] is None
    assert result["candidates"] == []


def test_inspect_build_source_preserves_dynamic_dispatch_for_candidate_override(tmp_path):
    class ContainerfileService(DeploymentServiceMixin):
        def _is_dockerfile_candidate(self, candidate: Path) -> bool:
            return candidate.name.lower() == "containerfile"

    service = ContainerfileService()
    nested = tmp_path / "api"
    nested.mkdir()
    containerfile = nested / "Containerfile"
    containerfile.write_text("FROM alpine\n", encoding="utf-8")

    result = service.inspect_build_source(str(tmp_path))

    assert result["status"] == "ready"
    assert result["selected_path"] == containerfile
    assert result["context_path"] == nested
    assert result["auto_detected"] is True
