from pathlib import Path
import sys

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.services.state_store_runtime import StateStoreRuntime


class Logger:
    def error(self, *_a, **_k): pass


class FileStore:
    mode = "file"
    def __init__(self, **kwargs): self.kwargs=kwargs
    def is_healthy(self): return True
    def schema_version(self): return 1


def _runtime(tmp_path, create_store_fn):
    return StateStoreRuntime(
        config_dir=tmp_path,
        servers_dir=tmp_path / "servers",
        file_store_cls=FileStore,
        create_store_fn=create_store_fn,
        resolve_storage_config_fn=lambda _p: {"backend":"postgres","postgres":{"password":"secret"}},
        sanitize_postgres_config_fn=lambda cfg: {**cfg, "password":"***"} if cfg else {},
        logger=Logger(),
    )


def test_state_store_runtime_initializes_requested_backend(tmp_path):
    class Store(FileStore):
        mode="postgres"
        def schema_version(self): return 3
    runtime=_runtime(tmp_path, lambda **_kwargs: Store())
    ok, warning=runtime.init()
    assert ok is True and warning is None
    status=runtime.status()
    assert status["backend"]=="postgres"
    assert status["healthy"] is True
    assert status["schema_version"]==3
    assert status["config"]["postgres"]["password"]=="***"


def test_state_store_runtime_falls_back_to_file(tmp_path):
    def fail(**_kwargs): raise RuntimeError("db unavailable")
    runtime=_runtime(tmp_path, fail)
    ok, warning=runtime.init()
    assert ok is False and "db unavailable" in warning
    assert runtime.get().mode=="file"
    status=runtime.status()
    assert status["backend"]=="file"
    assert status["warning"]=="db unavailable"
