# Deployment service modularization review handoff

## Scope

This branch modularizes `src/dockerpilot/deployment_service.py` while preserving the existing `DeploymentServiceMixin` compatibility surface.

Phase 0 captured the public/internal method contract before extraction. The final mixin still exposes the same 32 methods with the same signatures.

## Result

Baseline:

- `deployment_service.py`: ~105 KB
- `DeploymentServiceMixin`: 1,978 class lines
- 32 methods

Final:

- `deployment_service.py`: 15,843 bytes
- file: 363 lines
- `DeploymentServiceMixin`: 281 class lines
- 32 methods
- no unused imports in the final facade

The mixin is now primarily a compatibility facade and orchestration entry point rather than the owner of strategy implementations.

## Extracted modules

### Runtime planning

`src/dockerpilot/deployment_runtime.py`

- temporary port mapping offsets
- privileged-mode detection/inheritance
- command/Alpine keepalive fallback

### Validation

`src/dockerpilot/deployment_validation.py`

- comprehensive pre-traffic-switch container validation
- HTTP response checks
- restart/stability checks
- log/error checks
- resource and volume checks

### Deployment strategies

`src/dockerpilot/deployment_strategies/`

- `rolling.py`
- `quick.py`
- `blue_green.py`
- `canary.py`

The original `DeploymentServiceMixin` methods remain as delegates with the Phase 0 signatures.

### Promotion

`src/dockerpilot/deployment_promotion.py`

- environment promotion orchestration
- pre-promotion checks
- post-promotion validation

### Build and config leaf services

`src/dockerpilot/deployment_build.py`

- enhanced image build orchestration
- standalone image build flow

`src/dockerpilot/deployment_config_io.py`

- Dockerfile template creation
- deployment configuration template creation

### Existing helpers reused

The refactor intentionally kept and reused the already separated modules rather than rewriting them:

- `build_source.py`
- `deployment_helpers.py`
- `deployment_history.py`
- `health_checks.py`
- `image_preparation.py`

`show_deployment_history()` now delegates to `deployment_history.py` as well.

## Phase checkpoints

1. Phase 0 — compatibility baseline and no-regrowth gate
2. Phase 1 — runtime planning helpers
3. Phase 2 — comprehensive validation extraction
4. Phase 3 — rolling and quick strategy extraction
5. Phase 4 — blue/green strategy extraction
6. Phase 5 — canary strategy extraction
7. Phase 6 — promotion extraction
8. Phase 7 — build/config/history leaf cleanup

## Validation

Final branch validation:

- Phase 0 deployment contract: `2 passed`
- full portable unit suite: `519 passed, 9 skipped`
- modularization gates: `30 passed`
- smoke imports: `2 passed`
- security unit gate: `11 passed, 1 skipped`

The existing configuration archive `tar.extractall()` deprecation warning remains unchanged and is outside this refactor.

## Compatibility notes

No intentional CLI or TUI behavior change is part of this refactor. Deployment strategy ordering, rollback behavior, cancellation checkpoints, progress output, build behavior, promotion behavior, and facade method signatures were preserved during extraction.

## Review focus

Recommended review order:

1. `deployment_service.py` facade/delegation
2. Phase 0 contract fixture/test
3. strategy modules under `deployment_strategies/`
4. `deployment_validation.py`
5. `deployment_promotion.py`
6. runtime/build/config leaf modules
7. focused regression tests

The main review risk is semantic drift at strategy boundaries rather than API drift; the Phase 0 contract specifically guards the latter.
