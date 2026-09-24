# DockerPilot `pilot.py` modularization — Phase 0 inventory

Status: **inventory only — no runtime behavior changes**

Branch: `refactor/pilot-modularization-phase-0`

## Baseline

`src/dockerpilot/pilot.py` currently contains the `DockerPilotEnhanced` composition root/facade plus a large amount of implementation logic.

Static AST inventory at the Phase 0 baseline:

- `DockerPilotEnhanced`: lines **38–1749**
- methods defined directly on the class: **66**
- inherited implementation: `DeploymentServiceMixin`, `BackupRestoreMixin`
- major collaborators created by `__init__`: `ContainerManager`, `ImageManager`, `MonitoringManager`
- CLI is already partially extracted to `dockerpilot.cli` (`parser.py`, `handlers.py`, `interactive.py`, `tui.py`)

The goal is **not** to redesign the public API in one pass. `DockerPilotEnhanced` should remain the compatibility facade while implementation is extracted behind it in small, reviewable steps.

## Responsibility inventory

### 1. Bootstrap / runtime infrastructure

Current methods:

- `__init__`
- `_show_banner`
- `_configure_console_streams`
- `_setup_logging`
- `_load_config`
- `_init_docker_client`
- `_signal_handler`
- `_error_handler`
- `_parse_multi_target`
- `_update_progress`
- `_show_loading`
- `_with_loading`

These methods own process/runtime concerns: console setup, logging, Docker client creation, signal handling, generic error handling, progress callbacks and terminal loading indicators.

**Recommendation:** keep composition/wiring in `pilot.py`; extract reusable runtime helpers only after the mixin dependencies below are untangled.

### 2. Deployment support shared with existing mixins

Current methods:

- `_check_cancel_flag`
- `_load_health_check_defaults`
- `_get_database_config`
- `_get_database_name`
- `_is_database_service`

These are not isolated helpers. Existing mixins call them directly.

Observed dependencies:

`DeploymentServiceMixin` ->

- `_check_cancel_flag`
- `_update_progress`
- `_with_loading`
- `_load_health_check_defaults`
- `_get_database_config`
- `_get_database_name`

`BackupRestoreMixin` ->

- `_check_cancel_flag`
- `_update_progress`
- `_with_loading`
- `_get_database_config`

**Risk:** high relative to the leaf services. Do not extract this group first. A shared deployment/runtime support object or dedicated mixin should be introduced only after low-coupling domains are moved.

### 3. Container / image facade

Current methods:

- `list_containers`
- `list_images`
- `remove_image`
- `prune_dangling_images`
- `container_operation`
- `update_restart_policy`
- `rename_container`
- `run_new_container`
- `exec_container`

Most of this area is already a facade over `ContainerManager` / `ImageManager`.

External usage is broad: CLI handlers, interactive CLI, TUI and MCP use these methods. Therefore these names should remain available on `DockerPilotEnhanced` during the refactor.

Special case: `exec_container` still contains real Docker/TTY implementation in `pilot.py`; it is a later candidate for `ContainerManager` or a terminal execution service.

### 4. Monitoring and legacy container operations

Current methods:

- `get_container_stats`
- `monitor_containers_dashboard`
- `get_container_stats_once`
- `monitor_container_live`
- `stop_and_remove_container`
- `exec_command_non_interactive`
- `health_check_standalone`

`get_container_stats` and `monitor_containers_dashboard` already delegate to `MonitoringManager`.

The remaining methods still implement Docker operations, terminal rendering, statistics calculation, HTTP health checking or command execution directly inside `pilot.py`.

**Later extraction targets:**

- monitoring implementation -> `MonitoringManager`
- stop/remove and non-interactive exec -> `ContainerManager`
- standalone HTTP health check -> health-check service/module

Public facade methods can remain in `DockerPilotEnhanced` as delegates.

### 5. Deployment history presentation

Current method:

- `show_deployment_history`

It reads `deployment_history.json`, formats records and renders a Rich table.

There is already a `deployment_history.py` module, so this should eventually delegate there (or to a small presentation/service layer) instead of owning persistence/rendering in `pilot.py`.

### 6. CLI bridge / compatibility layer

Current methods:

- `create_cli_parser`
- `run_cli`
- `_run_container_interactive`
- `_handle_container_cli`
- `_handle_monitor_cli`
- `_handle_deploy_cli`
- `_handle_backup_cli`
- `_handle_config_cli`
- `_handle_pipeline_cli`
- `_run_interactive_menu`

Most `_handle_*` methods are compatibility shims that immediately import and call functions from `cli.handlers`.

