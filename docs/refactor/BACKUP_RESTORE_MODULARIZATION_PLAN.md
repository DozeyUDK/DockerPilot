# Backup/restore modularization plan

## Goal

Reduce `src/dockerpilot/backup_restore.py` from a ~95 KB implementation-heavy mixin to a small compatibility facade while preserving the existing `BackupRestoreMixin` method surface and behavior.

Phase 0 baseline:

- file size: 95,537 bytes
- file lines: 1,769
- `BackupRestoreMixin`: 1,755 class lines
- methods: 17

The largest methods are `backup_container_data()` (340 lines), `_backup_directory()` (232), `_backup_volume_using_docker()` (148), `_migrate_container_data()` (146), `_backup_bind_mount_using_docker()` (129), and `_check_sudo_required_for_backup()` (124).

## Constraints

- Preserve all 17 Phase 0 method names and signatures.
- Keep `BackupRestoreMixin` as the compatibility surface used by `DockerPilotEnhanced` and deployment strategies.
- Do not change backup/restore ordering, cancellation semantics, progress callbacks, sudo behavior, or best-effort migration semantics during extraction.
- Prefer mechanical extraction plus focused regression tests over rewriting logic.
- Keep `dockerpilot-runner` outside the DockerPilot implementation/docs; it is only used externally to validate this branch.
- Security hardening that changes archive semantics is a separate follow-up. In particular, the legacy tar extraction behavior is not silently changed as part of modularization.

## Phase 0 — contract baseline

- Snapshot all 17 methods and signatures in `tests/fixtures/backup_restore_phase0_api.json`.
- Add a no-regrowth gate for `BackupRestoreMixin`.
- Capture baseline size and responsibility map.

## Phase 1 — backup discovery and mount inspection

Extract pure/leaf logic around:

- sudo requirement and mount-size inspection
- existing-backup discovery and metadata validation
- path/mount classification where practical

Target modules:

- `backup_discovery.py`
- `backup_mounts.py`

## Phase 2 — archive execution/runtime helpers

Extract low-level execution helpers:

- `_run_sudo_command`
- Docker-volume archive execution
- Docker bind-mount archive execution
- direct tar fallback
- orphaned backup-container cleanup

Target module:

- `backup_archive.py`

Add focused tests for cancellation, timeout, fallback and ownership-fix behavior. Also verify the existing console-progress path is import-safe; the current monolith references Rich progress classes inside `_backup_directory()` without module-level imports.

## Phase 3 — container backup orchestration

Extract the 340-line `backup_container_data()` workflow into an orchestration service while keeping the facade method unchanged.

Responsibilities include:

- backup reuse
- backup directory creation
- preflight/sudo warning
- mount filtering
- named-volume and bind-mount dispatch
- metadata writing and summary output

Target module:

- `container_backup.py`

## Phase 4 — restore orchestration

Extract:

- `restore_container_data`
- `_restore_from_tar`

Target module:

- `container_restore.py`

Preserve current restore behavior exactly during this refactor; archive extraction hardening is a separate security change.

## Phase 5 — migration/copy helpers

Extract:

- `_migrate_container_data`
- `_copy_volume_data`
- `_copy_bind_mount_data`
- `_copy_container_files`

Target module:

- `container_migration.py`

Keep the existing best-effort migration policy: migration errors are logged but do not automatically block deployment unless current behavior already does so.

## Phase 6 — deployment-state snapshot/restore

Extract:

- `backup_deployment_state`
- `restore_deployment_state`

Target module:

- `deployment_state_backup.py`

## Phase 7 — final facade cleanup

- Remove imports no longer used by `backup_restore.py`.
- Keep all 17 facade methods/signatures.
- Add focused regression tests for every extracted boundary.
- Produce `docs/refactor/BACKUP_RESTORE_MODULARIZATION_REVIEW.md`.
- Run the full portable unit suite, modularization gates, smoke imports and security gate before PR.

## Expected final shape

`backup_restore.py` should primarily contain delegates and compatibility hooks, ideally a few hundred lines or less. Strategy/runtime implementation should live in cohesive modules with focused tests.
