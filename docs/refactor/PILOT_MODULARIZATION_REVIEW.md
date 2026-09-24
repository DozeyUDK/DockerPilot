# DockerPilot `pilot.py` modularization — review handoff

Status: **implementation complete on review branch; not merged**

Branch: `refactor/pilot-modularization-all-phases`

This handoff summarizes the implementation that followed `PILOT_MODULARIZATION_PHASE0.md`. The refactor intentionally preserves `DockerPilotEnhanced` as the compatibility facade while moving implementation into existing domain managers and small focused modules.

## Before / after

Phase 0 baseline:

- `src/dockerpilot/pilot.py`: **78,813 bytes**
- `DockerPilotEnhanced`: lines **38–1749** (**1,712 class lines**)
- methods defined directly on the facade: **66**

Final branch state after all phases:

- `src/dockerpilot/pilot.py`: **27,350 bytes**
- file length: **581 lines**
- `DockerPilotEnhanced`: lines **62–555** (**494 class lines**)
- methods defined directly on the facade: **66**

The direct method count deliberately stays at 66. Those method names are the compatibility surface used by CLI/TUI/MCP, managers, mixins and possible external callers. The reduction comes from turning those methods into delegates rather than deleting or renaming them.

Relative to the Phase 0 baseline, the facade class shrank by roughly **71% in class lines** and `pilot.py` shrank by roughly **65% in bytes**.

## Phase 1 — leaf feature services

Independent feature implementations were moved out first, with their existing `DockerPilotEnhanced` methods retained as delegates.

New modules:

- `services/templates.py`
  - production checklist generation
  - documentation generation
- `services/pipeline.py`
  - Git integration
  - GitHub Actions / GitLab CI / Jenkins generation
- `services/configuration_archive.py`
  - configuration export/import
- `services/system_validation.py`
  - Python/Docker/module/disk/permission validation
- `services/integration_testing.py`
  - integration-test dispatch
  - HTTP/custom/database placeholder tests
  - report rendering and persistence
- `services/alerts.py`
  - alert configuration/state
  - condition evaluation
  - notification delivery

Compatibility details:

- public facade method names and return behavior are retained;
- pipeline private provider helpers (`_create_github_actions_config`, `_create_gitlab_ci_config`, `_create_jenkins_config`) remain available as delegates;
- integration-test callbacks route through the facade private methods so monkeypatching/subclass overrides still work;
- alert initialization, triggering and notification routing likewise preserve the old facade callback chain.

## Phase 2 — interactive CLI extraction

The ~182-line `_run_container_interactive` implementation was moved to:

- `cli/interactive.py` -> `run_container_interactive(pilot, args)`

`DockerPilotEnhanced._run_container_interactive` remains as a small compatibility delegate. `cli/__init__.py` exports the extracted flow.

No parser commands or CLI flags were intentionally changed.

## Phase 3 — direct Docker/runtime operations moved to their domains

Container operations moved into `ContainerManager`:

- `exec_container`
- `stop_and_remove_container`
- `exec_command_non_interactive`

Monitoring operations moved into `MonitoringManager`:

- `get_container_stats_once`
- `monitor_container_live`

`MonitoringManager` now accepts the shared error handler as an optional dependency; existing direct construction remains compatible because it has a `nullcontext` fallback.

Other domain moves:

- standalone HTTP health check -> `services/health_checks.py`
- deployment-history presentation -> existing `deployment_history.py`

The facade methods still exist and delegate to the new owners.

## Phase 4 — shared runtime/deployment support boundary

The helpers consumed by `DeploymentServiceMixin` and `BackupRestoreMixin` were not deleted. Their implementation was extracted to:

- `services/runtime_support.py`

It now owns:

- cancel-flag lookup/consumption
- health-check defaults loading
- database image matching/name lookup
- progress callback isolation
- loading animation/context
- shared Docker/network error context
- multi-target parsing

The old facade helper methods remain as compatibility delegates, so the two large mixins do not need a simultaneous rewrite.

## Phase 5 — bootstrap / composition-root cleanup

Process/bootstrap implementation moved to:

- `services/bootstrap.py`

It owns:

- Windows console stream configuration
- banner rendering
- logger construction
- YAML config loading
- Docker client initialization using the active Docker CLI context when available

`DockerPilotEnhanced.__init__` remains the composition root and still wires:

- console/logger
- Docker client
- `ContainerManager`
- `ImageManager`
- `MonitoringManager`
- `AlertService`
- signal handlers
- compatibility state required by deployment/backup mixins

This keeps object construction visible in one place while removing the implementation details from `pilot.py`.

## Compatibility guardrails retained

The branch intentionally keeps the following characteristics:

