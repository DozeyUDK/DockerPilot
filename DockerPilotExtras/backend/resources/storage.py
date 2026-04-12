"""Storage backend API resources."""

from __future__ import annotations

import time


def create_storage_resources(
    *,
    Resource,
    app,
    request,
    default_postgres_schema,
    default_postgres_table_prefix,
    storage_error_cls,
    get_storage_status,
    test_postgres_connection,
    discover_local_postgres,
    sanitize_postgres_config,
    ensure_local_postgres_container,
    create_store,
    migrate_legacy_file_state_to_store,
    save_storage_config,
    init_state_store,
    build_postgres_dsn,
):
    """Return storage resource classes with injected dependencies."""

    class StorageStatus(Resource):
        """Storage backend status and schema info."""

        def get(self):
            status = get_storage_status()
            status["success"] = True
            return status

    class StorageTestPostgres(Resource):
        """Test PostgreSQL connectivity and optionally apply schema."""

        def post(self):
            try:
                data = request.get_json() or {}
                postgres_cfg = data.get("postgres") or {}
                if not postgres_cfg:
                    return {"success": False, "error": "Missing postgres configuration"}, 400
                ensure_schema = bool(data.get("ensure_schema", True))
                result = test_postgres_connection(postgres_cfg, ensure_schema=ensure_schema)
                status_code = 200 if result.get("success") else 400
                return result, status_code
            except Exception as exc:
                return {"success": False, "error": str(exc)}, 500

    class StorageDiscoverLocalPostgres(Resource):
        """Discover local PostgreSQL container settings."""

        def get(self):
            container_name = request.args.get("container_name", "postgres-dozeyserver")
            result = discover_local_postgres(container_name=container_name)
            if result.get("success"):
                result["postgres"] = sanitize_postgres_config(result.get("postgres", {}))
            return result, (200 if result.get("success") else 404)

    class StorageBootstrapLocalPostgres(Resource):
        """Create/start local PostgreSQL container and optionally configure storage backend."""

        def post(self):
            try:
                data = request.get_json() or {}
                container_name = data.get("container_name", "postgres-dozeyserver")
                image = data.get("image", "postgres:16-alpine")
                host_port = int(data.get("host_port", 5432))
                database = data.get("database", "dockerpilot_extras")
                user = data.get("user", "dockerpilot")
                password = data.get("password", "dockerpilot_change_me")
                schema = data.get("schema", default_postgres_schema)
                table_prefix = data.get("table_prefix", default_postgres_table_prefix)
                auto_create_schema = bool(data.get("auto_create_schema", True))
                volume_name = data.get("volume_name") or f"{container_name}-data"
                configure_storage = bool(data.get("configure_storage", True))
                migrate_from_file = bool(data.get("migrate_from_file", True))

                container, created = ensure_local_postgres_container(
                    container_name=container_name,
                    image=image,
                    host_port=host_port,
                    database=database,
                    user=user,
                    password=password,
                    volume_name=volume_name,
                )

                postgres_cfg = {
                    "host": "127.0.0.1",
                    "port": host_port,
                    "database": database,
                    "user": user,
                    "password": password,
                    "sslmode": "prefer",
                    "schema": schema,
                    "table_prefix": table_prefix,
                    "auto_create_schema": auto_create_schema,
                    "container_name": container_name,
                }

                deadline = time.time() + int(data.get("wait_seconds", 45))
                last_error = None
                while time.time() < deadline:
                    test_result = test_postgres_connection(postgres_cfg, ensure_schema=False)
                    if test_result.get("success"):
                        last_error = None
                        break
                    last_error = test_result.get("error")
                    time.sleep(2)
                if last_error:
                    return {
                        "success": False,
                        "error": f"Container started but PostgreSQL not ready yet: {last_error}",
                        "container_name": container_name,
                        "created": created,
                    }, 500

                migration_info = {}
                if configure_storage:
                    runtime_cfg = {"backend": "postgres", "postgres": postgres_cfg}
                    target_store = create_store(
                        config_dir=app.config["CONFIG_DIR"],
                        servers_dir=app.config["SERVERS_DIR"],
                        resolved_cfg=runtime_cfg,
                    )
                    if migrate_from_file:
                        migration_info = migrate_legacy_file_state_to_store(target_store)
                    save_storage_config(app.config["CONFIG_DIR"], runtime_cfg)
                    init_state_store(runtime_cfg)

                return {
                    "success": True,
                    "created": created,
                    "container": {
                        "name": container.name,
                        "status": container.status,
                        "id": container.id,
                    },
                    "postgres": sanitize_postgres_config(postgres_cfg),
                    "storage_configured": configure_storage,
                    "migration": migration_info,
                }
            except Exception as exc:
                return {"success": False, "error": str(exc)}, 500

    class StorageConfigure(Resource):
        """Configure active state backend (file/postgres) and run migration if requested."""

        def post(self):
            try:
                data = request.get_json() or {}
                backend = str(data.get("backend", "")).strip().lower()
                persist = bool(data.get("persist", True))
                migrate_from_file = bool(data.get("migrate_from_file", True))

                if backend not in {"file", "postgres"}:
                    return {"success": False, "error": "backend must be 'file' or 'postgres'"}, 400

                if backend == "file":
                    runtime_cfg = {"backend": "file", "postgres": {}}
                    if persist:
                        save_storage_config(app.config["CONFIG_DIR"], runtime_cfg)
                    init_state_store(runtime_cfg)
                    return {
                        "success": True,
                        "message": "Switched to file storage",
                        "storage": get_storage_status(),
                    }

                postgres_cfg = data.get("postgres") or {}
                if not postgres_cfg and data.get("container_name"):
                    discovery = discover_local_postgres(container_name=str(data.get("container_name")))
                    if discovery.get("success"):
                        postgres_cfg = discovery.get("postgres") or {}
                if not postgres_cfg:
                    return {"success": False, "error": "Missing postgres config for backend=postgres"}, 400

                if "schema" in data and data.get("schema"):
                    postgres_cfg["schema"] = data.get("schema")
                if "table_prefix" in data and data.get("table_prefix") is not None:
                    postgres_cfg["table_prefix"] = data.get("table_prefix")
                if "tables" in data and isinstance(data.get("tables"), dict):
                    postgres_cfg["tables"] = data.get("tables")
                if "auto_create_schema" in data:
                    postgres_cfg["auto_create_schema"] = bool(data.get("auto_create_schema"))

                runtime_cfg = {"backend": "postgres", "postgres": postgres_cfg}
                target_store = create_store(
                    config_dir=app.config["CONFIG_DIR"],
                    servers_dir=app.config["SERVERS_DIR"],
                    resolved_cfg=runtime_cfg,
                )
                migration_info = {}
                if migrate_from_file:
                    migration_info = migrate_legacy_file_state_to_store(target_store)

                if persist:
                    save_storage_config(app.config["CONFIG_DIR"], runtime_cfg)
                init_state_store(runtime_cfg)

                return {
                    "success": True,
                    "message": "PostgreSQL storage configured",
                    "storage": get_storage_status(),
                    "dsn_preview": build_postgres_dsn(sanitize_postgres_config(postgres_cfg)),
                    "migration": migration_info,
                }
            except storage_error_cls as exc:
                return {"success": False, "error": str(exc)}, 400
            except Exception as exc:
                return {"success": False, "error": str(exc)}, 500

    return (
        StorageStatus,
        StorageTestPostgres,
        StorageDiscoverLocalPostgres,
        StorageBootstrapLocalPostgres,
        StorageConfigure,
    )
