# #11D.2B — verify_plan canary (host)

**Verdict:** `PASS_ROOT_BROKER_VERIFY_PLAN_CANARY`

Host run as `dockerpilot-extras` against the live root broker Unix socket. No Docker apply, UFW, secrets, or deploy.

Earlier agent runs were blocked on interactive `sudo -u`; the operator completed the canary manually. Final verdict is PASS.

---

## Positive

| Field | Value |
|-------|--------|
| `ok` | `true` |
| `status` | `pass` |
| `blocking_findings` | `0` |
| `dozeyguard_exit_code` | `0` |
| `dry_run` | `false` |
| `broker_verification_sha256` | `1656e706c669a634b3b1b1fe162b3bd63c5a488d9425c9b0c00d36f034ff0960` |
| `plan_sha256` | `afbf4eefef11ed3b548ad6b271d4289b9d36835785ed2beecd3d8a4d8163b8cb` |

Stages (all `ok=true`): `plan_schema`, `source_spec`, `plan_sha256`, `plan_ttl`, `spec_schema`, `spec_sha256`, `compose_recompute`, `dozeyguard_revalidate`, `firewall_recompute`

---

## Negatives

| Case | code |
|------|------|
| field change without hash recompute | `plan_hash_mismatch` |
| source_spec tamper + recompute | `spec_hash_mismatch` |
| compose model tamper + recompute | `compose_model_mismatch` |
| forged result_sha256 + recompute | `dozeyguard_result_mismatch` |
| firewall.required flip + recompute | `firewall_mismatch` |
| expired + recompute | `plan_expired` |

Plan fixture mtime unchanged by canary.

---

## Post-checks

| Check | Result |
|-------|--------|
| Client | `dockerpilot-extras` (broker peer UID) |
| socket / service | active / active; `NRestarts=0` |
| Runner | stdlib-only `plan_sha256` recompute (no Docker SDK import) |

## Runner

```bash
sudo -u dockerpilot-extras /usr/bin/python3 \
  tools/secure_deploy/broker_verify_plan_canary.py \
  --plan /path/to/plan.json \
  --output /path/to/results.json \
  --socket /run/dockerpilot-secure-broker/broker.sock
```

Defaults may point at local `/tmp` paths for convenience; they are not required.

```
SUDO_USED=true  # sudo -u dockerpilot-extras only
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
SECRETS_USED=false
DEPLOYMENT_EXECUTED=false
```
