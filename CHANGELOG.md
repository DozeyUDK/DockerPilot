# Changelog

All notable changes to Docker Pilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2024-01-XX

### Added
- Initial release of Docker Pilot
- Container management operations (start, stop, restart, remove, rename, pause, unpause)
- Image management (list, build, remove)
- Real-time monitoring dashboard with CPU, memory, network metrics
- Advanced deployment strategies:
  - Rolling deployment (zero-downtime)
  - Blue-Green deployment
  - Canary deployment
- CI/CD pipeline generation:
  - GitHub Actions
  - GitLab CI
  - Jenkins
- Environment promotion (dev → staging → prod)
- Integration testing framework
- Monitoring alerts system
- Backup and restore functionality
- Interactive CLI mode
- Configuration templates for deployment
- One-click installation scripts for Linux, macOS, and Windows
- Comprehensive documentation

### Features
- Health checks for containers
- Automatic rollback on deployment failure
- Deployment history tracking
- Performance metrics collection
- Resource limits configuration
- Volume and network management
- Log viewing and monitoring

### Technical
- Python 3.9+ support
- Docker API integration
- Rich terminal UI with color output
- YAML configuration support
- Extensible architecture with modular components

---

## [0.9.0-pre.2] - 2026-05-18

### Added
- DockerPilot TUI with interactive exec in a new terminal session
- DockerPilot Extras full-stack installer and Windows CLI bootstrap improvements
- Schema-aware PostgreSQL storage and environment-scoped container views
- Extras preflight API, remote probes, and dedicated CI smoke tests
- Environment-to-server mapping and SSH-based environment promotion
- Container rename flow
- Live migration logs in the deployment progress panel
- Searcher UI split into separate HTML/JS assets

### Changed
- Modularized pilot engine (CLI parser/handlers, deployment, backup mixins)
- Removed Deployments tab; expanded container CI/CD pipeline flow in Extras
- Repository layout cleanup and tighter CI (Python 3.9–3.12)
- Default install uses venv (PEP 668); `--system` option for system-wide install
- Translated remaining DockerPilot Extras frontend strings

### Fixed
- List containers when backing image or image reference was removed
- False-success stateful migrations and hardened mount transfer in Extras
- Empty environment bindings fallback in environment status
- Environments layout overflow and workflow card sizing
- Promotion keeps `include_data` when `skip_backup` is true
- Bind/volume data migration during environment promotion
- TUI terminal launcher fallback and Python 3.9 compatibility
- Extras CORS/session cookies and Node 18+ loader status

## [0.9.0-pre.1] - 2026-01-16

### Changed
- General fixes and stabilization after the 0.1.0 baseline

## [Unreleased]

### Planned
- Kubernetes integration
- Docker Compose support
- Multi-registry support
- Advanced monitoring dashboards
- Webhook notifications
- Plugin system

