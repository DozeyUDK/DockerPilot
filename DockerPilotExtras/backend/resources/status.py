"""Status-related API resources for DockerPilot Extras."""

from __future__ import annotations

import subprocess
from typing import Any, Callable, Dict, Tuple


def create_status_resources(
    *,
    Resource,
    app,
    run_preflight_checks: Callable,
    load_env_servers_config: Callable,
    get_server_config_by_id: Callable,
    get_containers_and_images_for_server: Callable,
    build_environment_status: Callable,
    get_selected_server_config: Callable,
    build_status_context: Callable,
    run_remote_probe: Callable,
    probe_remote_binary_version: Callable,
    get_dockerpilot: Callable,
    preflight_base_dir,
):
    """Return concrete status resource classes with injected dependencies."""

    environment_status_cache = {
        "data": None,
        "timestamp": None,
        "ttl": 2,
    }

    class EnvironmentStatus(Resource):
        """Get status of all environments using DockerPilot."""

        def get(self):
            import time

            current_time = time.time()
            if (
                environment_status_cache["data"] is not None
                and environment_status_cache["timestamp"] is not None
                and current_time - environment_status_cache["timestamp"] < environment_status_cache["ttl"]
            ):
                app.logger.debug("Returning cached environment status")
                return environment_status_cache["data"]

            try:
                env_servers_cfg = load_env_servers_config()
                env_servers = env_servers_cfg.get("env_servers", {}) if isinstance(env_servers_cfg, dict) else {}
                result = build_environment_status(
                    env_servers,
                    get_server_config_by_id,
                    get_containers_and_images_for_server,
                )
                environment_status_cache["data"] = result
                environment_status_cache["timestamp"] = current_time
                return result
            except Exception as exc:
                app.logger.exception("Environment status failed")
                return {"success": False, "error": str(exc), "environments": {}}, 500

    class StatusCheck(Resource):
        """Check Docker and DockerPilot status using DockerPilot API."""

        def get(self):
            server_config = get_selected_server_config()
            context = build_status_context(server_config)
            server_label = context["server_name"]

            status = {
                "docker": {"available": False, "version": None, "error": None},
                "dockerpilot": {"available": False, "version": None, "error": None},
                "context": context,
            }

            if server_config:
                docker_output, docker_error = run_remote_probe(
                    server_config,
                    "docker --version 2>/dev/null || sudo -n docker --version 2>/dev/null || echo MISSING_DOCKER",
                )
                if docker_error:
                    status["docker"]["error"] = f"Remote Docker check failed on {server_label}: {docker_error}"
                elif not docker_output or "MISSING_DOCKER" in docker_output:
                    status["docker"]["error"] = f"Docker not available on remote server: {server_label}"
                else:
                    status["docker"] = {
                        "available": True,
                        "version": docker_output.splitlines()[0].strip(),
                        "error": None,
                    }

                dockerpilot_output, dockerpilot_error = probe_remote_binary_version(
                    server_config,
                    "dockerpilot",
                    "MISSING_DOCKERPILOT",
                    "DOCKERPILOT_AVAILABLE_NO_VERSION",
                )
                if dockerpilot_error:
                    status["dockerpilot"]["error"] = (
                        f"Remote DockerPilot check failed on {server_label}: {dockerpilot_error}"
                    )
                elif not dockerpilot_output or "MISSING_DOCKERPILOT" in dockerpilot_output:
                    status["dockerpilot"]["error"] = (
                        f"DockerPilot not installed or not in PATH on {server_label}"
                    )
                else:
                    version_value = dockerpilot_output.splitlines()[0].strip()
                    if "DOCKERPILOT_AVAILABLE_NO_VERSION" in version_value:
                        version_value = "DockerPilot available (version flag unsupported)"
                    status["dockerpilot"] = {
                        "available": True,
                        "version": version_value,
                        "error": None,
                    }
            else:
                # Local checks
                try:
                    pilot = get_dockerpilot()
                    if pilot.client:
                        try:
                            pilot.client.ping()
                            docker_version = pilot.client.version()
                            status["docker"] = {
                                "available": True,
                                "version": docker_version.get("Version", "Unknown"),
                                "error": None,
                            }
                        except Exception as exc:
                            status["docker"]["error"] = f"Docker connection failed: {str(exc)}"
                    else:
                        status["docker"]["error"] = "Docker client not initialized"
                except Exception as exc:
                    status["docker"]["error"] = f"Docker check failed: {str(exc)}"
                    try:
                        result = subprocess.run(
                            ["docker", "--version"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0:
                            status["docker"] = {
                                "available": True,
                                "version": result.stdout.strip(),
                                "error": None,
                            }
                    except Exception:
                        pass

                try:
                    pilot = get_dockerpilot()
                    if pilot.client:
                        try:
                            pilot.list_containers(show_all=False, format_output="json")
                            try:
                                from dockerpilot import __version__

                                version_str = f"DockerPilot {__version__}"
                            except ImportError:
                                version_str = "DockerPilot Enhanced"

                            status["dockerpilot"] = {
                                "available": True,
                                "version": version_str,
                                "error": None,
                            }
                        except Exception as exc:
                            status["dockerpilot"]["error"] = f"DockerPilot API test failed: {str(exc)}"
                    else:
                        status["dockerpilot"]["error"] = "DockerPilot client not initialized"
                except Exception as exc:
                    status["dockerpilot"]["error"] = f"DockerPilot check failed: {str(exc)}"
                    try:
                        result = subprocess.run(
                            ["dockerpilot", "--version"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0:
                            status["dockerpilot"] = {
                                "available": True,
                                "version": result.stdout.strip(),
                                "error": None,
                            }
                    except Exception:
                        pass

            return status

    class PreflightCheck(Resource):
        """Run setup preflight checks for DockerPilotExtras."""

        def get(self):
            result = run_preflight_checks(preflight_base_dir)
            http_status = 200 if result.get("success") else 503
            return result, http_status

    return EnvironmentStatus, StatusCheck, PreflightCheck
