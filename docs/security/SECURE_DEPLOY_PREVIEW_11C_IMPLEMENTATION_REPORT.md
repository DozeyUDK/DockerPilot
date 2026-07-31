# Secure Deploy Preview #11C — Implementation Report

**Verdict:** `PASS_PREVIEW_UI` (local; not committed; not pushed)

**Date:** 2026-07-31

## Baseline

| Item | Value |
|------|-------|
| #11B commit | `1e2ed35` `feat(security): add secure deploy contract v1` |
| #11C branch | `feature/secure-deploy-preview-ui` |
| DockerPilot version | `0.9.0-pre.2` |
| Dozeyguard | unchanged (no contract edit) |

## Delivered

### Backend (`DockerPilotExtras/backend/secure_deploy/`)

- `errors.py`, `store.py`, `normalizer.py`, `dozeyguard_adapter.py`, `firewall_planner.py`, `service.py`, `resources.py`, `policy/preview.toml`
- Routes wired in `api.py` / `app.py`
- Fail-closed Secure Deploy auth gate (independent of legacy soft auth)
- File store under `~/.dockerpilot_extras/secure_deploy/`

### Frontend

- Route `/secure-deploy`, nav item **Secure Deploy**
- Wizard + preview panel
- CSRF header via `secure_deploy_csrf` from auth status
- Preview-only actions only

### Docs

- `docs/security/SECURE_DEPLOY_PREVIEW_11C.md`
- this report

### Tests

- `tests/test_secure_deploy_preview.py` — auth, CSRF, size, store, normalizer, adapter negatives, frontend guards, HTTP draft/validate/plan
- Contract tests unchanged
- Legacy extras API loaders forced to `WEB_AUTH_ENABLED=false` to avoid module pollution from Secure Deploy auth tests

## Configuration (no secrets)

| Variable | Purpose |
|----------|---------|
| `DOZEYGUARD_BIN` | Absolute path to dozeyguard executable |
| `DOZEYGUARD_POLICY_PATH` | Absolute policy TOML (default packaged preview policy) |
| `SECURE_DEPLOY_STORE_ROOT` | Override store root (tests) |
| `WEB_AUTH_ENABLED` | Must be `true` for Secure API |
| `WEB_AUTH_TOTP_SECRET` | Required for Secure API MFA |
| `CORS_ORIGINS` | Allowed Origins for Secure POSTs |

## Confirmations

```
DOCKER_USED=false
FIREWALL_CHANGED=false
PRODUCTION_CHANGED=false
APPROVAL_IMPLEMENTED=false
APPLY_IMPLEMENTED=false
GIT_PUSHED=false
```

## Test / build counts (verification run)

| Suite | Result |
|-------|--------|
| Secure Deploy preview tests | 20 passed |
| Secure Deploy contract tests | 28 passed |
| Extras regression (preflight/status/pipeline/setup) | included in 66-pass combined run |
| Frontend Vitest | N/A (source guards in preview tests) |
| Frontend `npm run build` | PASS |
| Dozeyguard | fmt OK, clippy OK, **39** tests PASS |

## Known limitations

- Frontend has no Vitest runner; guards are source-level assertions
- Dozeyguard result hash recomputed with Python canonicalization (must match scanner contract)
- `SameSite=Lax` retained for SPA-on-:3000; CSRF required on Secure POSTs
- PostgreSQL store interface deferred; file store only
- Exposure `custom` not in Spec v1
- Fake Dozeyguard used in unit/integration tests; optional real binary not required for #11C

## Next canary

**#11D** — approval + root broker + firewall apply + secret materialization (out of scope here).

## Git

#11C changes are **not** auto-committed per batch instructions.
