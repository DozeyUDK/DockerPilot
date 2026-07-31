# Secure Deploy Contract v1

**Status:** #11B offline contracts (no UI/API apply, no Docker/firewall)
**DockerPilot branch:** `feature/secure-deploy-contract-v1`
**Related:** Dozeyguard `docs/JSON_CONTRACT_V1.md`, architecture note under `/home/dozey/docs/dockerpilot/`

## Trust boundaries

```text
Operator / UI (later #11C)
  → unprivileged control plane validates Spec v1
  → normalize to Compose JSON (secret refs only)
  → Dozeyguard JSON contract v1 (scanner)
  → immutable DeploymentPlan v1 + plan_sha256
  → approval bound to plan_sha256 + actor + nonce + expiry
  → root broker (#11D+) applies structural ops only
```

Flask/UI must never hold raw `docker.sock`, sudo, or secret values. Dozeyguard remains a pure scanner.

## Spec v1

Schema: `schemas/secure-deployment-spec-v1.schema.json`
(also packaged at `src/dockerpilot/secure_deploy/schemas/`)

Closed (`additionalProperties: false`). Highlights:

| Area | Rule |
|------|------|
| Image | `digest` required (`sha256:…`); mutable tag only with `allow_mutable_tag` + `exception_ref` |
| Runtime | `read_only=true`, `no_new_privileges=true`, `cap_drop` contains `ALL`, `cap_add` must not include `ALL` |
| Command | argv arrays only — never shell strings |
| Exposure | `none`, `localhost`, `lan_allowlist`, `zerotier_allowlist`, `public_via_existing_proxy` (no `custom` in v1) |
| Secrets | OpenBao refs only (`provider=openbao`); no values / tokens / KV paths required in UI |
| Health | `required=true` with argv `test` |
| Forbidden | `privileged`, `network_mode`, `devices`, host binds `/` `/etc` / docker.sock |

Python API: `dockerpilot.secure_deploy.validate_secure_deployment_spec`.

## Plan v1

Schema: `schemas/deployment-plan-v1.schema.json`

Immutable planning artifact after Spec + Dozeyguard. Contains firewall action stubs, secret **names**, compose project/service, Dozeyguard digests, approval block. **No secret values.**

### Canonicalization

1. Walk values; reject non-finite floats.
2. Sort object keys lexicographically.
3. Preserve array order (argv / ports are semantic).
4. Encode UTF-8 JSON with separators `(",", ":")`, `ensure_ascii=False`, `allow_nan=False`.
5. SHA-256 → lowercase hex.

Helpers: `canonical_json_bytes`, `sha256_hex`, `sha256_canonical`.

### `plan_sha256` coverage

Hashed payload **excludes**:

- `plan_sha256` (self)
- `created_at`
- `expires_at`
- entire `approval` object

Everything else in the plan document is included (including `spec_sha256`, compose model, firewall stubs, invariants).

`compute_plan_sha256(plan)` implements this.

### Approval binding (#11B functions only)

Logical binding fields:

- `plan_sha256`
- `actor`
- `nonce`
- `expires_at`
- one-time `status` transition (`pending` → `approved` → `consumed`)

Storage / HTTP endpoints are **not** implemented in #11B.

## Dozeyguard contract

- `contract_version`: **1**
- Exit codes unchanged: `0` pass/warn, `1` error, `2` fail
- `--max-input-bytes` default 2 MiB; hard max 16 MiB
- `result.result_sha256` over canonical report without self-hash / timestamps
- **DG026** blocks `cap_add: ALL` (case-insensitive)

## Secret lifecycle

| Stage | Allowed |
|-------|---------|
| Spec / Plan / history / UI | logical refs (`name`, provider, injection) |
| Broker (later) | materialize from OpenBao to tmpfs |
| Logs | `redact_for_log()` replaces secret-like values with `[REDACTED]` |

## Legacy `deployment.yml`

Remains legacy. Mapping (manual, untrusted):

| Legacy | Spec v1 |
|--------|---------|
| `image_tag: repo:latest` | reject unless exception; require digest |
| `environment: {KEY: value}` | **do not copy**; convert to `secrets.refs` |
| `port_mapping` | `network.published_ports` + exposure profile |
| `volumes` free-form | named/bind with `approved_root_ref` |
| `privileged` | forbidden |
| strategies rolling/blue-green | recreate/rolling only in Spec v1 |

Any future auto-migration must default to untrusted, require re-validation, strip plaintext secrets, and never mint approvals.

## Breaking changes (conscious)

- Dozeyguard `--output json` now emits contract v1 envelope (not a bare `{findings:…}` list wrapper only).
- Finding field exposed as `path`; exception as `{status}` object.
- New rule **DG026** (does not alter DG001–DG025 meanings).

## Plan for #11C

- Secure Deploy page in DockerPilotExtras
- draft / validate / plan endpoints
- Dozeyguard subprocess: fixed argv, `shell=False`, timeout, fail-closed
- preview only — **no apply**
