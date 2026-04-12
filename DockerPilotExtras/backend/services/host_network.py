"""Host-network related helpers for migration/config extraction."""

from __future__ import annotations

import re
from typing import Dict


def extract_port_from_string(value: str):
    """Extract valid TCP/UDP port number from text value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    candidates = re.findall(r"(?<!\\d)(\\d{2,5})(?!\\d)", text)
    for token in reversed(candidates):
        try:
            port = int(token)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return str(port)
    return None


def infer_port_mapping_for_host_network(attrs: dict, image_tag: str = "") -> Dict[str, str]:
    """Infer minimal port mapping for host-network containers.

    Priority:
    1) explicit env-based app ports
    2) conservative image defaults
    """
    env_ports: Dict[str, str] = {}
    env_candidates = [
        "GF_SERVER_HTTP_PORT",
        "PORT",
        "SERVER_PORT",
        "HTTP_PORT",
        "HTTPS_PORT",
        "SERVICE_PORT",
        "APP_PORT",
        "WEB_PORT",
        "UI_PORT",
        "API_PORT",
        "PGPORT",
        "POSTGRES_PORT",
        "MYSQL_PORT",
        "MARIADB_PORT",
        "REDIS_PORT",
        "MONGO_PORT",
        "MONGODB_PORT",
        "ELASTICSEARCH_PORT",
        "KAFKA_PORT",
        "DBPORT",
        "TSPORT",
        "INFLUXDB_HTTP_BIND_ADDRESS",
    ]

    env_list = (attrs.get("Config", {}) or {}).get("Env", []) or []
    env_map = {}
    for env_var in env_list:
        if isinstance(env_var, str) and "=" in env_var:
            key, value = env_var.split("=", 1)
            env_map[key] = value

    for key in env_candidates:
        if key not in env_map:
            continue
        parsed = extract_port_from_string(env_map.get(key))
        if parsed:
            env_ports[parsed] = parsed

    if env_ports:
        return env_ports

    image_lower = (image_tag or "").lower()
    defaults = [
        ("grafana", "3000"),
        ("influxdb", "8086"),
        ("prometheus", "9090"),
        ("qdrant", "6333"),
        ("homeassistant", "8123"),
        ("postgres", "5432"),
        ("mariadb", "3306"),
        ("mysql", "3306"),
        ("redis", "6379"),
        ("mongo", "27017"),
        ("elasticsearch", "9200"),
        ("kibana", "5601"),
    ]
    for pattern, port in defaults:
        if pattern in image_lower:
            return {port: port}

    return {}