`_run_container_interactive` is the exception: it is ~182 lines of prompt/UI logic that still lives in `pilot.py`, even though `cli/interactive.py` already exists.

**Recommendation:** move `_run_container_interactive` into `cli/interactive.py` in a later focused phase, then keep only a thin compatibility delegate if external callers still require it.

### 7. CI/CD pipeline generation

Current methods:

- `integrate_with_git`
- `create_pipeline_config`
- `_create_github_actions_config`
- `_create_gitlab_ci_config`
- `_create_jenkins_config`

Dependencies are limited to filesystem/templates, GitPython, console and logger.

**Extraction candidate:** `PipelineService` / `pipeline_service.py`.

This is a strong first implementation extraction because coupling to Docker runtime state is minimal.

### 8. Integration-test runner

Current methods:

- `run_integration_tests`
- `_run_single_integration_test`
- `_run_http_test`
- `_run_database_test`
- `_run_custom_test`
- `_generate_test_report`
- `_save_test_report`

This is a cohesive subsystem with its own configuration loading, execution and report generation.

**Extraction candidate:** `IntegrationTestService` / `integration_testing.py`.

It needs `console` and `logger`, but otherwise has low coupling to `DockerPilotEnhanced` state.

### 9. Alerts / notifications

Current methods:

- `setup_monitoring_alerts`
- `_initialize_alert_monitoring`
- `check_alerts`
- `_trigger_alert`
- `_send_notification`

State introduced dynamically:

- `self.alert_rules`
- `self.notification_channels`

**Extraction candidate:** `AlertService` / `alerts.py`.

Moving the alert state into the service would also remove dynamically-created attributes from the facade.

### 10. Template/document generators

Current methods:

- `create_production_checklist`
- `generate_documentation`

Both are leaf operations: read packaged templates, write files, log/render status.

**Extraction candidate:** one small `TemplateService`, or separate `ChecklistService` / `DocumentationService` only if they grow.

These are among the safest methods to extract first.

### 11. System validation

Current method:

- `validate_system_requirements`

Checks Python, Docker, Python modules, disk space and Docker permissions.

**Extraction candidate:** `SystemValidationService`.

Dependency on Docker is explicit (`client`); console can be injected.

### 12. Configuration archive import/export

Current methods:

- `export_configuration`
- `import_configuration`

These are filesystem/archive operations with logger/console dependencies.

**Extraction candidate:** `ConfigurationArchiveService`.

Note: archive extraction safety should be audited separately; Phase 0 does not alter behavior.

## Dependency map

```text
DockerPilotEnhanced
|
|-- composition/runtime
|   |-- Console
|   |-- logger
|   |-- docker client
|   |-- ContainerManager
|   |-- ImageManager
|   `-- MonitoringManager
|
|-- DeploymentServiceMixin
|   `-- calls pilot runtime/deployment helpers
|       |-- _check_cancel_flag
|       |-- _update_progress
|       |-- _with_loading
|       |-- _load_health_check_defaults
|       |-- _get_database_config
|       `-- _get_database_name
|
|-- BackupRestoreMixin
|   `-- calls pilot runtime/deployment helpers
|       |-- _check_cancel_flag
|       |-- _update_progress
|       |-- _with_loading
|       `-- _get_database_config
|
|-- CLI / TUI / MCP
|   `-- depend on DockerPilotEnhanced public facade
|       |-- list_containers / list_images
|       |-- container operations
|       |-- monitoring operations
|       |-- deployment / backup methods
|       |-- pipeline / config / docs / validation
|       `-- integration tests / alerts / checklist
|
`-- leaf implementation still embedded in pilot.py
    |-- pipeline generation
    |-- integration testing
    |-- alerts
    |-- documentation/checklist
    |-- system validation
    `-- configuration archive
