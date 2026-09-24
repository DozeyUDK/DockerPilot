# DeploymentServiceMixin modularization plan

## Goal

Reduce `src/dockerpilot/deployment_service.py` from a 2k-line orchestration monolith to a compatibility facade while preserving the Phase 0 method/signature surface and current CLI/TUI behavior.

Phase 0 baseline:

- 32 methods on `DeploymentServiceMixin`
- 1,978 class lines
- 2,044 file lines
- existing helper modules already cover build-source discovery, deployment helper normalization, image preparation, health checks, and deployment-history recording

The refactor should move behavior behind the existing methods rather than rename or remove them.

## Constraints

- Preserve all Phase 0 method signatures unless a separate intentional API change is approved.
- Keep `DeploymentServiceMixin` usable through `DockerPilotEnhanced` without changing callers.
- Do not mix `dockerpilot-runner` implementation into DockerPilot.
- Preserve cancellation/progress callbacks and current console/log output semantics where practical.
- Avoid broad rewrites of working helper modules merely for naming consistency.
- Each phase must pass the deployment contract gate plus relevant regression tests before proceeding.

## Phase 0 — baseline and contract gate

Create a static AST-based snapshot of every `DeploymentServiceMixin` method signature and add a no-regrowth budget for the class. Run the existing deployment helper, image preparation, health-check, canary, facade, and general unit gates before implementation extraction.

## Phase 1 — container runtime planning helpers

Extract repeated container-spec construction and lifecycle decisions into a focused module, without changing strategy flow. Candidate responsibilities:

- network/port selection for temporary and final containers
- command fallback for images such as Alpine
- privileged-mode decision logic
- common container kwargs assembly
- safe stop/remove helpers
- active-container discovery
- temporary deployment names and port mappings

Primary target: remove duplicated Docker kwargs logic from rolling, blue/green, and canary flows.

## Phase 2 — validation service

Move `_comprehensive_container_validation` and promotion validation mechanics to a dedicated validation service. Keep facade methods as delegates. Split pure checks where useful:

- running/restart stability
- HTTP health response and response-time checks
- critical-log pattern checks
- resource usage checks
- volume checks
- final stability check

Inject sleep/request/logging dependencies where this improves deterministic tests.

## Phase 3 — rolling and quick strategies

Extract `_rolling_deploy` and `quick_deploy` into strategy functions/services. Preserve current method signatures as facade delegates.

Rolling strategy boundaries:

- image preparation
- previous deployment discovery
- temporary container creation
- health/validation
- traffic switch
- rollback/cleanup
- deployment-history record

Quick strategy boundaries:

- config loading/defaults
- image build
- old container/image cleanup
- replacement start
- optional health check
- deployment-history record

## Phase 4 — blue/green strategy

Extract `_blue_green_deploy_enhanced`, the largest orchestration path, after common runtime and validation helpers exist. Keep cancellation checkpoints and progress events behavior-compatible.

Separate concerns inside the strategy rather than creating another monolith:

- active-slot discovery
- optional backup
- target-slot preparation
- target deployment/data migration
- target validation/parallel tests
- traffic switch
- final validation and rollback
- cleanup/history/progress completion

## Phase 5 — canary strategy

Extract `_canary_deploy` to its own strategy module and reuse the common runtime builder. Keep `_monitor_canary_performance` delegated to the existing health-check helper.

## Phase 6 — promotion service

Extract `environment_promotion`, `_run_pre_promotion_checks`, and `_run_post_promotion_validation` into a promotion service. Preserve environment-specific resource policy and config persistence behavior.

This phase should make explicit which promotion checks are real and which are currently simulated sleeps/messages, without silently changing semantics during the refactor.

## Phase 7 — build/config/history facade cleanup

Move remaining leaf behavior that still bloats the mixin where it has a clear home:

- Dockerfile template/config-file creation
- enhanced/standalone image-build orchestration
- deployment-history rendering

Do not move simple delegation methods solely to reduce line count if doing so makes ownership less clear.

## Final target

`DeploymentServiceMixin` should become a thin compatibility facade that wires existing DockerPilot state (`client`, `console`, `logger`, config, backup callbacks, progress/cancellation callbacks) into focused services/strategies.

A reasonable final budget is roughly 350–600 class lines, but correctness and explicit boundaries take priority over hitting a line-count target.

## Validation gates

After every phase:

1. `tests/test_deployment_service_phase0_contract.py`
2. deployment helper/build/image/health/canary focused tests
3. pilot facade/regression tests
4. `modularization-gates`
5. `unit-fast`

Before merge, also run `smoke-import` and `unit-security`.
