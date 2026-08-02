# #11D.2B — dry_run canary (host)

**Verdict:** `PASS_ROOT_BROKER_DRY_RUN_CANARY`

Source: operator-run canary results as `dockerpilot-extras` against the live root broker. No Docker apply, UFW, secrets, or deploy. Earlier agent steps were blocked on interactive sudo; final verdict is PASS.

---

## Positive

| Field | Value |
|-------|--------|
| `ok` | `true` |
| `status` | `pass` |
| `dry_run` | `true` |
| `blocking_findings` | `0` |
| `dozeyguard_exit_code` | `0` |
| `broker_verification_sha256` | `536063e4f237c06292e2a67481ef80b6f926ce15dde190fe4c49e4c1952a3731` |
| `plan_sha256` | `afbf4eefef11ed3b548ad6b271d4289b9d36835785ed2beecd3d8a4d8163b8cb` |

Stages (all `ok=true`): `plan_schema`, `source_spec`, `plan_sha256`, `plan_ttl`, **`approval_binding`**, `spec_schema`, `spec_sha256`, `compose_recompute`, `dozeyguard_revalidate`, `firewall_recompute`

Dry-run digest includes `approval_binding`, so SHA ≠ verify_plan digest. Firewall: stage `firewall_recompute` only; no apply.

---

## Approval behavior

| Item | Result |
|------|--------|
| Binding | pass (`approval_binding`) |
| Consumption | `validate_only_not_consumed` — broker validates only; Extras `consume` reserved for future apply |
| Replay same approval | `ok=true` / `status=pass` |
| Actor/nonce mutation | `ok=true` — broker does not bind actor/nonce to plan |

---

## Negatives

| Case | code |
|------|------|
| missing approval | `approval_required` |
| wrong plan_sha256 | `approval_hash_mismatch` |
| wrong plan_id | `approval_plan_mismatch` |
| expired | `approval_expired` |
| status consumed/pending/revoked | `approval_status` |

---

## Post-checks

| Check | Result |
|-------|--------|
| socket / service | active / active (running) |
| `NRestarts` | `0` |
| journal during canary | no traceback / integrity failures |
| plan fixture | mtime + `plan_sha256` unchanged |

## Runner

```bash
sudo -u dockerpilot-extras /usr/bin/python3 \
  tools/secure_deploy/broker_dry_run_canary.py \
  --plan /path/to/plan.json \
  --output /path/to/results.json \
  --socket /run/dockerpilot-secure-broker/broker.sock
```

```
SUDO_USED=true  # operator: sudo -u dockerpilot-extras only
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
SECRETS_USED=false
DEPLOYMENT_EXECUTED=false
```