```

## Compatibility surface observed outside `pilot.py`

The following methods have direct callers in `src/` and should be treated as compatibility API during extraction:

### High-traffic facade

- `list_containers` — CLI, interactive CLI, TUI, MCP
- `list_images` — CLI, interactive CLI, TUI, MCP
- `_parse_multi_target` — CLI handlers and interactive CLI
- container actions: `run_new_container`, `rename_container`, `remove_image`, `prune_dangling_images`, `container_operation`, `update_restart_policy`, `stop_and_remove_container`, `exec_container`, `exec_command_non_interactive`
- monitoring: `monitor_containers_dashboard`, `monitor_container_live`, `get_container_stats_once`, `health_check_standalone`

### Product features exposed through CLI

- `show_deployment_history`
- `validate_system_requirements`
- `run_integration_tests`
- `setup_monitoring_alerts`
- `generate_documentation`
- `create_production_checklist`
- `create_pipeline_config`
- `export_configuration`
- `import_configuration`

### Internal contracts used by mixins/managers

- `_error_handler` is injected into `ContainerManager` and `ImageManager`
- `_check_cancel_flag`, `_update_progress`, `_with_loading`, `_load_health_check_defaults`, `_get_database_config`, `_get_database_name` are effectively internal interfaces consumed by the inherited mixins

These contracts are the main reason to use delegation instead of deleting/moving methods wholesale.

## Proposed target shape

Do **not** create every possible service immediately. Start with cohesive domains that already exist in `pilot.py` and have clear boundaries.

```text
src/dockerpilot/
├── pilot.py                       # composition root + backwards-compatible facade
├── container_manager.py
├── image_manager.py
├── monitoring.py
├── deployment_service.py
├── backup_restore.py
├── services/
│   ├── __init__.py
│   ├── pipeline.py
│   ├── integration_testing.py
│   ├── alerts.py
│   ├── templates.py
│   ├── system_validation.py
│   └── configuration_archive.py
└── cli/
    ├── handlers.py
    ├── interactive.py
    ├── parser.py
    └── tui.py
```

The `services/` package should stay small. If a service remains only a few functions and has no state, a module-level functional API is preferable to unnecessary classes.

## Recommended extraction order

### Phase 1 — leaf services, zero public API change

Extract the lowest-coupling domains while preserving facade delegates in `DockerPilotEnhanced`:

1. template generation (`create_production_checklist`, `generate_documentation`)
2. pipeline generation (`integrate_with_git`, `create_pipeline_config`, provider helpers)
3. configuration archive (`export_configuration`, `import_configuration`)
4. system validation (`validate_system_requirements`)
5. integration-test runner
6. alerts

Each domain should be a separate commit or tightly-scoped commit group with tests.

### Phase 2 — finish CLI extraction

Move `_run_container_interactive` to `cli/interactive.py` and review whether the `_handle_*_cli` compatibility shims can remain as tiny delegates or be removed after checking callers.

### Phase 3 — eliminate duplicated Docker implementation

Move remaining direct Docker operations from `pilot.py` into the managers that already own those domains:

- monitoring/stat calculations -> `MonitoringManager`
- stop/remove, exec helpers -> `ContainerManager`
- health checks -> dedicated health-check module/service where appropriate
- deployment-history presentation/persistence -> existing deployment-history boundary

Keep facade delegates while CLI/MCP compatibility requires them.

### Phase 4 — runtime/deployment support boundary

Only after the easier extractions are stable, address helpers shared with `DeploymentServiceMixin` and `BackupRestoreMixin`:

- cancellation
- progress callbacks
- loading UI
- database/health-check metadata

This phase may introduce a small runtime context/support object shared by the facade and mixins. Avoid changing both large mixins at the same time unless tests prove the boundary first.

### Phase 5 — shrink composition root

After all domains delegate cleanly, `pilot.py` should primarily contain:

- `DockerPilotEnhanced.__init__`
- dependency wiring
- signal/process lifecycle
- compatibility facade methods
- CLI entry delegation
- minimal module bootstrap

Target size is not a hard rule, but roughly **200–400 lines** is reasonable if compatibility delegates remain.

## Refactor rules / guardrails

1. **No CLI command or flag changes** as part of modularization.
2. **No public `DockerPilotEnhanced` method removal** in the extraction phase without a dedicated compatibility decision.
3. **No simultaneous rewrite of `deployment_service.py` or `backup_restore.py`.** They are separate large refactors.
4. Extract implementation first; keep delegates in `pilot.py`.
5. Constructor dependencies should be explicit (`console`, `logger`, `client`, paths/callbacks) rather than passing the whole pilot object into every service.
6. Do not introduce services that merely forward one line to another object unless they own a real domain boundary.
7. One domain extraction at a time, test, commit, then continue.
8. Preserve return values, output semantics and exception behavior unless a separate bugfix is explicitly approved.

## Phase 0 conclusion

The problem is not simply the line count. `pilot.py` currently combines four roles:

1. composition root,
2. backwards-compatible facade,
3. shared runtime support for large mixins,
4. implementation host for several independent product features.

The safest path is to leave roles 1–2 in place, defer the mixin support boundary, and extract the independent feature implementations first.

**Recommended Phase 1 first slice:** template/document generation. It has very low coupling, a small surface, and gives us the service/delegation pattern that later extractions can copy.
