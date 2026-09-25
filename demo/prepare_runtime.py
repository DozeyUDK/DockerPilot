#!/usr/bin/env python3
"""Create per-demo runtime credentials outside the repository."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

MARKER = ".dockerpilot-demo-state"
OWNER_MARKER = ".dockerpilot-demo-owner"


def create_runtime(state_dir: Path, *, codespaces: bool | None = None, rotate: bool = False) -> dict[str, str]:
    state_dir = Path(state_dir).expanduser().resolve()
    existed_before = state_dir.exists()
    state_dir.mkdir(parents=True, exist_ok=True)
    owner_marker = state_dir / OWNER_MARKER
    if not existed_before:
        owner_marker.write_text("created-by-dockerpilot-demo\n", encoding="utf-8")
    elif not owner_marker.exists():
        raise RuntimeError("Demo state directory was not created by DockerPilot demo")
    try:
        state_dir.chmod(0o700)
    except OSError:
        pass

    marker = state_dir / MARKER
    marker.write_text("DockerPilot isolated demo state\n", encoding="utf-8")

    env_path = state_dir / "runtime.env"
    if env_path.exists() and not rotate:
        return load_runtime(env_path)

    if codespaces is None:
        codespaces = os.environ.get("CODESPACES", "").lower() == "true"

    values = {
        "DOCKERPILOT_DEMO": "true",
        "DOCKERPILOT_DEMO_ALLOW_MUTATIONS": "false",
        "FLASK_ENV": "production",
        "PORT": "5000",
        "DP_STORAGE_BACKEND": "file",
        "WEB_AUTH_ENABLED": "true",
        "WEB_AUTH_USERNAME": "demo",
        "WEB_AUTH_PASSWORD": secrets.token_urlsafe(18),
        "WEB_AUTH_PASSWORD_HASH": "",
        "WEB_AUTH_TOTP_SECRET": "",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "SESSION_COOKIE_SECURE": "true" if codespaces else "false",
        "APP_SESSION_IDLE_MINUTES": "30",
        "AUTH_LOGIN_MAX_FAILURES": "1000",
        "AUTH_LOGIN_WINDOW_SECONDS": "60",
    }

    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    return values


def load_runtime(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-dir",
        default=str(Path.home() / ".dockerpilot_demo"),
        help="Demo runtime state directory (outside the repository by default)",
    )
    parser.add_argument("--rotate", action="store_true", help="Generate fresh credentials")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    values = create_runtime(state_dir, rotate=args.rotate)
    print(f"Demo runtime: {state_dir.resolve()}")
    print(f"Username: {values['WEB_AUTH_USERNAME']}")
    print(f"Password: {values['WEB_AUTH_PASSWORD']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