1. No intended CLI command/flag rename as part of this refactor.
2. Existing `DockerPilotEnhanced` public methods remain present.
3. Private methods that were observable extension/monkeypatch points remain present where practical.
4. `DeploymentServiceMixin` and `BackupRestoreMixin` continue calling the facade helper names they used before.
5. Managers receive explicit dependencies rather than the entire pilot object.
6. `deployment_service.py` and `backup_restore.py` were not rewritten as part of this branch.

## Tests added during the refactor

Permanent focused regression and merge-gate tests include:

- `test_template_service.py`
- `test_pipeline_service.py`
- `test_configuration_archive_service.py`
- `test_refactored_services.py`
- `test_refactored_service_regressions.py`
- `test_phase3_extractions.py`
- `test_bootstrap_service.py`
- `test_runtime_support.py`
- `test_pilot_facade_contract.py`
- `test_pilot_phase0_api_contract.py`
- `test_pilot_modularization_performance.py`
- `test_service_callbacks.py`

The Phase 0 facade signature snapshot is stored in `tests/fixtures/pilot_phase0_api.json`. The contract test compares all 66 facade methods, including defaults, keyword/variadic shape and return annotations, and also prevents `DockerPilotEnhanced` from growing back beyond the agreed line budget.

## Pre-merge regression and performance gate — 2026-09-23

The connector pytest environment is not the project virtualenv: `rich`, `textual`, `pytest-cov`, `pytest-socket` and the MCP package are not all installed there, and a live Docker daemon is unavailable. To execute the broad Python suite anyway, the merge-gate run used a temporary, untracked test bootstrap that added the repository `src/` directory to `sys.path` and supplied a minimal Rich compatibility stub for formatting-only code. That bootstrap is not part of the branch and must not be committed.

Results on `refactor/pilot-modularization-all-phases`:

- full suite: **429 passed, 10 skipped, 2 failed** in ~32.5 s;
- the only failures were `test_integrity_hash_and_writable` and `test_systemd_analyze_verify_staged_rewrite`; both fail because executable bits are not retained for staged temporary broker artifacts in this runner;
- the same two failures were reproduced on the untouched Phase 0 branch, so they are not introduced by the `pilot.py` modularization;
- excluding only those two runner-specific staging checks: **429 passed, 10 skipped, 2 deselected**, exit code 0;
- AST parsing of the project source tree also passed; one pre-existing `SyntaxWarning` remains in `DockerPilotExtras/backend/resources/migration.py` for an invalid escape sequence;
- configuration archive round-trip still emits the existing `tarfile.extractall()` Python 3.14 deprecation/security warning. Archive hardening remains intentionally out of scope for this behavior-preserving refactor.

A Phase 0 API snapshot was captured before extraction. The current facade matches it exactly for all 66 methods. During regression review, two subtle behavior changes were found and fixed in commit `ca04f6c`: `_setup_logging` and `_load_config` again return `None` as before, and `_with_loading` once again dispatches through the overridable `_show_loading` hook.

Performance gates were run on the connector host (`AMD Ryzen 5 PRO 2400GE`, 4 physical / 8 logical cores). Median microbenchmark results:

| Path | Baseline | Refactored | Ratio |
| --- | ---: | ---: | ---: |
| multi-target parsing, 100k calls | 0.2186 s | 0.2177 s | 0.996x |
| database image matching, 100k calls | 0.2060 s | 0.2082 s | 1.011x |
| direct manager vs facade delegation, 200k calls | 0.0493 s direct | 0.0697 s facade | 1.415x |

The facade overhead is about 20.5 ms across 200,000 calls, roughly **0.10 microseconds per call**. The permanent performance tests use deliberately wider CI-safe budgets and pass independently (`3 passed`). No material CPU-side regression was observed in the extracted hot helpers.

What this environment cannot prove: real Rich/Textual rendering, live Docker-daemon behavior, executable-bit-sensitive root-broker staging on a normal filesystem, and end-to-end Docker performance. Those remain the final normal-environment/CI smoke gate before merge.

## Morning review order

Recommended review path:

1. inspect the commit sequence on `refactor/pilot-modularization-all-phases`;
2. review `pilot.py` first — it should now read primarily as wiring + compatibility facade;
3. review `services/runtime_support.py` because it is the most cross-cutting extraction;
4. review `services/bootstrap.py` for Docker context/logging behavior;
5. inspect manager moves in `container_manager.py` and `monitoring.py`;
6. inspect the leaf services and compatibility callback tests;
7. run full pytest/CI and smoke-test CLI + TUI before merging.

## Out of scope / follow-up

Two very large modules remain separate concerns:

- `deployment_service.py`
- `backup_restore.py`

They should be reviewed/refactored independently rather than folded into this branch. The work here makes that safer by giving their shared runtime dependencies an explicit boundary first.

Also unchanged by design: configuration archive import preserves the previous extraction behavior. Archive-hardening should be handled as a separate security change rather than silently mixed into this behavior-preserving modularization.
