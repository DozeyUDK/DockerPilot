# Root Broker Install and Rollback

Scripts (generated under `.staging/11d2a/`, **not committed**):

- `INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh`
- `ROLLBACK_ROOT_BROKER_CANARY_WITH_SUDO.sh`

## Install (manual, after review)

```bash
# From DockerPilot repo root after building staging:
less .staging/11d2a/INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh
# only after explicit approval:
# sudo .staging/11d2a/INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh
# Optional: DOCKERPILOT_INSTALL_EXPECT_USER=<non-root-operator>
```

Guarantees:

- `set -euo pipefail`, umask 077
- Operator identity: `DOCKERPILOT_INSTALL_EXPECT_USER` → `SUDO_USER` (never `root`; fail-closed)
- sha256 verify of manifest + binary + policy
- `systemd-analyze verify` before install
- `install(1)` exact paths from manifest (no `cp -r`, no globs, no `rm -rf`)
- create user/group only if absent; refuse docker group membership
- Backup dir bound to install manifest hash (refuse foreign backups)
- start **socket unit only**; do not enable on first canary
- backup existing destinations under `/var/backups/dockerpilot-secure-broker-11d2a`

## Rollback

Stops socket/service, checks backup manifest hash matches this staging, restores backups or removes installed files from manifest,
daemon-reload, deletes user/group only if created by this install and unused.

## #11D.2A status

Install script was **not** executed in the staging-only batch.
