# Secure Deploy Broker Protocol v1

Transport: **Unix domain socket only** (no TCP).
Framing: **4-byte big-endian length prefix** + UTF-8 JSON body.
Limits: max frame **2 MiB**; one request → one response; timeouts required.

## Operations (#11D.1)

| Operation | Purpose |
|-----------|---------|
| `ping` | Liveness |
| `capabilities` | Lists supported ops; `apply_supported=false` |
| `verify_plan` | Independent revalidation |
| `dry_run` | verify_plan + approved approval binding (does not apply) |

Forbidden: `apply`, `deploy`, `firewall_apply`, `materialize_secrets`, `rollback`, `exec`, `command`, `shell`.

Forbidden request fields (enforced in `protocol.py`): `command`, `args`, `argv`, `path`, `working_directory`, `environment`.

Schemas:

- `schemas/secure-deploy-broker-request-v1.schema.json`
- `schemas/secure-deploy-broker-response-v1.schema.json`

## Peer auth

Linux `SO_PEERCRED` UID check (canary: expected UID). Peercred is **not** approval.

## Trust

Broker does **not** trust client-provided plan status, hashes, Dozeyguard results, or firewall plans.
It revalidates using broker-owned Dozeyguard (`BROKER_DOZEYGUARD_*`), never Flask `DOZEYGUARD_*`.
