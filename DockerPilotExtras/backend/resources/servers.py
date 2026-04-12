"""Server-management API resources for DockerPilot Extras."""

from __future__ import annotations

import uuid


def create_server_resources(
    *,
    Resource,
    app,
    request,
    session,
    ssh_available: bool,
    load_servers_config,
    save_servers_config,
    test_ssh_connection,
):
    """Return concrete server resource classes with injected dependencies."""

    class ServerList(Resource):
        """List all configured servers."""

        def get(self):
            try:
                config = load_servers_config()
                servers = config.get("servers", [])
                safe_servers = []
                for server in servers:
                    safe_server = {
                        "id": server.get("id"),
                        "name": server.get("name"),
                        "hostname": server.get("hostname"),
                        "port": server.get("port", 22),
                        "username": server.get("username"),
                        "auth_type": server.get("auth_type", "password"),
                        "description": server.get("description", ""),
                    }
                    safe_servers.append(safe_server)

                return {
                    "success": True,
                    "servers": safe_servers,
                    "default_server": config.get("default_server", "local"),
                }
            except Exception as exc:
                app.logger.error(f"Failed to list servers: {exc}")
                return {"error": str(exc)}, 500

    class ServerCreate(Resource):
        """Create a new server configuration."""

        def post(self):
            if not ssh_available:
                return {"error": "SSH libraries not available. Install paramiko and cryptography."}, 503

            try:
                data = request.get_json()
                config = load_servers_config()

                name = data.get("name")
                hostname = data.get("hostname")
                username = data.get("username")
                auth_type = data.get("auth_type", "password")

                if not name or not hostname or not username:
                    return {"error": "Missing required fields: name, hostname, username"}, 400

                if auth_type == "password":
                    if not data.get("password"):
                        return {"error": "Password required for password authentication"}, 400
                elif auth_type == "key":
                    if not data.get("private_key"):
                        return {"error": "Private key required for key authentication"}, 400
                elif auth_type == "2fa":
                    if not data.get("password"):
                        return {"error": "Password required for 2FA authentication"}, 400
                else:
                    return {"error": f"Unknown authentication type: {auth_type}"}, 400

                server_id = str(uuid.uuid4())
                server_config = {
                    "id": server_id,
                    "name": name,
                    "hostname": hostname,
                    "port": data.get("port", 22),
                    "username": username,
                    "auth_type": auth_type,
                    "description": data.get("description", ""),
                }

                if auth_type == "password":
                    server_config["password"] = data.get("password")
                elif auth_type == "key":
                    server_config["private_key"] = data.get("private_key")
                    if data.get("key_passphrase"):
                        server_config["key_passphrase"] = data.get("key_passphrase")
                elif auth_type == "2fa":
                    server_config["password"] = data.get("password")
                    if data.get("totp_secret"):
                        server_config["totp_secret"] = data.get("totp_secret")

                config["servers"].append(server_config)

                if save_servers_config(config):
                    return {
                        "success": True,
                        "message": f"Server {name} created successfully",
                        "server_id": server_id,
                    }
                return {"error": "Failed to save server configuration"}, 500
            except Exception as exc:
                app.logger.error(f"Failed to create server: {exc}")
                return {"error": str(exc)}, 500

    class ServerUpdate(Resource):
        """Update an existing server configuration."""

        def put(self, server_id):
            if not ssh_available:
                return {"error": "SSH libraries not available"}, 503

            try:
                data = request.get_json()
                config = load_servers_config()

                server_index = None
                for i, server in enumerate(config["servers"]):
                    if server.get("id") == server_id:
                        server_index = i
                        break

                if server_index is None:
                    return {"error": "Server not found"}, 404

                server = config["servers"][server_index]

                if "name" in data:
                    server["name"] = data["name"]
                if "hostname" in data:
                    server["hostname"] = data["hostname"]
                if "port" in data:
                    server["port"] = data["port"]
                if "username" in data:
                    server["username"] = data["username"]
                if "description" in data:
                    server["description"] = data.get("description", "")
                if "auth_type" in data:
                    server["auth_type"] = data["auth_type"]

                auth_type = server.get("auth_type", "password")
                if auth_type == "password":
                    if "password" in data:
                        server["password"] = data["password"]
                elif auth_type == "key":
                    if "private_key" in data:
                        server["private_key"] = data["private_key"]
                    if "key_passphrase" in data:
                        server["key_passphrase"] = data.get("key_passphrase")
                elif auth_type == "2fa":
                    if "password" in data:
                        server["password"] = data["password"]
                    if "totp_secret" in data:
                        server["totp_secret"] = data.get("totp_secret")

                if save_servers_config(config):
                    return {
                        "success": True,
                        "message": f"Server {server['name']} updated successfully",
                    }
                return {"error": "Failed to save server configuration"}, 500
            except Exception as exc:
                app.logger.error(f"Failed to update server: {exc}")
                return {"error": str(exc)}, 500

    class ServerDelete(Resource):
        """Delete a server configuration."""

        def delete(self, server_id):
            try:
                config = load_servers_config()
                config["servers"] = [s for s in config["servers"] if s.get("id") != server_id]

                if config.get("default_server") == server_id:
                    config["default_server"] = "local"

                if save_servers_config(config):
                    return {"success": True, "message": "Server deleted successfully"}
                return {"error": "Failed to save server configuration"}, 500
            except Exception as exc:
                app.logger.error(f"Failed to delete server: {exc}")
                return {"error": str(exc)}, 500

    class ServerTest(Resource):
        """Test connection to a server."""

        def post(self, server_id=None):
            if not ssh_available:
                return {"error": "SSH libraries not available"}, 503

            try:
                data = request.get_json() or {}

                if server_id:
                    config = load_servers_config()
                    server_config = None
                    for server in config.get("servers", []):
                        if server.get("id") == server_id:
                            server_config = server.copy()
                            break
                    if not server_config:
                        return {"error": "Server not found"}, 404
                else:
                    server_config = {
                        "hostname": data.get("hostname"),
                        "port": data.get("port", 22),
                        "username": data.get("username"),
                        "auth_type": data.get("auth_type", "password"),
                        "password": data.get("password"),
                        "private_key": data.get("private_key"),
                        "key_passphrase": data.get("key_passphrase"),
                        "totp_code": data.get("totp_code"),
                    }

                return test_ssh_connection(server_config)
            except Exception as exc:
                app.logger.error(f"Failed to test server connection: {exc}")
                return {"error": str(exc)}, 500

    class ServerSelect(Resource):
        """Select default server for current session."""

        def post(self):
            try:
                data = request.get_json()
                server_id = data.get("server_id", "local")

                app.logger.info(f"Selecting server: {server_id}, session_id: {session.get('_id', 'no-id')}")

                session["selected_server"] = server_id
                session.permanent = True

                if data.get("set_as_default"):
                    config = load_servers_config()
                    config["default_server"] = server_id
                    save_servers_config(config)

                app.logger.info(
                    f"Server {server_id} selected, session now has: {session.get('selected_server')}"
                )

                return {
                    "success": True,
                    "message": f"Server {server_id} selected",
                    "server_id": server_id,
                }
            except Exception as exc:
                app.logger.error(f"Failed to select server: {exc}", exc_info=True)
                return {"error": str(exc)}, 500

        def get(self):
            """Get currently selected server."""
            try:
                selected = session.get("selected_server", "local")
                app.logger.debug(
                    f"Getting selected server from session: {selected}, session_id: {session.get('_id', 'no-id')}"
                )

                config = load_servers_config()
                default = config.get("default_server", "local")
                server_id = selected if selected != "local" else default

                if server_id != "local":
                    for server in config.get("servers", []):
                        if server.get("id") == server_id:
                            return {
                                "success": True,
                                "server_id": server_id,
                                "server": {
                                    "id": server.get("id"),
                                    "name": server.get("name"),
                                    "hostname": server.get("hostname"),
                                    "port": server.get("port", 22),
                                    "username": server.get("username"),
                                    "auth_type": server.get("auth_type"),
                                },
                            }

                return {"success": True, "server_id": "local", "server": None}
            except Exception as exc:
                app.logger.error(f"Failed to get selected server: {exc}", exc_info=True)
                return {"error": str(exc)}, 500

    return ServerList, ServerCreate, ServerUpdate, ServerDelete, ServerTest, ServerSelect
