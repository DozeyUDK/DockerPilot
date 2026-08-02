# Secure Deploy Preview (#11C)

Preview-only control plane and UI for DockerPilot Secure Deploy.
**No apply, approve, broker, Docker Engine mutation, or firewall execution.**

Base: Secure Deploy Contract v1 (`PASS_CONTRACT_V1`, commit on `feature/secure-deploy-contract-v1`).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/secure-deploy/drafts` | Save Spec draft |
| `GET` | `/api/secure-deploy/drafts/<draft_id>` | Read draft |
| `POST` | `/api/secure-deploy/validate` | Schema-validate Spec + `spec_sha256` |
| `POST` | `/api/secure-deploy/plan` | Normalize → Dozeyguard → firewall plan → immutable plan |
| `GET` | `/api/secure-deploy/plans/<plan_id>` | Read stored plan |

Not present: `/approve`, `/apply`, `/execute`, `/deploy`, command/argv endpoints.

Envelope:

```json
{
  "schema_version": 1,
  "request_id": "req_…",
  "success": true
}
```

Errors use `{ "error": { "code": "…", "message": "…" } }` without tracebacks.

## Auth gate (fail-closed)

Secure Deploy is **stricter than legacy API**:

| Condition | Result |
|-----------|--------|
| `WEB_AUTH_ENABLED=false` | `503` `secure_deploy_auth_required` |
| MFA secret missing | `403` `secure_deploy_mfa_required` |
| No session | `401` `unauthorized` |
| Session without MFA | `403` `secure_deploy_mfa_unverified` |
| Missing/invalid CSRF on mutating methods | `403` `secure_deploy_csrf` |
| Disallowed `Origin` (when sent) | `403` `secure_deploy_csrf` |

Session cookie: `HttpOnly`; `SameSite=Lax` (SPA cross-port); `Secure` when `FLASK_ENV=production`.
CSRF token exposed as `secure_deploy_csrf` on `/api/auth/status` (not localStorage).
Legacy `/api/command/execute` and deploy routes are unchanged.

Max body: **2 MiB**.

## Draft / plan lifecycle

```
Spec → draft (optional) → validate → plan (immutable) → [STOP]
```

- Store root: `~/.dockerpilot_extras/secure_deploy/` (`SECURE_DEPLOY_STORE_ROOT` override for tests)
- `drafts/` + `plans/`; dirs `0700`; files `0600`; atomic tmp + fsync + replace
- Server-generated UUIDs; no client paths; no symlink follow; TTL ~30 min
- Plans are immutable after create (`409` on mutation)
- `approval.status=pending`; no `approved_by`; no apply fields

## Normalization

`normalize_spec_to_compose(spec) -> dict` (pure Python, no `docker compose config`).

Maps Spec v1 runtime/network/storage/health into Compose JSON.
Secrets become `x-dockerpilot-secret-refs` (logical refs only).
Rejects: shell-string command/entrypoint, `network_mode: host`, wildcard publish without policy, secret values.

## Dozeyguard adapter

Trusted config only:

- `DOZEYGUARD_BIN` — absolute executable
- `DOZEYGUARD_POLICY_PATH` — absolute policy (default: packaged `secure_deploy/policy/preview.toml`)

Fixed argv, `shell=False`, stdin Compose JSON, capture_output, timeout, `close_fds=True`, minimal env, `cwd=/`.

Validates contract_version, scanner name, input hash, exit/result consistency, `result_sha256`.

| Process exit | Preview outcome |
|--------------|-----------------|
| 0 | plan may be `ready` |
| 2 | plan stored as `invalid` (policy fail) |
| 1 / timeout / bad JSON / hash mismatch | fail-closed error |

## Firewall plan model

Structural actions only (no shell strings, no live iptables/UFW):

```json
{
  "engine": "docker-user",
  "operation": "allow",
  "protocol": "tcp",
  "host_port": 8080,
  "source": "10.241.0.0/16",
  "destination_binding": "0.0.0.0",
  "rule_id": "…",
  "comment": "…",
  "plan_sha256": null
}
```

Localhost / public-via-proxy → no ingress mutations.
LAN/ZeroTier without CIDR → validation error.

## UI flow

Route: `/secure-deploy` (nav: **Secure Deploy**).

Wizard: Identity → Image → Runtime → Network → Storage → Secrets → Preview.

Actions only: Save draft, Validate, Generate preview, Download redacted plan.

Badge: `PREVIEW ONLY — NO CHANGES WILL BE APPLIED`.

No Deploy / Apply / Approve / Execute buttons.

## Error codes (stable)

| Code | Meaning |
|------|---------|
| `secure_deploy_auth_required` | Auth disabled / gate closed |
| `secure_deploy_mfa_required` | MFA not configured |
| `secure_deploy_mfa_unverified` | Session lacking MFA |
| `secure_deploy_csrf` | CSRF/Origin failure |
| `unauthorized` | No session |
| `payload_too_large` | >2 MiB |
| `unsupported_media_type` | Non-JSON |
| `validation_failed` | Spec/plan/schema |
| `dozeyguard_*` | Scanner failures |
| `immutable_plan` | Plan rewrite attempted |
| `path_traversal` / `symlink_rejected` / `invalid_id` | Store safety |

## Limits

- Request body / stored object: 2 MiB
- Dozeyguard stdout/stderr: 2 MiB
- Dozeyguard timeout: 15s (configurable in adapter config)
- Plan TTL: 30 minutes

## Trust boundary

| Trusted | Untrusted |
|---------|-----------|
| Dozeyguard binary path | Spec/Compose from client |
| Policy path | Draft/plan IDs as paths |
| Session + MFA + CSRF | Origin outside allowlist |
| Packaged schemas | Secret values (rejected) |

DockerPilotExtras must **not** receive `docker.sock`, docker group, sudo, root, or secret materialization in #11C.

## Test matrix

- Auth disabled / missing session / MFA / CSRF / Origin
- Body size / Content-Type / unknown fields
- Store traversal / symlink / immutable plan
- Normalizer (wildcard, shell argv, allowlist CIDR)
- Dozeyguard exit 0/1/2, timeout, oversized output, sentinel, hash mismatch
- Frontend source guards (preview-only, no apply routes)
- Regression: legacy preflight/status with `WEB_AUTH_ENABLED=false`

## Preview-only constraints

- No approve / apply / broker / Docker SDK in `secure_deploy/`
- Subprocess only inside Dozeyguard adapter
- Firewall planner returns data only
- Production services untouched

## Plan #11D (next canary — not started)

- Approval workflow (human gate)
- Root Unix-socket broker (no docker.sock to Extras)
- Firewall pre-stage + rollback execution
- Secret broker materialization
- Controlled apply with locks and health gates

**STOP before #11D.**
