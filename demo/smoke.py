#!/usr/bin/env python3
"""End-to-end smoke check for the live DockerPilot demo."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import urllib.request
from pathlib import Path

EXPECTED_CONTAINERS = {
    "dockerpilot-demo-web-dev",
    "dockerpilot-demo-web-staging",
    "dockerpilot-demo-web-prod",
    "dockerpilot-demo-cache",
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def request_json(opener, url: str, *, method: str = "GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--state-dir", default=str(Path.home() / ".dockerpilot_demo"))
    parser.add_argument("--dockerpilot", default=str(Path(__file__).resolve().parents[1] / ".venv/bin/dockerpilot"))
    args = parser.parse_args()

    env = load_env(Path(args.state_dir) / "runtime.env")
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    status, login = request_json(
        opener,
        f"{args.base_url}/api/auth/login",
        method="POST",
        payload={
            "username": env["WEB_AUTH_USERNAME"],
            "password": env["WEB_AUTH_PASSWORD"],
        },
    )
    if status != 200 or not login.get("authenticated"):
        raise RuntimeError(f"demo login failed: {status} {login}")

    status_code, service_status = request_json(opener, f"{args.base_url}/api/status")
    if status_code != 200:
        raise RuntimeError(f"status endpoint failed: {status_code}")
    if not service_status.get("docker", {}).get("available"):
        raise RuntimeError(f"Docker is unavailable through Extras: {service_status}")
    if not service_status.get("dockerpilot", {}).get("available"):
        raise RuntimeError(f"DockerPilot is unavailable through Extras: {service_status}")

    cli = subprocess.run(
        [args.dockerpilot, "container", "list", "--all", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    missing = sorted(name for name in EXPECTED_CONTAINERS if name not in cli.stdout)
    if missing:
        raise RuntimeError(f"DockerPilot CLI did not report demo containers: {missing}\n{cli.stdout}")

    print("DockerPilot demo smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
