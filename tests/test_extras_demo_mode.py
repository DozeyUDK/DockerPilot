from pathlib import Path
import ast
import sys

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
APP_PATH = EXTRAS_DIR / "backend" / "app.py"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services.demo_mode import demo_mutation_is_blocked


def _blocked(method, path, *, enabled=True, allow_mutations=False):
    return demo_mutation_is_blocked(
        enabled=enabled,
        allow_mutations=allow_mutations,
        method=method,
        path=path,
    )


def test_shareable_demo_blocks_state_changing_api_mutations():
    for method, path in (
        ("POST", "/api/storage/configure"),
        ("POST", "/api/containers/migrate"),
        ("POST", "/api/secure-deploy/drafts"),
        ("POST", "/api/command/execute"),
        ("POST", "/api/pipeline/save"),
        ("PUT", "/api/environment/container-bindings"),
        ("PATCH", "/api/servers/example"),
        ("DELETE", "/api/servers/example"),
    ):
        assert _blocked(method, path)


def test_shareable_demo_allows_reads_auth_and_pure_pipeline_generation():
    assert not _blocked("GET", "/api/status")
    assert not _blocked("GET", "/api/containers")
    assert not _blocked("OPTIONS", "/api/storage/configure")
    assert not _blocked("POST", "/api/auth/login")
    assert not _blocked("POST", "/api/auth/logout")
    assert not _blocked("POST", "/api/pipeline/generate")


def test_normal_runtime_is_unchanged_and_interactive_demo_is_explicit_opt_in():
    assert not _blocked("POST", "/api/storage/configure", enabled=False)
    assert not _blocked("POST", "/api/storage/configure", allow_mutations=True)


def test_non_api_routes_are_not_affected():
    assert not _blocked("POST", "/")
    assert not _blocked("POST", "/some-frontend-route")


def test_extras_app_wires_demo_guard_before_request_and_reports_mode():
    source = APP_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "from backend.services.demo_mode import demo_mutation_is_blocked" in source
    assert "def enforce_demo_read_only():" in source
    assert "'demo_mode': DEMO_MODE" in source
    assert "'demo_read_only': bool(DEMO_MODE and not DEMO_ALLOW_MUTATIONS)" in source
