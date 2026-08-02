# Root Broker Staging #11D.2A

**Mode:** staging only — no sudo, no systemd install, no apply.

## Bundle

Built by `tools/secure_deploy/build_root_broker_staging.py` into:

`.staging/11d2a/` (gitignored)

Dozeyguard source: env `DOZEYGUARD_SRC`, or sibling `../dozeyguard` relative to the DockerPilot repo.

Target install layout:

| Path | Purpose |
|------|---------|
| `/usr/libexec/dockerpilot-secure-broker/` | broker entry + Python package + schemas + VERSION/MANIFEST |
| `/usr/libexec/dockerpilot-secure-broker/bin/dozeyguard` | pinned release binary |
| `/etc/dockerpilot-secure-broker/` | config template → materialized config, policy.toml |
| `/var/lib/dockerpilot-secure-broker/` | state/approvals/runs |
| `/run/dockerpilot-secure-broker/broker.sock` | systemd socket |

Allowed ops: `ping`, `capabilities`, `verify_plan`, `dry_run`.

## Users (plan only)

- `dockerpilot-extras` — must not be in group `docker`; nologin
- `dockerpilot-secure-broker` — socket group; not for unrelated host service accounts

## Trust

Broker does not import from operator home directories, does not use user PYTHONPATH/venv,
does not load `.env`, does not trust Flask store — request carries plan; broker revalidates.

## Next

Manual review of `INSTALL_ROOT_BROKER_CANARY_WITH_SUDO.sh`, then #11D.2B with sudo.
