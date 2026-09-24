# MonitoringManager modularization review handoff

## Scope

This branch modularizes `src/dockerpilot/monitoring.py` while preserving the existing `MonitoringManager` compatibility surface.

Phase 0 captured the original seven method names and signatures before extraction.

## Result

Baseline:

- `monitoring.py`: 17,970 bytes
- file: 374 lines
- `MonitoringManager`: 358 class lines
- methods: 7

Final facade:

- `monitoring.py`: ~2.4 KB
- about 50 file lines
- `MonitoringManager`: 34 class lines
- methods: 7

The manager is now a compatibility facade over focused monitoring modules.

## Extracted modules

### Statistics sampling

`src/dockerpilot/monitoring_stats.py`

- two-sample Docker stats collection
- comprehensive CPU calculation through the existing `calculate_cpu_percent()` helper
- memory usage/limit/percentage
- aggregate network RX/TX
- PID count
- `ContainerStats` creation

### Dashboard and history

`src/dockerpilot/monitoring_dashboard.py`

- multi-container live dashboard
- metric trend display
- 60-sample per-container history retention
- metrics JSON serialization
- monitoring summary table

### One-shot snapshot

`src/dockerpilot/monitoring_snapshot.py`

- legacy dockerpilot-Lite one-shot stats path
- independent legacy CPU formula
- memory/network/PID display

The one-shot CPU formula is intentionally not normalized to the comprehensive statistics formula in this refactor. Runtime tests preserve the existing behaviour.

### Live stream

`src/dockerpilot/monitoring_live.py`

- Docker streaming stats consumption
- bytes/string/dict payload handling
- CPU delta calculation
- live memory display
- screen clearing and duration handling
- Ctrl+C behaviour

## Compatibility note

`monitoring.py` intentionally retains `import time` even though the facade does not call `time` directly. Existing regression code monkeypatches `dockerpilot.monitoring.time.sleep`; because Python shares the imported `time` module object with extracted monitoring modules, retaining this module-level symbol preserves that compatibility hook.

## Phase checkpoints

1. Phase 0 — API/signature baseline and no-regrowth gate
2. Phase 1 — statistics sampling extraction
3. Phase 2 — dashboard/history extraction
4. Phase 3 — one-shot snapshot extraction
5. Phase 4 — live stream extraction
6. Phase 5 — facade cleanup and review handoff

## Validation

Final branch validation:

- monitoring Phase 0 contract: `2 passed`
- full portable unit suite: `576 passed, 9 skipped`
- modularization gates: `30 passed`
- smoke imports: `2 passed`
- security unit gate: `11 passed, 1 skipped`

Expected warnings remain unchanged:

- `services/configuration_archive.py` `tar.extractall()` Python 3.14 warning
- `container_restore.py` legacy `tar.extractall()` Python 3.14 warning

## Behaviour preserved

- all seven `MonitoringManager` method names and signatures
- one-second two-sample stats cadence
- comprehensive stats CPU calculation
- distinct legacy one-shot/live CPU calculation
- memory/network/PID calculations
- dashboard history retention and trend rendering
- metrics JSON format and ISO timestamps
- dashboard Docker `NotFound` handling
- live bytes/string/dict stats parsing
- live screen clearing and Ctrl+C handling
- module-level `monitoring.time` compatibility hook

## Review focus

Recommended review order:

1. `monitoring.py` facade
2. Phase 0 contract
3. `monitoring_stats.py`
4. `monitoring_dashboard.py`
5. `monitoring_snapshot.py`
6. `monitoring_live.py`
7. focused monitoring regression tests

The main risk is semantic drift in sampling/calculation behaviour rather than API drift; runtime tests intentionally exercise real extracted paths rather than only delegation.
