"""
Storage backends for DockerPilot Extras runtime state.

Supports:
- file-based JSON storage (legacy/default)
- PostgreSQL storage with schema migration checks
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional dependency in file mode
    psycopg = None
    dict_row = None


SCHEMA_VERSION = 1
DEFAULT_STORAGE_MODE = "file"
STORAGE_CONFIG_FILENAME = "storage.json"
DEFAULT_POSTGRES_SCHEMA = "DockerPilot"
DEFAULT_POSTGRES_TABLE_PREFIX = "dp_"
DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA = False

TABLE_ROLE_TO_SUFFIX = {
    "schema_migrations": "schema_migrations",
    "servers": "servers",
    "settings": "settings",
    "env_servers": "env_servers",
    "deployment_history": "deployment_history",
}

ENV_STORAGE_BACKEND = ("DP_STORAGE_BACKEND", "DOCKERPILOT_EXTRAS_STORAGE_BACKEND")
ENV_POSTGRES_DSN = ("DP_POSTGRES_DSN", "DOCKERPILOT_EXTRAS_POSTGRES_DSN")
ENV_POSTGRES_HOST = ("DP_POSTGRES_HOST", "DOCKERPILOT_EXTRAS_POSTGRES_HOST")
ENV_POSTGRES_PORT = ("DP_POSTGRES_PORT", "DOCKERPILOT_EXTRAS_POSTGRES_PORT")
ENV_POSTGRES_DB = ("DP_POSTGRES_DB", "DOCKERPILOT_EXTRAS_POSTGRES_DB")
ENV_POSTGRES_USER = ("DP_POSTGRES_USER", "DOCKERPILOT_EXTRAS_POSTGRES_USER")
ENV_POSTGRES_PASSWORD = ("DP_POSTGRES_PASSWORD", "DOCKERPILOT_EXTRAS_POSTGRES_PASSWORD")
ENV_POSTGRES_SSLMODE = ("DP_POSTGRES_SSLMODE", "DOCKERPILOT_EXTRAS_POSTGRES_SSLMODE")
ENV_POSTGRES_SCHEMA = ("DP_POSTGRES_SCHEMA", "DOCKERPILOT_EXTRAS_POSTGRES_SCHEMA")
ENV_POSTGRES_TABLE_PREFIX = ("DP_POSTGRES_TABLE_PREFIX", "DOCKERPILOT_EXTRAS_POSTGRES_TABLE_PREFIX")
ENV_POSTGRES_AUTO_CREATE_SCHEMA = (
    "DP_POSTGRES_AUTO_CREATE_SCHEMA",
    "DOCKERPILOT_EXTRAS_POSTGRES_AUTO_CREATE_SCHEMA",
)


class StorageError(RuntimeError):
    """Base storage error."""


class PostgresUnavailableError(StorageError):
    """Raised when PostgreSQL backend is requested but unavailable."""


class SchemaValidationError(StorageError):
    """Raised when PostgreSQL schema migration metadata is inconsistent."""


def _get_first_env(*names: Tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime.now(timezone.utc)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _is_valid_identifier(name: str) -> bool:
    if not isinstance(name, str):
        return False
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _normalize_layout_config(postgres_config: Dict[str, Any]) -> Tuple[str, bool, Dict[str, str]]:
    cfg = dict(postgres_config or {})
    schema = str(cfg.get("schema", DEFAULT_POSTGRES_SCHEMA))
    if not _is_valid_identifier(schema):
        raise StorageError(
            f"Invalid PostgreSQL schema name '{schema}'. Use letters, digits, underscore; cannot start with digit."
        )

    auto_create_schema = _parse_bool(
        cfg.get("auto_create_schema"),
        default=DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
    )

    table_prefix = str(cfg.get("table_prefix", DEFAULT_POSTGRES_TABLE_PREFIX))
    if table_prefix and not _is_valid_identifier(table_prefix.rstrip("_") + "_"):
        # Prefix itself may end with underscore; validate as identifier-ish payload.
        raise StorageError(
            f"Invalid PostgreSQL table prefix '{table_prefix}'. Use letters, digits, underscore."
        )

    tables_cfg = cfg.get("tables", {}) or {}
    if not isinstance(tables_cfg, dict):
        raise StorageError("PostgreSQL 'tables' configuration must be an object")

    table_names: Dict[str, str] = {}
    for role, suffix in TABLE_ROLE_TO_SUFFIX.items():
        candidate = tables_cfg.get(role) or f"{table_prefix}{suffix}"
        candidate = str(candidate)
        if not _is_valid_identifier(candidate):
            raise StorageError(
                f"Invalid PostgreSQL table name for '{role}': '{candidate}'. "
                "Use letters, digits, underscore; cannot start with digit."
            )
        table_names[role] = candidate

    return schema, auto_create_schema, table_names


def _build_migration_sql_v1(schema: str, table_names: Dict[str, str]) -> str:
    s = _quote_ident(schema)
    t = {key: f"{s}.{_quote_ident(name)}" for key, name in table_names.items()}
    env_constraint = _quote_ident(f"{table_names['env_servers']}_env_name_check")
    idx_name = _quote_ident(f"idx_{table_names['deployment_history']}_occurred_at")
    return f"""
