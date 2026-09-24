# MonitoringManager modularization plan

## Baseline

`src/dockerpilot/monitoring.py` currently contains:

- 17,970 bytes
- 374 file lines
- 358 `MonitoringManager` class lines
- 7 class methods

Largest methods:

- `monitor_containers_dashboard()` — 108 lines
- `monitor_container_live()` — 88 lines
- `get_container_stats_once()` — 61 lines
- `get_container_stats()` — 41 lines

## Goal

Turn `MonitoringManager` into a thin compatibility facade while preserving all seven public/private method names and signatures. This refactor should not intentionally change CLI/TUI output, sampling cadence, CPU/memory/network calculations, metrics persistence, live monitoring behaviour or error handling.

## Phases

### Phase 0 — compatibility baseline

- freeze the seven-method API/signature contract
- add a no-regrowth gate
- capture baseline size metrics

### Phase 1 — statistics sampling

Extract `get_container_stats()` and its Docker stats parsing into a focused statistics module. Preserve the existing two-sample CPU calculation via `utils.calculate_cpu_percent()`.

### Phase 2 — dashboard and history

Extract:

- `monitor_containers_dashboard()`
- `_save_metrics_history()`
- `_show_monitoring_summary()`

Keep rendering, history retention, metrics JSON format and summary calculations unchanged.

### Phase 3 — one-shot snapshot

Extract `get_container_stats_once()` without normalizing its legacy CPU formula. The one-shot path currently calculates CPU independently from `get_container_stats()`; behavioural parity is more important than deduplicating formulas in this refactor.

### Phase 4 — live stream

Extract `monitor_container_live()` including stream parsing, screen clearing, CPU delta calculation, duration handling and Ctrl+C semantics.

### Phase 5 — final facade cleanup

- remove dead imports
- compile extracted modules
- re-run API/no-regrowth contract
- run full portable unit suite and existing modularization/smoke/security gates
- write a review handoff

## Review risks

The main semantic risks are:

- accidentally changing the two different CPU formulas used by the comprehensive and Lite paths
- losing one-second sampling delays
- changing bytes/string handling for streaming stats
- changing metrics-history JSON timestamps
- changing dashboard history retention or summary values
- losing Docker `NotFound` handling in the dashboard

Focused runtime regression tests should exercise these paths rather than only checking delegation.
