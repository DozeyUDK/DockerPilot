from pathlib import Path
from types import SimpleNamespace
import threading
import sys

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.services import server_runtime


class Logger:
    def warning(self, *_a, **_k): pass
    def info(self, *_a, **_k): pass


def test_get_or_create_pilot_caches_instance(tmp_path):
    made=[]
    class Pilot:
        def __init__(self, **kwargs):
            self.kwargs=kwargs
            made.append(self)
    instances={}
    lock=threading.RLock()
    one, sid = server_runtime.get_or_create_pilot(
        "local",
        instances=instances,
        lock=lock,
        config_dir=tmp_path,
        load_servers_config=lambda: {"servers": []},
        pilot_cls=Pilot,
        log_level="INFO",
        logger=Logger(),
    )
    two, _ = server_runtime.get_or_create_pilot(
        "local",
        instances=instances,
        lock=lock,
        config_dir=tmp_path,
        load_servers_config=lambda: {"servers": []},
        pilot_cls=Pilot,
        log_level="INFO",
        logger=Logger(),
    )
    assert one is two
    assert len(made)==1
    assert sid=="local"
    assert one.kwargs["register_signal_handlers"] is False


def test_get_selected_server_config_and_by_id():
    load=lambda: {"servers":[{"id":"prod","hostname":"10.0.0.2"}]}
    assert server_runtime.get_selected_server_config("prod", load_servers_config=load)["hostname"]=="10.0.0.2"
    assert server_runtime.get_selected_server_config("missing", load_servers_config=load) is None
    assert server_runtime.get_server_config_by_id("local", load_servers_config=load)=={"id":"local"}
    assert server_runtime.get_server_config_by_id("prod", load_servers_config=load)["id"]=="prod"


def test_inventory_parses_containers_and_dedupes_images():
    def execute(_server, command, **_kwargs):
        if command.startswith("ps "):
            return "api\tdemo:1\trunning\tUp 2 minutes\n"
        return "demo\t1\ndemo\t1\n<none>\tlatest\n"
    containers, images, error = server_runtime.get_containers_and_images_for_server(
        {"id":"x"}, execute_docker_command=execute
    )
    assert containers==[{"name":"api","image":"demo:1","state":"running","status":"Up 2 minutes"}]
    assert images==["demo:1"]
    assert error is None


def test_inventory_reports_host_error_only_when_both_surfaces_empty():
    def execute(*_a, **_k): raise RuntimeError("offline")
    containers, images, error = server_runtime.get_containers_and_images_for_server(
        {"id":"x"}, execute_docker_command=execute
    )
    assert containers==[] and images==[] and error=="offline"
