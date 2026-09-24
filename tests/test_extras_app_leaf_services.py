from pathlib import Path
from types import SimpleNamespace
import sys

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.services import deployment_files, health_detection, local_postgres


class _Logger:
    def debug(self, *_args, **_kwargs): pass
    def info(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass


def test_deployment_files_round_trip(tmp_path):
    cfg = {"deployment": {"container_name": "api", "image": "demo:1"}}
    path = deployment_files.save_deployment_config(tmp_path, "api", cfg, "prod", "demo:1")
    assert path.name == "deployment-prod.yml"
    assert deployment_files.load_deployment_config(tmp_path, "api", "prod") == cfg
    found = deployment_files.find_active_deployment_dir(tmp_path, "api")
    assert found == path.parent
    assert deployment_files.find_all_deployment_dirs(tmp_path, "api")[0][0] == found


def test_deployment_files_ignore_invalid_metadata(tmp_path):
    bad = tmp_path / "api_bad"
    bad.mkdir()
    (bad / "metadata.json").write_text("{not-json", encoding="utf-8")
    assert deployment_files.find_active_deployment_dir(tmp_path, "api") is None


def test_health_detection_prefers_pilot_detector():
    pilot = SimpleNamespace(_detect_health_check_endpoint=lambda _image: "/ready")
    assert health_detection.detect_health_check_endpoint(
        "demo:1", get_dockerpilot=lambda: pilot, logger=_Logger()
    ) == "/ready"


def test_health_detection_fallback_handles_non_http_and_known_http():
    redis_container = SimpleNamespace(image=SimpleNamespace(tags=["redis:7"], id="id"))
    grafana_container = SimpleNamespace(image=SimpleNamespace(tags=["grafana/grafana:latest"], id="id"))
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **_kwargs: [redis_container, grafana_container]))
    pilot = SimpleNamespace(client=client)
    get_pilot = lambda: pilot
    assert health_detection.detect_from_running_containers(
        "redis:7", get_dockerpilot=get_pilot, logger=_Logger()
    ) is None
    assert health_detection.detect_from_running_containers(
        "grafana/grafana:latest", get_dockerpilot=get_pilot, logger=_Logger()
    ) == "/api/health"


def test_parse_env_list_preserves_equals_in_values():
    assert local_postgres.parse_env_list(["A=1", "TOKEN=a=b=c", "bad", None]) == {
        "A": "1", "TOKEN": "a=b=c"
    }


def test_discover_local_postgres_uses_container_metadata(monkeypatch):
    class NotFound(Exception): pass
    container = SimpleNamespace(
        name="postgres-dozeyserver",
        status="running",
        id="abc",
        image=SimpleNamespace(tags=["postgres:16"], id="img"),
        attrs={
            "Config": {"Env": ["POSTGRES_DB=dp", "POSTGRES_USER=dozey", "POSTGRES_PASSWORD=secret"]},
            "NetworkSettings": {"Ports": {"5432/tcp": [{"HostPort": "55432"}]}},
        },
        reload=lambda: None,
    )
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container))
    fake_docker = SimpleNamespace(from_env=lambda: client, errors=SimpleNamespace(NotFound=NotFound))
    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    result = local_postgres.discover_local_postgres(
        "postgres-dozeyserver",
        default_schema="dockerpilot",
        default_table_prefix="dp_",
        default_auto_create_schema=True,
        sanitize_postgres_config=lambda cfg: {**cfg, "password": "***"},
    )
    assert result["success"] is True
    assert result["postgres"]["port"] == 55432
    assert result["postgres"]["database"] == "dp"
    assert result["postgres_sanitized"]["password"] == "***"
