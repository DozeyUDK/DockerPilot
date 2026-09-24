# ContainerManager modularization review handoff

## Scope

This branch modularizes `src/dockerpilot/container_manager.py` while preserving the existing `ContainerManager` compatibility surface.

Phase 0 captured the original class method contract before extraction. The final class still exposes all 20 methods with the same signatures and defaults.

The module-level `_container_image_label` helper also remains importable for compatibility; it is now backed by `container_listing.container_image_label`.

## Result

Baseline:

- `container_manager.py`: 30,063 bytes
- file: 625 lines
- `ContainerManager`: 593 class lines
- methods: 20

Final:

- `container_manager.py`: 6,429 bytes
- file: 129 lines
- `ContainerManager`: 94 class lines
- methods: 20
- no unused imports in the final facade

The manager is now primarily an initialization and compatibility facade. Listing/presentation, creation, lifecycle, inspection and command execution are isolated in focused modules.

## Extracted modules

### Container listing

`src/dockerpilot/container_listing.py`

- image label resolution without image inspect
- JSON listing shape
- responsive Rich table construction
- port/size/uptime presentation
- container summary output

### Container creation

`src/dockerpilot/container_creation.py`

- volume normalization
- Docker run argument construction
- restart/network/privileged settings
- CPU and memory limit conversion
- container creation error handling

### Container lifecycle

`src/dockerpilot/container_lifecycle.py`

- unified operation dispatch/progress
- restart-policy updates
- start/stop/restart/remove
- pause/unpause/rename
- expected-status waiting
- combined stop-and-remove flow

The original error-handler context semantics and running-container removal confirmation are preserved.

### Container inspection

`src/dockerpilot/container_inspection.py`

- single and comma-separated multi-container log viewing
- interactive log selection
- JSON attribute rendering

### Container command execution

`src/dockerpilot/container_exec.py`

- interactive `docker exec -it` execution
- non-interactive Docker SDK `exec_run()` execution
- running-state and exit-code handling

The interactive subprocess path has a regression test that executes through a monkeypatched `subprocess.run`, specifically guarding extraction-time import regressions.

## Phase checkpoints

1. Phase 0 — compatibility baseline and no-regrowth gate
2. Phase 1 — listing/presentation extraction
3. Phase 2 — creation/configuration extraction
4. Phase 3 — lifecycle extraction
5. Phase 4 — inspection extraction
6. Phase 5 — command execution extraction
7. Phase 6 — facade import cleanup and review handoff

## Validation

Final branch validation:

- ContainerManager Phase 0 contract: `2 passed`
- full portable unit suite: `561 passed, 9 skipped`
- modularization gates: `30 passed`
- smoke imports: `2 passed`
- security unit gate: `11 passed, 1 skipped`

Existing Python 3.14 `tar.extractall()` warnings from backup/configuration archive code remain outside this refactor.

## Compatibility notes

No intentional CLI/TUI behavior change is part of this refactor. The following behavior is preserved:

- all 20 class method names, signatures and defaults
- module-level `_container_image_label` import compatibility
- listing JSON shape and responsive table output
- operation dispatch and success/failure wording
- restart policy payloads
- volume normalization
- run-container Docker kwargs
- lifecycle error-handler contexts
- multi-container log parsing
- JSON inspection output
- interactive Docker CLI exec semantics
- non-interactive SDK exec semantics

## Review focus

Recommended review order:

1. `container_manager.py` facade/delegation
2. Phase 0 contract fixture/test
3. `container_lifecycle.py`
4. `container_creation.py`
5. `container_listing.py`
6. `container_inspection.py`
7. `container_exec.py`
8. focused regression tests

The main review risk is semantic drift in Docker argument construction and lifecycle dispatch rather than API drift. Phase 0 protects the class surface, while focused tests exercise representative runtime paths including Docker kwargs, lifecycle calls, log parsing and subprocess execution.
