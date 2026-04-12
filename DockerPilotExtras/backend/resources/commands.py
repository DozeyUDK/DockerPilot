"""Command execution and command-help API resources."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def create_command_resources(*, Resource, request):
    """Return command-related resource classes."""

    class ExecuteCommand(Resource):
        """Execute Docker or DockerPilot command."""

        def post(self):
            try:
                data = request.get_json()
                program = data.get("program", "docker")
                command = data.get("command", "")
                working_directory = data.get("working_directory", None)

                if not command:
                    return {"error": "Brak komendy"}, 400

                allowed_programs = ["docker", "dockerpilot"]
                if program not in allowed_programs:
                    return {"error": f"Disallowed program: {program}"}, 400

                command_parts = command.strip().split()
                if not command_parts:
                    return {"error": "Empty command"}, 400

                original_command = " ".join(command_parts)
                exec_converted_to_simple = False

                def looks_like_container_id(s):
                    return len(s) == 12 and all(c in "0123456789abcdefABCDEF" for c in s)

                if program == "dockerpilot" and len(command_parts) > 0:
                    docker_command = command_parts[0]

                    if len(command_parts) >= 2 and looks_like_container_id(docker_command):
                        container_id = docker_command
                        actual_command = command_parts[1]
                        container_commands_with_id = [
                            "logs",
                            "start",
                            "stop",
                            "restart",
                            "remove",
                            "rm",
                            "pause",
                            "unpause",
                            "exec",
                            "exec-simple",
                            "inspect",
                        ]

                        if actual_command in container_commands_with_id:
                            if actual_command == "inspect":
                                program = "docker"
                                command_parts = ["inspect", container_id] + command_parts[2:]
                            else:
                                if actual_command == "rm":
                                    actual_command = "remove"
                                elif actual_command == "exec-simple":
                                    actual_command = "exec-simple"
                                command_parts = ["container", actual_command, container_id] + command_parts[2:]
                            docker_command = command_parts[0] if len(command_parts) > 0 else ""

                    docker_to_dockerpilot = {
                        "ps": "container list",
                        "list": "container list",
                        "ls": "container list",
                        "images": "container list-images",
                        "list-img": "container list-images",
                        "list-images": "container list-images",
                        "img": "container list-images",
                        "rmi": "container remove-image",
                        "remove-image": "container remove-image",
                        "start": "container start",
                        "stop": "container stop",
                        "restart": "container restart",
                        "rm": "container remove",
                        "remove": "container remove",
                        "pause": "container pause",
                        "unpause": "container unpause",
                        "exec": "container exec",
                        "logs": "container logs",
                        "stats": "monitor stats",
                        "health": "monitor health",
                        "monitor": "monitor dashboard",
                        "deploy": "deploy config",
                        "build": "build",
                        "validate": "validate",
                        "test": "test",
                        "promote": "promote",
                        "alerts": "alerts",
                        "docs": "docs",
                        "checklist": "checklist",
                        "inspect": "docker_inspect",
                        "json": "docker_json",
                    }

                    if docker_command in docker_to_dockerpilot:
                        mapped_cmd = docker_to_dockerpilot[docker_command]
                        if mapped_cmd == "docker_inspect":
                            program = "docker"
                            command_parts = ["inspect"] + command_parts[1:]
                        elif mapped_cmd == "docker_json":
                            program = "docker"
                            if len(command_parts) > 1 and looks_like_container_id(command_parts[1]):
                                command_parts = [
                                    "inspect",
                                    command_parts[1],
                                    "--format",
                                    "{{json .}}",
                                ] + command_parts[2:]
                            else:
                                command_parts = ["ps", "--format", "json"] + command_parts[1:]
                        else:
                            mapped_command = mapped_cmd.split()
                            command_parts = mapped_command + command_parts[1:]
                    elif program == "dockerpilot" and docker_command not in [
                        "container",
                        "monitor",
                        "deploy",
                        "backup",
                        "config",
                        "pipeline",
                    ]:
                        container_commands = [
                            "list",
                            "list-images",
                            "list-img",
                            "remove-image",
                            "start",
                            "stop",
                            "restart",
                            "remove",
                            "pause",
                            "unpause",
                            "stop-remove",
                            "exec-simple",
                            "exec",
                            "logs",
                        ]
                        if docker_command in container_commands:
                            command_parts = ["container"] + command_parts

                if (
                    program == "dockerpilot"
                    and len(command_parts) >= 3
                    and command_parts[0] == "container"
                    and command_parts[1] == "exec"
                ):
                    container_name = None
                    command_to_execute = None
                    i = 2
                    while i < len(command_parts):
                        arg = command_parts[i]
                        if arg in ["--command", "-c"]:
                            if i + 1 < len(command_parts):
                                command_to_execute = command_parts[i + 1]
                                i += 2
                            else:
                                i += 1
                        elif arg == "--help" or arg == "-h":
                            break
                        elif not arg.startswith("-") and container_name is None:
                            container_name = arg
                            i += 1
                        elif (
                            not arg.startswith("-")
                            and container_name is not None
                            and command_to_execute is None
                        ):
                            command_to_execute = " ".join(command_parts[i:])
                            break
                        else:
                            i += 1

                    if container_name:
                        exec_converted_to_simple = True
                        if command_to_execute:
                            command_parts = ["container", "exec-simple", container_name, command_to_execute]
                        else:
                            command_parts = ["container", "exec-simple", container_name, "pwd"]

                env = os.environ.copy()
                cwd = None
                if working_directory:
                    try:
                        cwd_path = Path(working_directory).resolve()
                        home = Path.home()
                        if str(cwd_path).startswith(str(home)):
                            if cwd_path.exists() and cwd_path.is_dir():
                                cwd = str(cwd_path)
                            else:
                                return {
                                    "error": f"Working directory does not exist: {working_directory}"
                                }, 400
                        else:
                            return {"error": "Working directory must be in user home directory"}, 400
                    except Exception as exc:
                        return {"error": f"Błędna ścieżka katalogu roboczego: {exc}"}, 400

                try:
                    result = subprocess.run(
                        [program] + command_parts,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env=env,
                        cwd=cwd,
                    )

                    output = result.stdout
                    error_output = result.stderr

                    if program == "dockerpilot":
                        if output:
                            lines = output.split("\n")
                            filtered_lines = []
                            in_banner = False
                            banner_end_markers = ["INFO:", "usage:", "dockerpilot:", "Author:", "WARNING:", "ERROR:"]

                            for i, line in enumerate(lines):
                                if i < 20 and (
                                    "╭───────────────────── Docker Managing Tool" in line
                                    or (
                                        "Docker Managing Tool" in line
                                        and "by Dozey" not in output[max(0, i - 5) : i + 5]
                                    )
                                ):
                                    in_banner = True
                                    continue

                                if in_banner:
                                    if any(marker in line for marker in banner_end_markers):
                                        in_banner = False
                                        if (
                                            "usage:" not in line.lower()
                                            and "dockerpilot: error:" not in line.lower()
                                        ):
                                            filtered_lines.append(line)
                                        continue

                                    if (
                                        line.strip().startswith("┏")
                                        or line.strip().startswith("┡")
                                        or "🐳" in line
                                    ):
                                        in_banner = False
                                        filtered_lines.append(line)
                                        continue

                                    is_table_line = (
                                        line.strip().startswith("││")
                                        or line.strip().startswith("┃┃")
                                        or "┏┳" in line
                                        or "┡╇" in line
                                        or "└┴" in line
                                    )

                                    if not is_table_line:
                                        if ("│" in line or "╰" in line or "╭" in line or "─" in line) and i < 15:
                                            if "││" not in line and "┃┃" not in line:
                                                continue
                                        if "Docker" in line and "Pilot" in line and "by Dozey" in line:
                                            continue

                                filtered_lines.append(line)

                            output = "\n".join(filtered_lines).strip()

                        if error_output:
                            lines = error_output.split("\n")
                            filtered_error_lines = []
                            in_banner = False

                            for line in lines:
                                if (
                                    "╭───────────────────── Docker Managing Tool" in line
                                    or "Docker Managing Tool" in line
                                ):
                                    in_banner = True
                                    continue

                                if in_banner:
                                    if any(
                                        marker in line
                                        for marker in ["INFO:", "usage:", "dockerpilot:", "Author:"]
                                    ):
                                        in_banner = False
                                        if (
                                            "usage:" in line.lower()
                                            or "dockerpilot:" in line.lower()
                                            or "error:" in line.lower()
                                        ):
                                            filtered_error_lines.append(line)
                                        continue
                                    if "│" in line or "╰" in line or "╭" in line or "─" in line:
                                        continue

                                filtered_error_lines.append(line)

                            error_output = "\n".join(filtered_error_lines).strip()

                        if not output and error_output:
                            output = error_output
                            error_output = None

                    suggestions = None
                    if exec_converted_to_simple:
                        suggestions = {
                            "message": (
                                '💡 Note: Command "exec" was automatically converted to "exec-simple" '
                                "(interactive shell is not available in web CLI)."
                            ),
                            "commands": ["exec-simple <container> <command> - execute command in container"],
                        }
                    elif program == "dockerpilot" and result.returncode != 0:
                        error_text = (error_output or output or "").lower()
                        if "invalid choice" in error_text:
                            first_cmd = original_command.split()[0] if original_command.split() else ""
                            docker_aliases = {
                                "ps": "container list",
                                "list": "container list",
                                "ls": "container list",
                                "images": "container list-images",
                                "list-img": "container list-images",
                                "img": "container list-images",
                                "rmi": "container remove-image",
                                "start": "container start",
                                "stop": "container stop",
                                "restart": "container restart",
                                "rm": "container remove",
                                "remove": "container remove",
                                "pause": "container pause",
                                "unpause": "container unpause",
                                "exec": "container exec",
                                "logs": "container logs",
                                "stats": "monitor stats",
                                "health": "monitor health",
                            }

                            if first_cmd in docker_aliases:
                                suggestions = {
                                    "message": f'💡 Wskazówka: "{first_cmd}" to komenda Docker. W DockerPilot użyj:',
                                    "commands": [docker_aliases[first_cmd]],
                                }
                            else:
                                all_commands = [
                                    "container list",
                                    "container list-images",
                                    "container remove-image",
                                    "container start",
                                    "container stop",
                                    "container restart",
                                    "container remove",
                                    "container pause",
                                    "container unpause",
                                    "container stop-remove",
                                    "container exec-simple",
                                    "container exec",
                                    "container logs",
                                    "monitor dashboard",
                                    "monitor live",
                                    "monitor stats",
                                    "monitor health",
                                    "deploy config",
                                    "deploy init",
                                    "deploy history",
                                    "deploy quick",
                                    "build",
                                    "validate",
                                    "backup create",
                                    "backup restore",
                                    "config export",
                                    "config import",
                                    "pipeline create",
                                    "test",
                                    "promote",
                                    "alerts",
                                    "docs",
                                    "checklist",
                                ]

                                if "choose from" in (error_output or output or "").lower():
                                    suggestions = {
                                        "message": "Dostępne komendy DockerPilot:",
                                        "commands": all_commands,
                                    }
                                else:
                                    suggestions = {
                                        "message": "Użyj jednej z dostępnych komend:",
                                        "commands": all_commands[:10],
                                    }

                    return {
                        "success": result.returncode == 0,
                        "output": output,
                        "error": error_output if error_output else None,
                        "return_code": result.returncode,
                        "command": f'{program} {" ".join(command_parts)}',
                        "suggestions": suggestions,
                    }
                except subprocess.TimeoutExpired:
                    return {"error": "Command exceeded time limit (30s)"}, 500
                except FileNotFoundError:
                    return {"error": f"{program} not found"}, 500
                except Exception as exc:
                    return {"error": str(exc)}, 500

            except Exception as exc:
                return {"error": str(exc)}, 500

    class GetCommandHelp(Resource):
        """Get help/available commands for Docker or DockerPilot."""

        def get(self):
            try:
                program = request.args.get("program", "docker")

                if program not in ["docker", "dockerpilot"]:
                    return {"error": "Niedozwolony program"}, 400

                try:
                    result = subprocess.run(
                        [program, "--help"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 or result.stderr:
                        help_text = result.stdout + result.stderr
                        return {"success": True, "help": help_text}
                    return {"success": False, "error": "Failed to get help"}
                except FileNotFoundError:
                    return {"error": f"{program} not found"}, 500
                except Exception as exc:
                    return {"error": str(exc)}, 500

            except Exception as exc:
                return {"error": str(exc)}, 500

    class DockerPilotCommands(Resource):
        """Get all available DockerPilot commands."""

        def get(self):
            commands = {
                "container": {
                    "description": "Container operations",
                    "commands": [
                        {"name": "container list", "aliases": ["ps", "list", "ls"], "description": "List containers"},
                        {
                            "name": "container list-images",
                            "aliases": ["images", "list-img", "img"],
                            "description": "List Docker images",
                        },
                        {"name": "container remove-image", "aliases": ["rmi"], "description": "Remove Docker image(s)"},
                        {"name": "container start", "aliases": ["start"], "description": "Start container(s)"},
                        {"name": "container stop", "aliases": ["stop"], "description": "Stop container(s)"},
                        {"name": "container restart", "aliases": ["restart"], "description": "Restart container(s)"},
                        {
                            "name": "container remove",
                            "aliases": ["rm", "remove"],
                            "description": "Remove container(s)",
                        },
                        {"name": "container pause", "aliases": ["pause"], "description": "Pause container(s)"},
                        {"name": "container unpause", "aliases": ["unpause"], "description": "Unpause container(s)"},
                        {
                            "name": "container stop-remove",
                            "aliases": [],
                            "description": "Stop and remove container(s) in one operation",
                        },
                        {
                            "name": "container exec-simple",
                            "aliases": [],
                            "description": "Execute command non-interactively",
                        },
                        {
                            "name": "container exec",
                            "aliases": ["exec"],
                            "description": "Execute interactive command in container(s)",
                        },
                        {"name": "container logs", "aliases": ["logs"], "description": "View container logs"},
                    ],
                },
                "monitor": {
                    "description": "Container monitoring",
                    "commands": [
                        {"name": "monitor dashboard", "aliases": ["monitor"], "description": "Multi-container dashboard"},
                        {"name": "monitor live", "aliases": [], "description": "Live monitoring with screen clearing"},
                        {
                            "name": "monitor stats",
                            "aliases": ["stats"],
                            "description": "Get one-time container statistics",
                        },
                        {
                            "name": "monitor health",
                            "aliases": ["health"],
                            "description": "Test health check endpoint",
                        },
                    ],
                },
                "deploy": {
                    "description": "Deployment operations",
                    "commands": [
                        {"name": "deploy config", "aliases": ["deploy"], "description": "Deploy from configuration file"},
                        {
                            "name": "deploy init",
                            "aliases": [],
                            "description": "Create deployment configuration template",
                        },
                        {"name": "deploy history", "aliases": [], "description": "Show deployment history"},
                        {"name": "deploy quick", "aliases": [], "description": "Quick deployment (build + replace)"},
                    ],
                },
                "other": {
                    "description": "Other operations",
                    "commands": [
                        {"name": "build", "aliases": ["build"], "description": "Build Docker image from Dockerfile"},
                        {"name": "validate", "aliases": ["validate"], "description": "Validate system requirements"},
                        {"name": "backup create", "aliases": [], "description": "Create deployment backup"},
                        {"name": "backup restore", "aliases": [], "description": "Restore from backup"},
                        {"name": "config export", "aliases": [], "description": "Export configuration"},
                        {"name": "config import", "aliases": [], "description": "Import configuration"},
                        {"name": "pipeline create", "aliases": [], "description": "Create CI/CD pipeline"},
                        {"name": "test", "aliases": ["test"], "description": "Integration testing"},
                        {"name": "promote", "aliases": ["promote"], "description": "Environment promotion"},
                        {"name": "alerts", "aliases": ["alerts"], "description": "Setup monitoring alerts"},
                        {"name": "docs", "aliases": ["docs"], "description": "Generate documentation"},
                        {
                            "name": "checklist",
                            "aliases": ["checklist"],
                            "description": "Generate production checklist",
                        },
                    ],
                },
            }

            all_commands = []
            for _, data in commands.items():
                for cmd in data["commands"]:
                    all_commands.append(cmd["name"])

            return {
                "success": True,
                "commands": commands,
                "all_commands": all_commands,
                "docker_aliases": {
                    "ps": "container list",
                    "list": "container list",
                    "ls": "container list",
                    "images": "container list-images",
                    "list-img": "container list-images",
                    "img": "container list-images",
                    "rmi": "container remove-image",
                    "start": "container start",
                    "stop": "container stop",
                    "restart": "container restart",
                    "rm": "container remove",
                    "remove": "container remove",
                    "pause": "container pause",
                    "unpause": "container unpause",
                    "exec": "container exec",
                    "logs": "container logs",
                    "stats": "monitor stats",
                    "health": "monitor health",
                    "monitor": "monitor dashboard",
                    "deploy": "deploy config",
                    "build": "build",
                    "validate": "validate",
                    "test": "test",
                    "promote": "promote",
                    "alerts": "alerts",
                    "docs": "docs",
                    "checklist": "checklist",
                },
            }

    return ExecuteCommand, GetCommandHelp, DockerPilotCommands
