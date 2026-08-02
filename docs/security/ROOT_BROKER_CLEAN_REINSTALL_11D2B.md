# #11D.2B — Clean root broker canary reinstall procedure

**Verdict:** `READY_FOR_CLEAN_ROOT_BROKER_CANARY_REINSTALL`

**Mode of this document:** analysis only. No sudo / rollback / install / systemctl was executed while drafting.

**Staging hashes (use these):**

| Item | SHA-256 |
|------|---------|
| Manifest | `94c14d1925f75cbf51cd54023fedb1044121ea6716495161eea30719e1aad2ff` |
| Install | `d74c3987462259bf482aab2f6d089f7c49e33ea0bb9cfde681668255eb064711` |
| Rollback | `b95795ae811a08f55a72a631b1ae083d8a8d5ee50215cb7b99fb97f3ca7745d0` |

---

## Analysis summary

### Proposed order — confirmed

Yes: **stop service → stop socket → rollback → verify clean (+ allowlisted leftovers) → new install**.

Rollback already stops service then socket before any unlink; explicit stops first are redundant and safe.

### Backup dir `/var/backups/dockerpilot-secure-broker-11d2a`

- Canary backup path is fixed for this bundle id (`11d2a`).
- Installer writes `install-manifest.sha256` into the backup dir and refuses mutation if a different hash is already present.
- Rollback also checks that marker against the staging manifest hash before restore/unlink.
- Staging rebuilds do **not** write here (only install).

### New rollback vs old install

| Check | Result |
|-------|--------|
| Uses same `BACKUP_DIR` | Yes |
| Stops service/socket before file ops | Yes |
| `daemon-reload` after | Yes |
| No `rm -rf` | Yes (only exact `Path.unlink` / restore) |
| Deletes user/group only if `created_*` markers | Yes |
| Allowlisted destinations only | Yes |
| Removes `/run` socket via unlink loop | **No** — not a manifest artifact; rely on socket `RemoveOnStop=true` after stop |
| Removes old `config.json` (null UID from first install) | **Gap** — new special-case only unlinks if `generated_config_json` marker exists (new installer). First install shipped `config.json` as a static artifact → likely **no** that marker → leftover after rollback |

**ABSENT_SAFE** after rollback alone: **almost**, with mandatory allowlisted cleanup of leftover `config.json` (and optional empty dirs). Documented below.

Do **not** run new install over the live old install without rollback: install would rewrite `BACKUP_DIR` “existed” snapshots to the old broken config and muddy failure recovery.

---

## PRE_ROLLBACK_CHECKS

```bash
# Hashes of the staging you are about to use
sha256sum .staging/11d2a/install-manifest.json
cat .staging/11d2a/install-manifest.sha256
# expect: 94c14d1925f75cbf51cd54023fedb1044121ea6716495161eea30719e1aad2ff

sha256sum .staging/11d2a/INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh
# expect: d74c3987462259bf482aab2f6d089f7c49e33ea0bb9cfde681668255eb064711

sha256sum .staging/11d2a/ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh
# expect: b95795ae811a08f55a72a631b1ae083d8a8d5ee50215cb7b99fb97f3ca7745d0

systemctl is-active dockerpilot-secure-broker.socket dockerpilot-secure-broker.service
systemctl is-enabled dockerpilot-secure-broker.socket dockerpilot-secure-broker.service
# expect: active/active (or active/inactive), disabled/disabled

sudo ls -la /var/backups/dockerpilot-secure-broker-11d2a
sudo test -f /var/backups/dockerpilot-secure-broker-11d2a/created_user_dockerpilot-extras && echo HAS_CREATED_USER
sudo test -f /var/backups/dockerpilot-secure-broker-11d2a/created_group_dockerpilot-secure-broker && echo HAS_CREATED_GROUP
sudo test -f /var/backups/dockerpilot-secure-broker-11d2a/generated_config_json && echo HAS_GEN_CFG || echo NO_GEN_CFG
# Expect NO_GEN_CFG for first-generation install → plan leftover config.json cleanup

sudo python3 -c 'import json;print(json.load(open("/etc/dockerpilot-secure-broker/config.json")).get("expected_peer_uid"))'
# expect: None

namei -l /run/dockerpilot-secure-broker/broker.sock
# expect: run dir root:dockerpilot-secure-broker 0750 (already hand-fixed)
```

---

## ROLLBACK_COMMAND

```bash
# 1) Explicit stop (also done inside rollback; keep this order)
sudo systemctl stop dockerpilot-secure-broker.service
sudo systemctl stop dockerpilot-secure-broker.socket

# 2) Rollback using CURRENT staging script (reads CURRENT manifest + EXISTING backup dir)
sudo .staging/11d2a/ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh

# 3) Mandatory allowlisted leftover from old static config.json artifact
#    (only if still present; exact path — no globs / no rm -rf)
sudo rm -f /etc/dockerpilot-secure-broker/config.json

# 4) Optional empty dirs only (fail if not empty — do NOT rm -rf)
sudo rmdir /var/lib/dockerpilot-secure-broker/state 2>/dev/null || true
sudo rmdir /var/lib/dockerpilot-secure-broker/approvals 2>/dev/null || true
sudo rmdir /var/lib/dockerpilot-secure-broker/runs 2>/dev/null || true
sudo rmdir /var/lib/dockerpilot-secure-broker 2>/dev/null || true
sudo rmdir /run/dockerpilot-secure-broker 2>/dev/null || true
sudo rmdir /etc/dockerpilot-secure-broker 2>/dev/null || true
sudo rmdir /usr/libexec/dockerpilot-secure-broker 2>/dev/null || true
```

