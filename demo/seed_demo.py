#!/usr/bin/env python3
"""Seed isolated DockerPilotExtras state for the live demo."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ENV_CONTAINERS = {
    "dev": ["dockerpilot-demo-web-dev"],
    "staging": ["dockerpilot-demo-web-staging"],
    "prod": ["dockerpilot-demo-web-prod"],
}


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def seed_demo(home: Path, *, force_history: bool = False) -> Path:
    home = Path(home).expanduser().resolve()
    config_dir = home / ".dockerpilot_extras"
    servers_dir = config_dir / "servers"
    config_dir.mkdir(parents=True, exist_ok=True)
    servers_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        servers_dir / "servers.json",
        {"servers": [], "default_server": "local"},
    )
    _write_json(
        config_dir / "environments.json",
        {"env_servers": {"dev": "local", "staging": "local", "prod": "local"}},
    )
    _write_json(
        config_dir / "env_container_bindings.json",
        {
            "env_containers": ENV_CONTAINERS,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    history_path = config_dir / "deployment_history.json"
    if force_history or not history_path.exists():
        now = datetime.now(timezone.utc).isoformat()
        _write_json(
            history_path,
            [
                {
                    "timestamp": now,
                    "strategy": "rolling",
                    "status": "success",
                    "output": "Demo seed: DEV deployment completed",
                    "config_path": "demo/compose.yml",
                    "environment": "dev",
                },
                {
                    "timestamp": now,
                    "strategy": "blue-green",
                    "status": "success",
                    "output": "Demo seed: STAGING deployment completed",
                    "config_path": "demo/compose.yml",
                    "environment": "staging",
                },
            ],
        )

    return config_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--force-history", action="store_true")
    args = parser.parse_args()
    config_dir = seed_demo(Path(args.home), force_history=args.force_history)
    print(f"Seeded DockerPilotExtras demo state in {config_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
