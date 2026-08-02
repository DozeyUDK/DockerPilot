# Root Broker Staging #11D.2A — Implementation Report

**Verdict:** `READY_FOR_ROOT_BROKER_CANARY_INSTALL`

**Date:** 2026-07-31

## Baseline

| Item | Value |
|------|-------|
| #11D.1 commit | `26a5f0d` `feat(security): add broker dry-run and approval flow` |
| Branch | `feature/secure-deploy-root-broker-staging` |
| Staging path | `.staging/11d2a/` (gitignored) |

## Bundle

| Field | Value |
|-------|-------|
| Path | `.staging/11d2a/` |
| Size | **4 623 335** bytes |
| Manifest SHA-256 | `605e1d7abb591b2e5dc1234cb83c2ed9b691c5151645e77ad3c149e0832dcff6` |
| Logical manifest SHA-256 | `d07c4c570b00aae79ed3cfccdc44da7270d66389d87b96679a854d66373f6452` |
| Dozeyguard SHA-256 | `8c53252d2a4bce0d948bc52fe4e3f23d909da2014eb6f3526229b1f03d800eab` |
| Policy SHA-256 | `612577752ba55739f7d70a3779fbc1fe1fb9e2a62bb43670089e24629d34c6ba` |

Target paths:

- `/usr/libexec/dockerpilot-secure-broker/`
- `/etc/dockerpilot-secure-broker/`
- `/var/lib/dockerpilot-secure-broker/`
- `/run/dockerpilot-secure-broker/broker.sock`

Ops: ping, capabilities, verify_plan, dry_run.

## Tests

- Staging offline suite PASS (with broker/contract/preview: **67** in combined run)
- Install/rollback: `bash -n` PASS
- `systemd-analyze verify`: runs; warns missing installed binary path (expected pre-install)

## Confirmations

```
SUDO_USED=false
ROOT_FILES_INSTALLED=false
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
OPENBAO_CHANGED=false
SECRETS_MATERIALIZED=false
APPLY_IMPLEMENTED=false
GIT_PUSHED=false
```

## Residual risks

- Root broker compromise ⇒ root.
- `MemoryDenyWriteExecute` may need host-specific exception for CPython.
- `systemd-analyze verify` warns that installed broker path is absent until install.

## Next

Review install script, then #11D.2B sudo canary install (**STOP before running install in this batch**).