---

## POST_ROLLBACK_CHECKS

```bash
systemctl is-active dockerpilot-secure-broker.socket dockerpilot-secure-broker.service || true
# expect: inactive / not-found or inactive

systemctl status dockerpilot-secure-broker.socket dockerpilot-secure-broker.service --no-pager || true

test ! -e /etc/systemd/system/dockerpilot-secure-broker.service
test ! -e /etc/systemd/system/dockerpilot-secure-broker.socket
test ! -e /usr/libexec/dockerpilot-secure-broker/broker
test ! -e /etc/dockerpilot-secure-broker/config.json
test ! -e /run/dockerpilot-secure-broker/broker.sock

getent passwd dockerpilot-extras || echo 'ABSENT user'
getent group dockerpilot-secure-broker || echo 'ABSENT group'
# If HAS_CREATED_USER/GROUP were present in backup: expect ABSENT.
# If markers were missing: users may still exist (EXISTS_EXPECTED) — install will reuse.

sudo systemctl daemon-reload   # already done by rollback; optional explicit

# ABSENT_SAFE target: no units, no broker binary, no config.json, no listening sock
```

---

## INSTALL_COMMAND

```bash
sudo .staging/11d2a/INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh
```

Expect: hash checks OK → staged systemd verify OK → files + materialized config with numeric `expected_peer_uid` → socket start → runtime dir/socket mode OK → **no** `systemctl enable`.

---

## POST_INSTALL_CHECKS

```bash
systemctl is-active dockerpilot-secure-broker.socket
# expect: active
systemctl is-active dockerpilot-secure-broker.service
# expect: inactive (until first connect) OR active if something already poked the socket

systemctl is-enabled dockerpilot-secure-broker.socket dockerpilot-secure-broker.service
# expect: disabled disabled

namei -l /run/dockerpilot-secure-broker/broker.sock
# expect: drwxr-x--- root dockerpilot-secure-broker ; socket present

sudo stat -c '%a %U:%G %n' \
  /run/dockerpilot-secure-broker \
  /run/dockerpilot-secure-broker/broker.sock \
  /usr/libexec/dockerpilot-secure-broker/broker \
  /etc/dockerpilot-secure-broker/config.json
# run dir 750 root:dockerpilot-secure-broker
# sock 660 root:dockerpilot-secure-broker
# broker/config root:root

sudo python3 -c 'import json,pwd; c=json.load(open("/etc/dockerpilot-secure-broker/config.json")); u=pwd.getpwnam("dockerpilot-extras").pw_uid; assert c["expected_peer_uid"]==u and "expected_peer_user" not in c; print("peer_uid", u)'

systemctl show dockerpilot-secure-broker.service -p CapabilityBoundingSet -p AmbientCapabilities --no-pager
# expect: both empty

id dockerpilot-extras
# must include dockerpilot-secure-broker; must NOT include docker
```

---

## PING_COMMAND

```bash
# As extras only — do not add the interactive operator account to the broker group
sudo -u dockerpilot-extras /usr/bin/python3 - <<'PY'
import json, socket, struct, uuid
SOCK = "/run/dockerpilot-secure-broker/broker.sock"

def call(op):
    req = {
        "protocol_version": 1,
        "request_id": "breq_" + uuid.uuid4().hex[:16],
        "operation": op,
        "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.2"},
    }
    raw = json.dumps(req, separators=(",", ":"), sort_keys=True).encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(SOCK)
    s.sendall(struct.pack("!I", len(raw)) + raw)
    (n,) = struct.unpack("!I", s.recv(4))
    body = b""
    while len(body) < n:
        body += s.recv(n - len(body))
    s.close()
    return req, json.loads(body.decode())

for op in ("ping", "capabilities", "totally_unknown_op_xyz"):
    req, resp = call(op)
    print("===", op, "===")
    print(json.dumps(resp, indent=2, sort_keys=True))
PY

systemctl is-active dockerpilot-secure-broker.service
# expect: active after socket activation

journalctl -u dockerpilot-secure-broker.service -n 50 --no-pager
```

Expect: `ping`/`capabilities` `ok: true`; unknown op fail-closed; no Docker/firewall/secret actions.

---

## EMERGENCY_STOP_COMMANDS

```bash
sudo systemctl stop dockerpilot-secure-broker.service
sudo systemctl stop dockerpilot-secure-broker.socket
# Do NOT enable; do not start docker; do not touch UFW/DOCKER-USER

# If install mid-flight failed after MUTATING=1, trap should have run rollback.
# Manual emergency rollback:
sudo .staging/11d2a/ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh
sudo rm -f /etc/dockerpilot-secure-broker/config.json   # only if leftover from old gen
```

---

## Confirmations (drafting session)

```
SUDO_USED=false
SYSTEMD_CHANGED=false
DOCKER_USED=false
FIREWALL_CHANGED=false
ROLLBACK_EXECUTED=false
INSTALL_EXECUTED=false
```
