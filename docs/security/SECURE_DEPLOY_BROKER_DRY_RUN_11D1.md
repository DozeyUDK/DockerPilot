# Secure Deploy Broker Dry-Run #11D.1

Offline/unprivileged canary: approval state machine + broker protocol verify/dry-run.

## Contract v1.1 additive

DeploymentPlan v1 gains optional:

- `source_spec` — embedded Spec for independent revalidation
- `contract_extension: "v1.1"`

`schema_version` remains `1` (non-breaking). Broker verify/dry-run **requires** `source_spec` (`plan_not_self_contained` otherwise).

Migration: new plans from Extras embed Spec; legacy plans without it cannot pass broker verify.

## Broker verification stages

1. Plan schema  
2. `source_spec` present  
3. Recompute `plan_sha256`  
4. Plan TTL  
5. Approval binding (dry_run)  
6. Spec schema + hash  
7. Re-normalize Compose + hash/model equality  
8. Broker-owned Dozeyguard re-scan + hash/exit/blocking  
9. Recompute firewall semantic equality  
10. Emit `broker_verification_sha256`

## Canary

- Unprivileged user process
- Socket in tmp path, mode `0600`
- Cleanup on stop (tests use finalizer)
- No root install, no systemd enable, no apply

## UI

- Approve plan (step-up TOTP)
- Verify with broker
- Still **no** Apply / Deploy / Execute
