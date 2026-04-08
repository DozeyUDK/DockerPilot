"""Preflight checks for DockerPilotExtras runtime dependencies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Tuple


def check_python_version(min_major: int = 3, min_minor: int = 9) -> Tuple[bool, str]:
    """Return whether Python version meets minimum requirement."""
    version = sys.version_info
    ok = (version.major, version.minor) >= (min_major, min_minor)
    return ok, f"{version.major}.{version.minor}.{version.micro}"


def check_command_version(command: List[str], timeout: int = 5) -> Tuple[bool, str]:
    """Run a version command and return status with output or error."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:  # pragma: no cover - defensive path
        return False, str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "command failed").strip()
        return False, message

    return True, (result.stdout or "ok").strip()


def check_python_imports(modules: List[str]) -> Tuple[bool, List[str]]:
    """Check that required Python modules can be imported."""
    missing: List[str] = []
    for module_name in modules:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)
    return not missing, missing


def check_frontend_dependencies(base_dir: Path) -> Tuple[bool, str]:
    """Check if frontend/node_modules exists."""
    node_modules = base_dir / "frontend" / "node_modules"
    if node_modules.exists():
        return True, "installed"
    return False, "missing node_modules (run: cd frontend && npm install)"


def _parse_node_major(version_output: str) -> int:
    """Extract Node.js major version from output."""
    raw = version_output.strip().lstrip("v")
    first = raw.split(".", 1)[0]
    return int(first)


def run_preflight_checks(base_dir: Path) -> Dict[str, Any]:
    """Run preflight checks and return a structured report."""
    checks: Dict[str, Dict[str, Any]] = {}

    py_ok, py_version = check_python_version()
    checks["python"] = {
        "required": True,
        "ok": py_ok,
        "details": py_version,
        "minimum": "3.9",
    }

    deps_ok, missing_modules = check_python_imports(["flask", "flask_cors", "flask_restful", "yaml"])
    checks["python_dependencies"] = {
        "required": True,
        "ok": deps_ok,
        "details": "ok" if deps_ok else f"missing: {', '.join(missing_modules)}",
    }

    node_ok, node_version = check_command_version(["node", "--version"])
    if node_ok:
        try:
            node_major = _parse_node_major(node_version)
            if node_major < 18:
                node_ok = False
                node_version = f"{node_version} (requires Node.js 18+)"
        except Exception:
            node_ok = False
            node_version = f"unable to parse version: {node_version}"
    checks["node"] = {
        "required": True,
        "ok": node_ok,
        "details": node_version,
    }

    npm_ok, npm_version = check_command_version(["npm", "--version"])
    checks["npm"] = {
        "required": True,
        "ok": npm_ok,
        "details": npm_version,
    }

    frontend_ok, frontend_details = check_frontend_dependencies(base_dir)
    checks["frontend_dependencies"] = {
        "required": False,
        "ok": frontend_ok,
        "details": frontend_details,
    }

    docker_ok, docker_version = check_command_version(["docker", "--version"])
    checks["docker"] = {
        "required": True,
        "ok": docker_ok,
        "details": docker_version,
    }

    dockerpilot_ok, dockerpilot_version = check_command_version(["dockerpilot", "--version"])
    checks["dockerpilot"] = {
        "required": True,
        "ok": dockerpilot_ok,
        "details": dockerpilot_version,
    }

    required_failed = [name for name, item in checks.items() if item["required"] and not item["ok"]]
    warnings = [name for name, item in checks.items() if not item["required"] and not item["ok"]]

    return {
        "success": not required_failed,
        "timestamp": datetime.now().isoformat(),
        "required_failed": required_failed,
        "warnings": warnings,
        "checks": checks,
    }
