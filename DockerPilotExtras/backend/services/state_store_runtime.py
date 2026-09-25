"""State-store lifecycle management for DockerPilot Extras."""

from __future__ import annotations


class StateStoreRuntime:
    def __init__(
        self,
        *,
        config_dir,
        servers_dir,
        file_store_cls,
        create_store_fn,
        resolve_storage_config_fn,
        sanitize_postgres_config_fn,
        logger,
    ):
        self.config_dir = config_dir
        self.servers_dir = servers_dir
        self.file_store_cls = file_store_cls
        self.create_store_fn = create_store_fn
        self.resolve_storage_config_fn = resolve_storage_config_fn
        self.sanitize_postgres_config_fn = sanitize_postgres_config_fn
        self.logger = logger
        self.store = None
        self.runtime_config: dict = {}
        self.init_warning: str | None = None

    def build_file_store(self):
        return self.file_store_cls(
            config_dir=self.config_dir,
            servers_dir=self.servers_dir,
        )

    def init(self, runtime_config: dict | None = None) -> tuple[bool, str | None]:
        cfg = runtime_config or self.resolve_storage_config_fn(self.config_dir)
        try:
            self.store = self.create_store_fn(
                config_dir=self.config_dir,
                servers_dir=self.servers_dir,
                resolved_cfg=cfg,
            )
            self.runtime_config = cfg
            self.init_warning = None
            return True, None
        except Exception as exc:
            self.init_warning = str(exc)
            self.logger.error(
                "Failed to initialize storage backend '%s': %s. Falling back to file storage.",
                cfg.get("backend", "unknown"),
                exc,
            )
            self.store = self.build_file_store()
            self.runtime_config = {"backend": "file", "postgres": {}}
            return False, str(exc)

    def get(self):
        if self.store is None:
            self.init()
        return self.store

    def status(self) -> dict:
        store = self.get()
        mode = getattr(store, "mode", "file")
        healthy = False
        schema_version = None
        error = None
        try:
            healthy = bool(store.is_healthy())
            schema_version = store.schema_version()
        except Exception as exc:
            error = str(exc)
        return {
            "backend": mode,
            "healthy": healthy,
            "schema_version": schema_version,
            "warning": self.init_warning,
            "error": error,
            "config": {
                "backend": self.runtime_config.get("backend", "file"),
                "postgres": self.sanitize_postgres_config_fn(
                    self.runtime_config.get("postgres", {})
                ),
            },
        }
