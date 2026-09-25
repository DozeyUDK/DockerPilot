# DockerPilot Extras security hardening review

## Scope

This pass hardens the existing DockerPilotExtras local/session authentication and remote execution paths. It intentionally does **not** add OAuth/OIDC providers or change the existing TOTP MFA product flow; provider/enrollment work is deferred to a separate change.

## Security changes

### Privilege elevation

- Sudo passwords are no longer stored in the Flask cookie session.
- The legacy `/api/environment/sudo-password` endpoint is retained for compatibility, but it now returns a short-lived server-side elevation token instead of persisting the password in session state.
- Environment promotion no longer reads a legacy `session['sudo_password']` fallback.
- Elevation tokens are short-lived, one-time, session-bound and scope-checked.

### Server credentials at rest

- Server `password`, `private_key`, `key_passphrase` and stored server `totp_secret` fields are encrypted before persistence.
- Existing plaintext server credentials are migrated to encrypted values at backend startup.
- The Fernet master key is read from `DOCKERPILOT_EXTRAS_SECRET_KEY` or from `~/.dockerpilot_extras/.secrets.key`.
- Generated key files are created with mode `0600`.
- Server edit operations preserve an existing secret when the frontend deliberately submits an empty secret field.

### SSH host identity

- `AutoAddPolicy` was removed from the Extras backend.
- SSH connections use strict host-key verification and a managed `~/.dockerpilot_extras/known_hosts` file.
- First contact returns the observed SHA256 fingerprint instead of silently trusting the host.
- The server form exposes an explicit **Trust this fingerprint** action.
- A later host-key mismatch fails closed.
- Migration SFTP paths use the same verified SSH-client path.

### Web session hardening

- Failed login attempts are rate-limited per client IP (`AUTH_LOGIN_MAX_FAILURES`, `AUTH_LOGIN_WINDOW_SECONDS`).
- Authenticated mutating `/api/*` requests require a session-bound `X-CSRF-Token`.
- The React API client automatically attaches the token to mutating requests.
- Secure Deploy keeps its stricter CSRF/origin gate.
- Production startup refuses `admin/admin` and requires an explicitly configured Flask `SECRET_KEY` when web authentication is enabled.

### Command execution

- No Extras backend Python module uses `subprocess(..., shell=True)`.
- Local commands are executed as argv without a shell.
- Docker command arguments are parsed and re-quoted before remote execution.
- Remote file writes/deploy commands use bounded builders rather than string interpolation at call sites.
- Legacy migration paths were updated to use verified SSH/SFTP helpers and safer command construction.

## App modularization

`DockerPilotExtras/backend/app.py` was reduced from approximately 89.6 KB to 54.9 KB while preserving its module-level compatibility surface.

Extracted services include:

- `auth_guard.py`
- `secret_store.py`
- `ssh_security.py`
- `ssh_execution.py`
- `remote_commands.py`
- `elevation_tokens.py`
- `deployment_files.py`
- `health_detection.py`
- `local_postgres.py`
- `environment_state.py`
- `server_runtime.py`
- `promotion_runtime.py`
- `state_store_runtime.py`

The remaining larger functions in `app.py` are primarily the current auth/TOTP/CSRF wiring and explicit Flask resource composition. Auth-provider restructuring is intentionally deferred until the OAuth/MFA follow-up.

## Validation

Validated during the pass:

- full fast unit suite: `615 passed, 12 skipped`
- security gate: `11 passed, 1 skipped`
- smoke import gate: `2 passed`
- modularization gate: `30 passed`
- focused new service tests for secret storage, auth guard, SSH verification/execution, remote command quoting, app leaf services, environment state, server runtime, promotion runtime and state-store runtime
- static regression gate confirms no `shell=True`, no `AutoAddPolicy`, no sudo-password assignment/read from Flask session, and encrypted server-config routing

The development host does not currently provide a working Node/npm environment or a working project `.venv`, so the existing GitHub `extras-tests` and `extras-frontend` jobs remain the authoritative full-dependency/backend and frontend build checks for the PR.

## Deferred follow-up

OAuth/OIDC provider integration and any MFA enrollment/provider UX changes are intentionally deferred to a separate branch. Existing TOTP verification behavior is retained in this pass.
