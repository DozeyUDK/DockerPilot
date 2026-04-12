"""Authentication and elevation-token API resources."""

from __future__ import annotations


def create_auth_resources(
    *,
    Resource,
    app,
    request,
    session,
    web_auth_enabled: bool,
    web_auth_username: str,
    web_auth_totp_secret: str,
    web_auth_totp_window: int,
    auth_status_payload,
    verify_password,
    verify_totp_code,
    clear_auth_session,
    get_dockerpilot,
    issue_elevation_token,
    revoke_elevation_tokens_for_current_session,
    now_ts,
    datetime_cls,
):
    """Return auth/elevation resource classes with injected dependencies."""

    class AuthStatus(Resource):
        """Return current authentication state for DockerPilotExtras web panel."""

        def get(self):
            try:
                return auth_status_payload()
            except Exception as exc:
                app.logger.error(f"Auth status failed: {exc}")
                return {"success": False, "error": str(exc)}, 500

    class AuthLogin(Resource):
        """Authenticate user with username/password and optional TOTP MFA."""

        def post(self):
            try:
                if not web_auth_enabled:
                    return {
                        "success": True,
                        "message": "Authentication disabled",
                        "auth_enabled": False,
                    }

                data = request.get_json() or {}
                username = str(data.get("username") or "").strip()
                password = str(data.get("password") or "")
                totp_code = str(data.get("totp_code") or "").strip()

                if not username or not password:
                    return {"success": False, "error": "username and password are required"}, 400
                if username != web_auth_username:
                    return {"success": False, "error": "Invalid credentials"}, 401
                if not verify_password(password):
                    return {"success": False, "error": "Invalid credentials"}, 401
                if web_auth_totp_secret and not verify_totp_code(
                    web_auth_totp_secret,
                    totp_code,
                    window=web_auth_totp_window,
                ):
                    return {"success": False, "error": "Invalid MFA code"}, 401

                session["auth_authenticated"] = True
                session["auth_username"] = username
                session["auth_mfa_verified"] = bool(web_auth_totp_secret)
                session["auth_last_activity_ts"] = now_ts()
                session.permanent = True

                app.logger.info(f"Authenticated web session for user '{username}'")
                return auth_status_payload()
            except Exception as exc:
                app.logger.error(f"Auth login failed: {exc}")
                return {"success": False, "error": str(exc)}, 500

    class AuthLogout(Resource):
        """Terminate authenticated web session."""

        def post(self):
            try:
                clear_auth_session()
                return {
                    "success": True,
                    "message": "Logged out",
                    "auth_enabled": web_auth_enabled,
                    "authenticated": False,
                }
            except Exception as exc:
                app.logger.error(f"Auth logout failed: {exc}")
                return {"success": False, "error": str(exc)}, 500

    class CheckSudoRequired(Resource):
        """Check if backup will require sudo password."""

        def post(self):
            try:
                data = request.get_json()
                container_name = data.get("container_name")

                if not container_name:
                    return {"error": "container_name is required"}, 400

                pilot = get_dockerpilot()
                requires_sudo, privileged_paths, mount_info = pilot._check_sudo_required_for_backup(
                    container_name
                )

                large_mounts = mount_info.get("large_mounts", [])
                total_size_tb = mount_info.get("total_size_tb", 0)
                has_large_mounts = len(large_mounts) > 0

                total_capacity_tb = sum(m.get("total_capacity_tb", 0) for m in large_mounts)
                if total_capacity_tb == 0:
                    total_capacity_tb = total_size_tb

                return {
                    "requires_sudo": requires_sudo,
                    "privileged_paths": privileged_paths[:5],
                    "total_privileged_paths": len(privileged_paths),
                    "has_large_mounts": has_large_mounts,
                    "large_mounts": large_mounts[:3],
                    "total_size_tb": round(total_size_tb, 2),
                    "total_size_gb": round(mount_info.get("total_size_gb", 0), 2),
                    "total_capacity_tb": round(total_capacity_tb, 2),
                    "message": "Backup will require sudo password" if requires_sudo else "No sudo required",
                    "warning": (
                        "⚠️ Wykryto duże dyski "
                        f"(użyte: {total_size_tb:.2f} TB, pojemność: {total_capacity_tb:.2f} TB). "
                        "Backup może trwać bardzo długo!"
                        if has_large_mounts
                        else None
                    ),
                }
            except Exception as exc:
                app.logger.error(f"Check sudo failed: {exc}")
                return {"error": str(exc)}, 500

    class ElevationToken(Resource):
        """Issue/revoke short-lived one-time elevation tokens."""

        def post(self):
            try:
                data = request.get_json() or {}
                sudo_password = str(data.get("sudo_password") or "")
                if not sudo_password.strip():
                    return {"error": "sudo_password is required"}, 400

                scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
                ttl_seconds = data.get("ttl_seconds")
                if ttl_seconds is not None:
                    try:
                        ttl_seconds = int(ttl_seconds)
                    except (TypeError, ValueError):
                        return {"error": "ttl_seconds must be an integer"}, 400

                issued = issue_elevation_token(
                    sudo_password=sudo_password,
                    scope=scope,
                    ttl_seconds=ttl_seconds,
                )
                app.logger.info(
                    f"Issued elevation token (action={scope.get('action')}, expires_in={issued['expires_in']}s)"
                )
                return {"success": True, **issued}
            except Exception as exc:
                app.logger.error(f"Failed to issue elevation token: {exc}")
                return {"error": str(exc)}, 500

        def delete(self):
            try:
                revoked = revoke_elevation_tokens_for_current_session()
                session.pop("sudo_password", None)
                session.pop("sudo_password_timestamp", None)
                app.logger.info(f"Revoked {revoked} elevation token(s) for current session")
                return {"success": True, "revoked": revoked}
            except Exception as exc:
                app.logger.error(f"Failed to revoke elevation tokens: {exc}")
                return {"error": str(exc)}, 500

    class SudoPassword(Resource):
        """Store sudo password in session for backup operations."""

        def post(self):
            try:
                data = request.get_json()
                sudo_password = data.get("sudo_password")

                if not sudo_password:
                    return {"error": "sudo_password is required"}, 400

                session["sudo_password"] = sudo_password
                session["sudo_password_timestamp"] = datetime_cls.now().isoformat()
                session.permanent = True

                app.logger.info("Sudo password stored in session (not logged)")

                response = {
                    "success": True,
                    "message": "Sudo password stored securely",
                }
                try:
                    issued = issue_elevation_token(
                        sudo_password=sudo_password,
                        scope={"action": "legacy.sudo_password"},
                    )
                    response.update(
                        {
                            "elevation_token": issued.get("token"),
                            "elevation_expires_in": issued.get("expires_in"),
                            "deprecated": True,
                        }
                    )
                except Exception:
                    pass

                return response
            except Exception as exc:
                app.logger.error(f"Store sudo password failed: {exc}")
                return {"error": str(exc)}, 500

        def delete(self):
            """Clear sudo password from session."""
            try:
                revoke_elevation_tokens_for_current_session()
                session.pop("sudo_password", None)
                session.pop("sudo_password_timestamp", None)
                return {"success": True, "message": "Sudo password cleared"}
            except Exception as exc:
                return {"error": str(exc)}, 500

    return AuthStatus, AuthLogin, AuthLogout, CheckSudoRequired, ElevationToken, SudoPassword
