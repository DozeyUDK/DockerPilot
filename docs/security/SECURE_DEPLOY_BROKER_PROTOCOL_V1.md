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
| `create_approval_challenge` | Revalidate plan, then create a non-authorizing broker challenge |
| `dry_run` | Revalidate plan + resolve broker-owned approval by `approval_id` (does not apply) |
| `admit_canary_execution` | Admit fixed canary from broker-owned staged bundle |
| `revoke_canary_admission` | Revoke staged/admitted fixed canary |
| `deploy_canary` | Execute fixed broker-owned canary path |
| `remove_canary` | Cleanup fixed broker-owned canary |

For `dry_run`, client-supplied `approval` JSON is forbidden. The control
client supplies only `approval_id`; the root broker loads the authoritative
v2 record from its own approval ledger.

The separate trusted approver socket is not part of the ordinary control
operation enum. It accepts one closed operation, `approve_challenge`, and
derives approver identity from `SO_PEERCRED`.

Forbidden: `apply`, `deploy`, `firewall_apply`, `materialize_secrets`, `rollback`, `exec`, `command`, `shell`.

Forbidden request fields (enforced in `protocol.py`): `command`, `args`, `argv`, `path`, `working_directory`, `environment`.

Schemas:

- `schemas/secure-deploy-broker-request-v1.schema.json`
- `schemas/secure-deploy-broker-response-v1.schema.json`

## Peer auth

The control socket authenticates the expected Extras UID with Linux
`SO_PEERCRED`; that identity is **not** approval.

The separate approver socket is root-owned mode `0600`. Its service derives
the peer UID from `SO_PEERCRED` and checks it against a root-owned
`allowed_approver_uids` allowlist. Request JSON cannot set or override the
authoritative UID.

## Trust

Broker does **not** trust client-provided plan status, hashes, Dozeyguard results, or firewall plans.
It revalidates using broker-owned Dozeyguard (`BROKER_DOZEYGUARD_*`), never Flask `DOZEYGUARD_*`.
