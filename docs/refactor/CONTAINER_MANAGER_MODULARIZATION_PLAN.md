# ContainerManager modularization plan

## Baseline

`src/dockerpilot/container_manager.py` currently contains:

- 30,063 bytes
- 625 file lines
- 593 `ContainerManager` class lines
- 20 methods

Largest methods at Phase 0:

- `run_new_container()` — 119 lines
- `list_containers()` — 112 lines
- `container_operation()` — 55 lines
- `view_container_logs()` — 46 lines
- `_normalize_volumes()` — 41 lines
- `exec_container()` — 39 lines

The objective is to keep `ContainerManager` as a compatibility facade while moving focused behavior into leaf modules. Existing method names, defaults and signatures stay stable unless an API change is explicitly made outside this refactor.

## Phase 0 — compatibility contract

Capture the exact 20-method AST signature surface and class-size baseline.

Gate:

- `tests/fixtures/container_manager_phase0_api.json`
- `tests/test_container_manager_phase0_contract.py`

## Phase 1 — listing and presentation

Extract container listing and presentation concerns:

- image label resolution
- JSON listing data
- responsive Rich table output
- summary output

Target module: `container_listing.py`.

## Phase 2 — container creation/configuration

Extract:

- volume normalization
- resource-limit conversion
- container run kwargs construction / run flow

Target module: `container_creation.py`.

Keep `run_new_container()` and `_normalize_volumes()` as facade delegates.

## Phase 3 — lifecycle operations

Extract:

- unified operation dispatch/progress
- start/stop/restart/remove/pause/unpause/rename
- restart-policy updates
- status waiting
- stop-and-remove flow

Target module: `container_lifecycle.py`.

Preserve the current error-handler context semantics and confirmation behavior.

## Phase 4 — inspection

Extract:

- log viewing
- JSON inspection

Target module: `container_inspection.py`.

Preserve multi-container log parsing and interactive selection behavior.

## Phase 5 — command execution

Extract:

- interactive Docker CLI exec
- non-interactive SDK exec

Target module: `container_exec.py`.

Preserve current running-state checks, exit-code handling and console output.

## Phase 6 — facade cleanup

Remove imports no longer owned by `container_manager.py`, audit facade size and add review handoff.

Target outcome:

- 20/20 facade methods preserved
- class primarily initialization + delegates
- no unused imports
- representative regression tests for every extracted boundary

## Validation strategy

At each phase:

1. Phase 0 AST contract must stay green.
2. Focused regression tests for the extracted module must pass.
3. Full portable unit suite must stay green before committing a major extraction.

Final gates:

- Phase 0 contract
- full portable unit suite
- modularization gates
- smoke imports
- security unit gate

No runner implementation details belong in DockerPilot documentation; the validation list records only project test results.
