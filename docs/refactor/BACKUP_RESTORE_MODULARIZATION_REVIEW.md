# Backup/restore modularization review handoff

## Scope

This branch modularizes `src/dockerpilot/backup_restore.py` while preserving the existing `BackupRestoreMixin` compatibility surface.

Phase 0 captured the original method contract before extraction. The final mixin still exposes the same 17 methods with the same signatures.

## Result

Baseline:

- `backup_restore.py`: 95,537 bytes
- file: 1,769 lines
- `BackupRestoreMixin`: 1,755 class lines
- methods: 17

Final:

- `backup_restore.py`: 5,822 bytes
- file: 109 lines
- `BackupRestoreMixin`: 77 class lines
- methods: 17
- no unused imports in the final facade

The mixin is now a small compatibility facade. Backup, restore, migration, archive execution, discovery and deployment-state logic live in focused modules.

## Extracted modules

### Backup discovery

`src/dockerpilot/backup_discovery.py`

- recent-backup discovery
- metadata validation
- newest-complete-backup selection

### Mount inspection

`src/dockerpilot/backup_mounts.py`

- sudo requirement detection
- mount size/capacity inspection
- privileged and large-mount metadata

### Archive/runtime helpers

`src/dockerpilot/backup_archive.py`

- Docker named-volume archive execution
- Docker bind-mount archive execution
- direct tar fallback
- sudo command execution
- cancellation/timeout handling
- orphaned temporary-container cleanup

This extraction also makes the Rich progress dependencies explicit in the module. The legacy `_backup_directory()` implementation referenced `Progress`, `SpinnerColumn`, `BarColumn`, `TextColumn`, `TimeElapsedColumn` and `TimeRemainingColumn` without importing them in `backup_restore.py`; the extracted runtime now imports them directly.

### Container backup orchestration

`src/dockerpilot/container_backup.py`

- existing-backup reuse
- backup path creation
- sudo preflight/warning flow
- mount filtering
- named-volume/bind-mount dispatch
- metadata creation
- backup summary/progress

### Container restore

`src/dockerpilot/container_restore.py`

- container-data restore orchestration
- tar archive extraction

The legacy `tar.extractall()` semantics are intentionally preserved in this refactor. Python 3.14 emits a deprecation/security warning for this path; archive extraction hardening should be handled as a separate security change.

### Container migration

`src/dockerpilot/container_migration.py`

- migration orchestration between blue/green containers
- named-volume copying
- bind-mount copying
- internal container-file copying

The existing best-effort migration policy is preserved: migration failures are logged and do not automatically block deployment where the previous implementation continued.

### Deployment-state backup/restore

`src/dockerpilot/deployment_state_backup.py`

- Docker container/image/network/volume state snapshot
- network and volume metadata restore

## Phase checkpoints

1. Phase 0 — compatibility baseline and no-regrowth gate
2. Phase 1 — backup discovery and mount inspection
3. Phase 2 — archive/runtime helpers
4. Phase 3 — container backup orchestration
5. Phase 4 — container restore
6. Phase 5 — migration/copy helpers
7. Phase 6 — deployment-state snapshot/restore
8. Phase 7 — facade cleanup and review handoff

## Validation

Final branch validation:

- backup/restore Phase 0 contract: `2 passed`
- full portable unit suite: `540 passed, 9 skipped`
- modularization gates: `30 passed`
- smoke imports: `2 passed`
- security unit gate: `11 passed, 1 skipped`

Expected warnings:

- existing `services/configuration_archive.py` `tar.extractall()` Python 3.14 warning
- extracted legacy `container_restore.py` `tar.extractall()` Python 3.14 warning

## Compatibility notes

No intentional CLI/TUI behavior change is part of this refactor. The following behavior was preserved during extraction:

- all 17 mixin method names and signatures
- backup reuse rules
- sudo/password flow
- mount filtering and external-storage skipping
- cancellation and timeout checks
- progress callbacks
- backup metadata format
- restore ordering
- best-effort migration behavior
- deployment-state snapshot format

## Review focus

Recommended review order:

1. `backup_restore.py` facade/delegation
2. Phase 0 contract fixture/test
3. `container_backup.py`
4. `backup_archive.py`
5. `container_restore.py`
6. `container_migration.py`
7. `backup_discovery.py` and `backup_mounts.py`
8. `deployment_state_backup.py`
9. focused regression tests

The main review risk is semantic drift in file-system/process orchestration rather than API drift. The Phase 0 contract guards the compatibility surface, while the focused tests cover extracted boundaries and representative behavior paths.
