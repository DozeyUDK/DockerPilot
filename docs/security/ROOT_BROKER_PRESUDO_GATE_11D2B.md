# #11D.2B — Root broker canary pre-sudo / reinstall gate

**Verdict:** `READY_FOR_ROOT_BROKER_CANARY_REINSTALL`

**Mode:** No sudo in this batch. Running host broker was **not** restarted or modified.

**Date:** 2026-07-31

---

## Confirmations

```
SUDO_USED=false
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
ROOT_FILES_INSTALLED=false   # this batch only rebuilt staging
SERVICE_RESTARTED=false
```

---

## CONFIG_GENERATION_MODEL

1. **Staging template** (`bundle/etc/dockerpilot-secure-broker/config.template.json`):
   - Declares `expected_peer_user=dockerpilot-extras`
   - **No** `expected_peer_uid` (never hardcoded, never a host-specific literal)
2. **Installer** (after `useradd`/`getent`):
   - Resolves UID via `pwd.getpwnam("dockerpilot-extras").pw_uid`
   - Calls `materialize_runtime_config()` → writes root-owned `/etc/dockerpilot-secure-broker/config.json` with numeric `expected_peer_uid` only
3. **Runtime broker** (`BrokerConfig`):
   - Requires integer `expected_peer_uid` (null → `config_peer_uid` fail-closed)
   - Rejects `expected_peer_user` (`config_peer_user`)
4. **Peer check** (`assert_expected_uid`):
   - `None` → `peer_uid_unconfigured` (never falls back to `os.getuid()`)

---

## Runtime directory fix

- Installer **no longer** `mkdir /run/dockerpilot-secure-broker` under `umask 077` (that produced `root:root 0700`).
- If the directory already exists with wrong owner/mode → `ensure_runtime_dir(..., fix=True)` repairs to `root:<broker_gid> 0750`.
- If absent → left for systemd `DirectoryMode=0750`.
- After `systemctl start` socket: assert run dir `0750` and socket `root:broker 0660`.

Helpers: `tools/secure_deploy/broker_install_helpers.py` (copied into staging, not a host install artifact for the Python package tree).

---

## Hashes (rebuilt staging)

| Item | SHA-256 |
|------|---------|
| **MANIFEST_SHA256** | `94c14d1925f75cbf51cd54023fedb1044121ea6716495161eea30719e1aad2ff` |
| **INSTALL_SHA256** | `d74c3987462259bf482aab2f6d089f7c49e33ea0bb9cfde681668255eb064711` |
| **ROLLBACK_SHA256** | `b95795ae811a08f55a72a631b1ae083d8a8d5ee50215cb7b99fb97f3ca7745d0` |
| Dozeyguard | `8c53252d2a4bce0d948bc52fe4e3f23d909da2014eb6f3526229b1f03d800eab` |
| Policy | `612577752ba55739f7d70a3779fbc1fe1fb9e2a62bb43670089e24629d34c6ba` |

Artifacts: **41** (includes `config.template.json`; **no** pre-baked `config.json`).

---

## TEST_COUNTS

```
87 passed / 0 failed / 0 skipped
= prior 85 + 2 new (peer_uid_fail_closed_and_match, runtime_dir_umask_077_repair)
```

| Suite | Passed |
|-------|--------|
| contract | 28 |
| preview | 20 |
| broker/approval | 8 |
| staging | 13 |
| extras regression | 18 |

Coverage added:

- `expected_peer_uid=None` → fail-closed
- matching extras UID → pass
- root UID / other UID → reject
- generator has no hardcoded UID / no pre-mkdir run dir
- umask 077 → 0700 repaired to 0750
- template → materialized numeric config

---

## Files changed (source; not committed)

- `src/dockerpilot/secure_deploy_broker/peer.py`
- `src/dockerpilot/secure_deploy_broker/config.py`
- `src/dockerpilot/secure_deploy_broker/server.py` (`build_canary_config` test helper)
- `tools/secure_deploy/broker_install_helpers.py` (**new**)
- `tools/secure_deploy/build_root_broker_staging.py`
- `tests/test_secure_deploy_root_broker_staging.py`
- `docs/security/ROOT_BROKER_PRESUDO_GATE_11D2B.md` (this report)
- `.staging/11d2a/` rebuilt (gitignored)

---

## Reinstall note (not executed here)

Host still runs the **old** config with `expected_peer_uid: null`. A deliberate reinstall/update of config + optional socket restart is required for ping-as-extras to succeed. This batch only prepared staging/scripts.

Suggested later (explicit sudo batch):

```bash
sudo .staging/11d2a/INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh
```

(or a narrower config-only update if preferred). Then re-run extras `ping` / `capabilities` / unknown-op canary.

---

## STOP

No sudo, no systemd changes, no Docker/firewall/secrets, no commit in this batch.