CREATE TABLE IF NOT EXISTS {t['schema_migrations']} (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {t['servers']} (
    server_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    secret_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {t['settings']} (
    setting_key TEXT PRIMARY KEY,
    setting_value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {t['env_servers']} (
    env_name TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT {env_constraint}
        CHECK (env_name IN ('dev', 'staging', 'prod'))
);

CREATE TABLE IF NOT EXISTS {t['deployment_history']} (
    history_id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT,
    config_path TEXT,
    details JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS {idx_name}
    ON {t['deployment_history']} (occurred_at DESC, history_id DESC);
"""


def sanitize_postgres_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    sanitized = dict(config)
    for key in ("password", "dsn"):
        if key in sanitized and sanitized[key]:
            sanitized[key] = "***"
    return sanitized


def build_postgres_dsn(config: Dict[str, Any]) -> str:
    dsn = (config or {}).get("dsn")
    if dsn:
        return str(dsn)

    host = (config or {}).get("host", "127.0.0.1")
    port = int((config or {}).get("port", 5432))
    database = (config or {}).get("database", "dockerpilot_extras")
    user = (config or {}).get("user", "postgres")
    password = (config or {}).get("password", "")
    sslmode = (config or {}).get("sslmode", "prefer")

    if password:
        auth = f"{user}:{password}"
    else:
        auth = user
    return f"postgresql://{auth}@{host}:{port}/{database}?sslmode={sslmode}"


def default_storage_config() -> Dict[str, Any]:
    return {
        "backend": DEFAULT_STORAGE_MODE,
        "postgres": {
            "schema": DEFAULT_POSTGRES_SCHEMA,
            "table_prefix": DEFAULT_POSTGRES_TABLE_PREFIX,
            "auto_create_schema": DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
        },
    }


def load_storage_config(config_dir: Path) -> Dict[str, Any]:
    cfg_path = Path(config_dir) / STORAGE_CONFIG_FILENAME
    if not cfg_path.exists():
        return default_storage_config()
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        if not isinstance(cfg, dict):
            return default_storage_config()
        cfg.setdefault("backend", DEFAULT_STORAGE_MODE)
        cfg.setdefault("postgres", {})
        return cfg
    except Exception:
        return default_storage_config()


def save_storage_config(config_dir: Path, cfg: Dict[str, Any]) -> None:
    cfg_path = Path(config_dir) / STORAGE_CONFIG_FILENAME
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def resolve_storage_config(config_dir: Path) -> Dict[str, Any]:
    cfg = load_storage_config(config_dir)
    backend = _get_first_env(*ENV_STORAGE_BACKEND)
    if backend:
        cfg["backend"] = backend.strip().lower()

    postgres_cfg = dict(cfg.get("postgres", {}))
    dsn = _get_first_env(*ENV_POSTGRES_DSN)
    host = _get_first_env(*ENV_POSTGRES_HOST)
    port = _get_first_env(*ENV_POSTGRES_PORT)
    database = _get_first_env(*ENV_POSTGRES_DB)
    user = _get_first_env(*ENV_POSTGRES_USER)
    password = _get_first_env(*ENV_POSTGRES_PASSWORD)
    sslmode = _get_first_env(*ENV_POSTGRES_SSLMODE)
    schema = _get_first_env(*ENV_POSTGRES_SCHEMA)
    table_prefix = _get_first_env(*ENV_POSTGRES_TABLE_PREFIX)
    auto_create_schema = _get_first_env(*ENV_POSTGRES_AUTO_CREATE_SCHEMA)

    if dsn:
        postgres_cfg["dsn"] = dsn
    if host:
        postgres_cfg["host"] = host
    if port:
        try:
            postgres_cfg["port"] = int(port)
        except ValueError:
            pass
    if database:
        postgres_cfg["database"] = database
    if user:
        postgres_cfg["user"] = user
    if password:
        postgres_cfg["password"] = password
    if sslmode:
        postgres_cfg["sslmode"] = sslmode
    if schema:
        postgres_cfg["schema"] = schema
    if table_prefix:
        postgres_cfg["table_prefix"] = table_prefix
    if auto_create_schema is not None:
        postgres_cfg["auto_create_schema"] = _parse_bool(
            auto_create_schema,
            default=DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA,
        )

    postgres_cfg.setdefault("schema", DEFAULT_POSTGRES_SCHEMA)
    postgres_cfg.setdefault("table_prefix", DEFAULT_POSTGRES_TABLE_PREFIX)
    postgres_cfg.setdefault("auto_create_schema", DEFAULT_POSTGRES_AUTO_CREATE_SCHEMA)

    cfg["postgres"] = postgres_cfg
    cfg["backend"] = str(cfg.get("backend", DEFAULT_STORAGE_MODE)).lower()
    return cfg


class FileStateStore:
    """Legacy file-based store."""

    def __init__(self, config_dir: Path, servers_dir: Path):
        self.config_dir = Path(config_dir)
        self.servers_dir = Path(servers_dir)

    @property
    def mode(self) -> str:
        return "file"

    def is_healthy(self) -> bool:
        return True

    def schema_version(self) -> Optional[int]:
        return None

    def load_servers_config(self) -> Dict[str, Any]:
        config_path = self.servers_dir / "servers.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("servers", [])
                    data.setdefault("default_server", "local")
                    return data
            except Exception:
                pass
        return {"servers": [], "default_server": "local"}

    def save_servers_config(self, config: Dict[str, Any]) -> bool:
        config_path = self.servers_dir / "servers.json"
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_env_servers_config(self) -> Dict[str, Any]:
        config_path = self.config_dir / "environments.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict):
                    data.setdefault("env_servers", {})
                    return data
            except Exception:
                pass
        return {"env_servers": {}}

    def save_env_servers_config(self, config: Dict[str, Any]) -> bool:
        config_path = self.config_dir / "environments.json"
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def get_deployment_history(self) -> List[Dict[str, Any]]:
        history_path = self.config_dir / "deployment_history.json"
        if history_path.exists():
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                if isinstance(history, list):
                    return history
            except Exception:
                pass
        return []

    def append_deployment_history(self, entry: Dict[str, Any], max_entries: int = 50) -> bool:
        history = self.get_deployment_history()
        history.append(dict(entry))
        history = history[-max_entries:]
        return self.replace_deployment_history(history, max_entries=max_entries)

    def replace_deployment_history(self, entries: List[Dict[str, Any]], max_entries: int = 50) -> bool:
        history_path = self.config_dir / "deployment_history.json"
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(list(entries)[-max_entries:], f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_env_container_bindings(self) -> Dict[str, Any]:
        bindings_path = self.config_dir / "env_container_bindings.json"
        if bindings_path.exists():
            try:
                with open(bindings_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict):
                    data.setdefault("env_containers", {})
                    return data
            except Exception:
                pass
        return {"env_containers": {}}

    def save_env_container_bindings(self, config: Dict[str, Any]) -> bool:
        bindings_path = self.config_dir / "env_container_bindings.json"
        try:
            bindings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(bindings_path, "w", encoding="utf-8") as f:
                json.dump(config or {"env_containers": {}}, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False


class PostgresStateStore:
    """PostgreSQL-backed store with schema migration validation."""

    _SECRET_KEYS = {"password", "private_key", "key_passphrase", "totp_secret", "totp_code"}
    _BASE_SERVER_KEYS = {"id", "name", "hostname", "port", "username", "auth_type", "description"}

    def __init__(self, postgres_config: Dict[str, Any]):
        if psycopg is None:
            raise PostgresUnavailableError(
                "PostgreSQL backend requires psycopg. Install with: pip install 'psycopg[binary]>=3.2.0'"
            )
        self.postgres_config = dict(postgres_config or {})
        self.schema, self.auto_create_schema, self.table_names = _normalize_layout_config(
            self.postgres_config
        )
        self.schema_ident = _quote_ident(self.schema)
        self.tables = {
            role: f"{self.schema_ident}.{_quote_ident(table_name)}"
            for role, table_name in self.table_names.items()
        }
        self._migration_sql_v1 = _build_migration_sql_v1(self.schema, self.table_names)
        self._migration_checksum_v1 = hashlib.sha256(
            self._migration_sql_v1.encode("utf-8")
        ).hexdigest()
        self.dsn = build_postgres_dsn(self.postgres_config)
        self._ensure_schema()

    @property
    def mode(self) -> str:
        return "postgres"

    def _table(self, role: str) -> str:
        return self.tables[role]

    def _connect(self):
        connect_timeout = int(self.postgres_config.get("connect_timeout", 5))
        kwargs = {"connect_timeout": connect_timeout}
        if dict_row is not None:
            kwargs["row_factory"] = dict_row
        return psycopg.connect(self.dsn, **kwargs)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.schema,))
                schema_exists = bool(cur.fetchone())
                if not schema_exists:
                    if self.auto_create_schema:
                        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema_ident}")
                    else:
                        raise SchemaValidationError(
                            f"Schema '{self.schema}' does not exist and auto_create_schema=false. "
                            "Create schema manually or enable auto_create_schema."
                        )

                schema_migrations = self._table("schema_migrations")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_migrations} (
                        version INTEGER PRIMARY KEY,
                        checksum TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    f"SELECT version, checksum FROM {schema_migrations} WHERE version = 1"
                )
                row = cur.fetchone()
                if row and row["checksum"] != self._migration_checksum_v1:
                    raise SchemaValidationError(
                        "Schema migration checksum mismatch for version 1. "
                        "Refusing to continue to avoid schema drift."
                    )

                cur.execute(self._migration_sql_v1)
                cur.execute(
                    f"""
                    INSERT INTO {schema_migrations} (version, checksum)
                    VALUES (1, %s)
                    ON CONFLICT (version) DO UPDATE SET checksum = EXCLUDED.checksum
                    """,
                    (self._migration_checksum_v1,),
                )
            conn.commit()

    def schema_version(self) -> Optional[int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT MAX(version) AS version FROM {self._table('schema_migrations')}"
                )
                row = cur.fetchone()
                return int(row["version"]) if row and row["version"] is not None else None

    def is_healthy(self) -> bool:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception:
            return False

    def load_servers_config(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT setting_value
                    FROM {self._table('settings')}
                    WHERE setting_key = 'default_server'
                    """
                )
                setting = cur.fetchone()
                default_server = "local"
                if setting and isinstance(setting["setting_value"], dict):
                    default_server = setting["setting_value"].get("server_id", "local")

                cur.execute(
                    f"""
                    SELECT
                        server_id, name, hostname, port, username, auth_type, description,
                        secret_payload, metadata
                    FROM {self._table('servers')}
                    ORDER BY name ASC, server_id ASC
                    """
                )
                rows = cur.fetchall()

        servers: List[Dict[str, Any]] = []
        for row in rows:
            server = {
                "id": row["server_id"],
                "name": row["name"],
                "hostname": row["hostname"],
                "port": row["port"],
                "username": row["username"],
                "auth_type": row["auth_type"],
                "description": row["description"] or "",
            }
            if isinstance(row["secret_payload"], dict):
                server.update(row["secret_payload"])
            if isinstance(row["metadata"], dict):
                server.update(row["metadata"])
            servers.append(server)

        return {"servers": servers, "default_server": default_server}

    def save_servers_config(self, config: Dict[str, Any]) -> bool:
        servers = list((config or {}).get("servers", []))
        default_server = (config or {}).get("default_server", "local")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._table('servers')}")
                for server in servers:
                    server = dict(server or {})
                    server_id = str(server.get("id", "")).strip()
                    if not server_id:
                        continue

                    secret_payload = {
                        k: v
                        for k, v in server.items()
                        if k in self._SECRET_KEYS and v is not None
                    }
                    metadata = {
                        k: v
                        for k, v in server.items()
                        if k not in self._BASE_SERVER_KEYS and k not in self._SECRET_KEYS
                    }

                    cur.execute(
                        f"""
                        INSERT INTO {self._table('servers')} (
                            server_id, name, hostname, port, username, auth_type, description,
                            secret_payload, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        """,
                        (
                            server_id,
                            server.get("name", server_id),
                            server.get("hostname", ""),
                            int(server.get("port", 22)),
                            server.get("username", ""),
                            server.get("auth_type", "password"),
                            server.get("description", "") or "",
                            json.dumps(secret_payload),
                            json.dumps(metadata),
                        ),
                    )

                cur.execute(
                    f"""
                    INSERT INTO {self._table('settings')} (setting_key, setting_value)
                    VALUES ('default_server', %s::jsonb)
                    ON CONFLICT (setting_key)
                    DO UPDATE SET
                        setting_value = EXCLUDED.setting_value,
                        updated_at = NOW()
                    """,
                    (json.dumps({"server_id": default_server}),),
                )
            conn.commit()
        return True

    def load_env_servers_config(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT env_name, server_id
                    FROM {self._table('env_servers')}
                    ORDER BY env_name ASC
                    """
                )
                rows = cur.fetchall()
        mapping = {row["env_name"]: row["server_id"] for row in rows}
        return {"env_servers": mapping}

    def save_env_servers_config(self, config: Dict[str, Any]) -> bool:
        env_servers = dict((config or {}).get("env_servers", {}))
        allowed_envs = {"dev", "staging", "prod"}

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._table('env_servers')}")
                for env_name, server_id in env_servers.items():
                    if env_name not in allowed_envs:
                        continue
                    cur.execute(
                        f"""
                        INSERT INTO {self._table('env_servers')} (env_name, server_id)
                        VALUES (%s, %s)
                        ON CONFLICT (env_name)
                        DO UPDATE SET server_id = EXCLUDED.server_id, updated_at = NOW()
                        """,
                        (env_name, str(server_id)),
                    )
            conn.commit()
        return True

    def get_deployment_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT occurred_at, strategy, status, output, config_path, details
                    FROM {self._table('deployment_history')}
                    ORDER BY occurred_at DESC, history_id DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()

        result: List[Dict[str, Any]] = []
        for row in reversed(rows):
            payload = {
                "timestamp": row["occurred_at"].isoformat() if row["occurred_at"] else _utc_iso_now(),
                "strategy": row["strategy"],
                "status": row["status"],
                "output": row["output"],
                "config_path": row["config_path"],
            }
            details = row["details"] if isinstance(row["details"], dict) else {}
            payload.update(details)
            result.append(payload)
        return result

    def append_deployment_history(self, entry: Dict[str, Any], max_entries: int = 50) -> bool:
        record = dict(entry or {})
        details = {
            k: v
            for k, v in record.items()
            if k not in {"timestamp", "strategy", "status", "output", "config_path"}
        }

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table('deployment_history')} (
                        occurred_at, strategy, status, output, config_path, details
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        _parse_timestamp(record.get("timestamp")),
                        record.get("strategy", "rolling"),
                        record.get("status", "unknown"),
                        record.get("output"),
                        record.get("config_path"),
                        json.dumps(details),
                    ),
                )
                cur.execute(
                    f"""
                    DELETE FROM {self._table('deployment_history')}
                    WHERE history_id NOT IN (
                        SELECT history_id
                        FROM {self._table('deployment_history')}
                        ORDER BY occurred_at DESC, history_id DESC
                        LIMIT %s
                    )
                    """,
                    (int(max_entries),),
                )
            conn.commit()
        return True

    def replace_deployment_history(self, entries: List[Dict[str, Any]], max_entries: int = 50) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._table('deployment_history')}")
            conn.commit()
        for entry in list(entries)[-max_entries:]:
            self.append_deployment_history(entry, max_entries=max_entries)
        return True

    def load_env_container_bindings(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT setting_value
                    FROM {self._table('settings')}
                    WHERE setting_key = 'env_container_bindings'
                    """
                )
                row = cur.fetchone()
        if row and isinstance(row.get("setting_value"), dict):
            payload = row["setting_value"]
            payload.setdefault("env_containers", {})
            return payload
        return {"env_containers": {}}

    def save_env_container_bindings(self, config: Dict[str, Any]) -> bool:
        payload = config or {"env_containers": {}}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table('settings')} (setting_key, setting_value)
                    VALUES ('env_container_bindings', %s::jsonb)
                    ON CONFLICT (setting_key)
                    DO UPDATE SET
                        setting_value = EXCLUDED.setting_value,
                        updated_at = NOW()
                    """,
                    (json.dumps(payload),),
                )
            conn.commit()
        return True


def create_store(config_dir: Path, servers_dir: Path, resolved_cfg: Dict[str, Any]):
    backend = str((resolved_cfg or {}).get("backend", DEFAULT_STORAGE_MODE)).lower()
    if backend == "postgres":
        return PostgresStateStore((resolved_cfg or {}).get("postgres", {}))
    return FileStateStore(config_dir=config_dir, servers_dir=servers_dir)


def test_postgres_connection(postgres_config: Dict[str, Any], ensure_schema: bool = True) -> Dict[str, Any]:
    if psycopg is None:
        return {
            "success": False,
            "error": "psycopg is not installed. Install with: pip install 'psycopg[binary]>=3.2.0'",
        }

    dsn = build_postgres_dsn(postgres_config)
    try:
        connect_timeout = int((postgres_config or {}).get("connect_timeout", 5))
        kwargs = {"connect_timeout": connect_timeout}
        if dict_row is not None:
            kwargs["row_factory"] = dict_row
        with psycopg.connect(dsn, **kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db, version() AS version")
                meta = cur.fetchone()

        schema_version = None
        if ensure_schema:
            store = PostgresStateStore(postgres_config)
            schema_version = store.schema_version()
            schema_name = store.schema
            table_names = store.table_names
        else:
            schema_name, _auto_create_schema, table_names = _normalize_layout_config(postgres_config)

        return {
            "success": True,
            "database": meta["db"] if meta else None,
            "server_version": meta["version"] if meta else None,
            "schema_version": schema_version,
            "schema": schema_name,
            "tables": table_names,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
