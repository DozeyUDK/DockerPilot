# #11D.2B — Error envelope operation binding fix

**Verdict:** `READY_FOR_ROOT_BROKER_ERROR_ENVELOPE_REINSTALL`

**Date:** 2026-07-31

No sudo / no host broker restart in this batch.

---

## ROOT_CAUSE

In `BrokerServer._handle`, error responses used:

```python
"operation": operation if operation in SUPPORTED_OPERATIONS else "ping"
```

Rejected ops (`dance`, `apply`, …) were rewritten to `ping`, masking the real rejected operation. Initial default was also `"ping"`.

---

## Fix

- `sanitize_error_operation()` — echo short ASCII identifier (`^[a-z][a-z0-9_]{0,63}$`, ≤64); else `"unknown"`.
- Error envelope uses sanitized value (never falls back to `ping`).
- `validate_request` fail-closed if `operation` is not a string (no TypeError on dict/int).
- Unexpected exceptions → `broker_internal_error` envelope (still sanitized op).

Allowlist of **executed** ops unchanged.

---

## RESPONSE_SCHEMA_CHANGE

`secure-deploy-broker-response-v1.schema.json` (both `schemas/` and package mirror):

- `operation`: from enum of 4 supported ops → `string` with `minLength`/`maxLength`/`pattern` (widening / additive for error echoes including `unknown`).
- Success responses still only emit supported ops.
- Request schema enum **unchanged** (strict allowlist for valid requests).

---

## FILES_CHANGED

- `src/dockerpilot/secure_deploy_broker/protocol.py`
- `src/dockerpilot/secure_deploy_broker/server.py`
- `schemas/secure-deploy-broker-response-v1.schema.json`
- `src/dockerpilot/secure_deploy/schemas/secure-deploy-broker-response-v1.schema.json`
- `tests/test_secure_deploy_broker_dry_run.py`
- `.staging/11d2a/` rebuilt (gitignored)
- this report

---

## TEST_COUNTS

```
88 passed / 0 failed / 0 skipped
```

(+1 vs prior 87: `test_broker_error_envelope_preserves_rejected_operation`)

Covers: `dance`/`apply` reject with matching `operation`; missing/non-string/overlong → `unknown`; ping/capabilities OK.

---

## Hashes (rebuilt staging)

| Item | SHA-256 |
|------|---------|
| **MANIFEST_SHA256** | `26cfb55da5f096e01261a8a4896f469ae0942085775842dc2393711e0cfcc0f2` |
| **INSTALL_SHA256** | `9f2f88316f8d2571d8bad8005fe9218c4b75747ed7aaac0f013ffde82b8efff3` |
| **ROLLBACK_SHA256** | `b95795ae811a08f55a72a631b1ae083d8a8d5ee50215cb7b99fb97f3ca7745d0` |

---

```
SUDO_USED=false
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
```

Host broker still runs the previous bundle until an explicit reinstall.
