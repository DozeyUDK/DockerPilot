# Docker Pilot
[![CI](https://github.com/DozeyUDK/DockerPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/DozeyUDK/DockerPilot/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/DozeyUDK/DockerPilot)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/DozeyUDK/DockerPilot?sort=semver)](https://github.com/DozeyUDK/DockerPilot/releases)
[![Docs](https://img.shields.io/badge/docs-README%20%26%20guides-blue)](docs/)
<img width="2667" height="465" alt="image" src="https://github.com/user-attachments/assets/c6ae8332-7e0e-4588-b014-ad9a90992087" />

**Docker container management tool with advanced deployment capabilities, real-time monitoring, and CI/CD integration.**

## Full Stack Install (CLI + DockerPilotExtras)

Use this when you want the DockerPilot CLI and the optional DockerPilotExtras web panel on the same machine.

### Linux/macOS
```bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot && chmod +x install_everything.sh && ./install_everything.sh
```

### Windows (PowerShell)
```powershell
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
powershell -ExecutionPolicy Bypass -File install.ps1 -Extras
```

### Windows (CMD)
```cmd
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot && install.bat extras
```

**What full stack install does:**
- installs DockerPilot CLI
- installs DockerPilotExtras backend Python dependencies
- installs DockerPilotExtras frontend dependencies with `npm install` if Node.js and npm are already available
- leaves DockerPilotExtras frontend install as a clear warning if Node.js/npm are missing

**Requirements for full stack install:**
- Python 3.9+
- Docker 20.10+
- Node.js 18+ and npm for the DockerPilotExtras frontend

## Quick Install (CLI Only)

### Linux/macOS
```bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot && chmod +x install.sh && ./install.sh
```

### Windows (PowerShell)
```powershell
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
powershell -ExecutionPolicy Bypass -File install.ps1
```

### Windows (CMD)
```cmd
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot && install.bat
```

**What the installer does:**
- Checks Python 3.9+ and Docker
- Installs Docker Pilot in a **virtual environment** (default), so it works on Ubuntu/Debian 24.04+ and other distros with [PEP 668](https://peps.python.org/pep-0668/) (no "externally-managed-environment" error)
- Puts `dockerpilot` in `~/.local/bin` (ensure it's in your `PATH`)
- Verifies installation

**Install options:**
- `./install.sh` — install in venv (recommended for most users)
- `./install.sh --system` — install system-wide (e.g. if you develop Docker Pilot)
- `./install.sh --extras` — install CLI plus DockerPilotExtras
- `./install_everything.sh` — wrapper for `./install.sh --extras`

**Prerequisites:**
- Python 3.9+
- Docker 20.10+ (with daemon running)
- On Debian/Ubuntu: `sudo apt install python3-venv` if the venv step fails

## Manual Installation

```bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
```

On **Ubuntu 24.04+, Debian 12+** (or any PEP 668 environment), use a venv or `--break-system-packages`:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
# run with: .venv/bin/dockerpilot
```

Or system-wide (if you prefer):

```bash
pip install -e . --break-system-packages
```

**Optional extras:**
```bash
pip install -e .[git]   # Git integration for CI/CD
pip install -e .[test]  # Development dependencies
pip install -e .[tui]   # Mouse-friendly terminal UI
```

**Verify installation:**
```bash
dockerpilot --help
dockerpilot validate  # Check system requirements
```

**Install DockerPilotExtras later:**
```bash
cd DockerPilotExtras
chmod +x setup_extras.sh && ./setup_extras.sh
```

---

## Repository Layout

- `src/dockerpilot/` - core CLI package
- `DockerPilotExtras/` - optional web panel built on top of the CLI
- `docs/` - user-facing guides and reference material
- `scripts/` - maintainer and release helpers
- `tools/` - optional standalone tools

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Container Management](#container-management)
- [Image Management](#image-management)
- [Monitoring](#monitoring)
- [Deployment Strategies](#deployment-strategies)
- [CI/CD Integration](#cicd-integration)
- [Configuration](#configuration)
- [Advanced Features](#advanced-features)
- [Troubleshooting](#troubleshooting)
- [Additional Guides](#additional-guides)

## Features

### Core Capabilities
- **Container Operations**: Start, stop, restart, remove, pause, unpause, exec into containers
- **Image Management**: List, build, and remove Docker images
- **Real-time Monitoring**: CPU, memory, network I/O, and process tracking
- **Health Checks**: Automated container health validation
- **Interactive Dashboard**: Live metrics with trend indicators
- **Container Shell Access**: Execute interactive bash/sh sessions inside running containers

### Advanced Deployment
- **Rolling Deployment**: Zero-downtime updates with automatic rollback
- **Blue-Green Deployment**: Parallel environment switching for maximum safety
- **Canary Deployment**: Gradual traffic shifting with performance monitoring

### DevOps Integration
- GitHub Actions, GitLab CI, and Jenkins pipeline generation
- Environment promotion (dev -> staging -> prod)
- Integration testing framework
- Monitoring and alerting system
- Backup and restore functionality

## Quick Start

### Interactive Mode

Run without arguments to enter interactive mode:

```bash
dockerpilot
```

Select from available commands:
- `list` - List all containers
- `start` - Start a container
- `stop` - Stop a container
- `monitor` - Real-time monitoring
- `deploy-init` - Create deployment config
- And many more...

### Mouse-Friendly TUI

Install the optional TUI extra and launch the clickable terminal UI:

```bash
pip install -e .[tui]
dockerpilot tui
```

Use the mouse to:
- click through command groups and subcommands
- fill arguments in a form instead of retyping flags
- pick existing containers and images from live Docker-backed lists for target-based commands
- run the generated command directly from the TUI

### Usage

After installation, the `dockerpilot` command is available globally:

```bash
# Interactive mode
dockerpilot

# Mouse-friendly TUI
dockerpilot tui

# CLI commands
dockerpilot container list --all
dockerpilot monitor myapp --duration 300
dockerpilot deploy config deployment.yml --type rolling

# Get help
dockerpilot --help
```

If `pip install -e .` was used, the command is automatically available. Otherwise, you can run:

```bash
python -m dockerpilot.main
```

## Container Management

### List Containers

```bash
# Table format (default)
dockerpilot container list --all

# JSON format
dockerpilot container list --format json
```

### Container Operations

```bash
# Start container
dockerpilot container start myapp

# Stop container (with timeout)
dockerpilot container stop myapp --timeout 30

# Restart container
dockerpilot container restart myapp

# Remove container
dockerpilot container remove myapp --force

# Pause/Unpause
dockerpilot container pause myapp
dockerpilot container unpause myapp

# Execute interactive shell in container
dockerpilot container exec myapp

# Execute custom command in container
dockerpilot container exec myapp --command /bin/sh
dockerpilot container exec myapp --command "ls -la /app"
```

### View Container Logs

Interactive mode:
```bash
dockerpilot
# Choose: logs
```

### View Container Details (JSON)

Interactive mode:
```bash
dockerpilot
# Choose: json
# Enter container name
```

## Image Management

### List Images

```bash
# Table format
dockerpilot container list-images --all

# JSON format
dockerpilot container list-images --format json
```

### Build Images

```bash
dockerpilot build /path/to/dockerfile myapp:latest --no-cache
```

Interactive mode:
```bash
dockerpilot
# Choose: build
# Follow prompts
```

### Remove Images

```bash
dockerpilot container remove-image myapp:latest --force
```

## Monitoring

### Real-time Dashboard

Monitor all running containers:
```bash
dockerpilot monitor --duration 300
```

Monitor specific containers:
```bash
dockerpilot monitor webapp database cache --duration 600
```

The dashboard displays:
- Container status
- CPU usage with trend indicators (up/down/steady)
- Memory usage and percentage
- Network I/O (download/upload)
- Process count (PIDs)
- Uptime

Metrics are automatically saved to `docker_metrics.json`.

## One-Click Deploy

**Quick deploy in 2 steps:**

```bash
# 1. Create deployment config
dockerpilot deploy init --output deployment.yml

# 2. Edit deployment.yml with your settings, then deploy
dockerpilot deploy config deployment.yml --type rolling
```

**Deployment types:**
- `rolling` - Zero-downtime updates (default)
- `blue-green` - Safest, parallel environments
- `canary` - Gradual rollout (5% -> 100%)

**Example deployment.yml:**
```yaml
deployment:
  image_tag: 'myapp:latest'
  container_name: 'myapp'
  port_mapping: {'8080': '8080'}
  environment: {ENV: 'production'}
  restart_policy: 'unless-stopped'
  health_check_endpoint: '/health'
```

**View deployment history:**
```bash
dockerpilot deploy history --limit 20
```

## CI/CD Integration

### Generate Pipeline Configurations

**GitHub Actions:**
```bash
dockerpilot pipeline create --type github --output .github/workflows
```

**GitLab CI:**
```bash
dockerpilot pipeline create --type gitlab
```

**Jenkins:**
```bash
dockerpilot pipeline create --type jenkins
```

### Environment Promotion

Promote from dev to staging:
```bash
dockerpilot promote dev staging --config deployment.yml
```

Promote to production:
```bash
dockerpilot promote staging prod --config deployment.yml
```

**Features:**
- Environment-specific resource allocation
- Automated pre-promotion checks
- Post-promotion validation
- Rollback on failure

## Configuration

### Logging Levels

```bash
dockerpilot --log-level DEBUG container list
```

Available levels: DEBUG, INFO, WARNING, ERROR

### Configuration Files

The tool uses several configuration files:

- `deployment.yml` - Deployment configuration
- `alerts.yml` - Monitoring alerts
- `integration-tests.yml` - Test definitions
- `docker_pilot.log` - Application logs
- `docker_metrics.json` - Performance metrics
- `deployment_history.json` - Deployment records

### Export/Import Configuration

```bash
# Export all configs
dockerpilot config export --output backup.tar.gz

# Import configs
dockerpilot config import backup.tar.gz
```

## Advanced Features

### Integration Testing

Create test configuration (`integration-tests.yml`):

```yaml
tests:
  - name: 'Health Check'
    type: 'http'
    url: 'http://localhost:8080/health'
    expected_status: 200
    timeout: 5
  
  - name: 'API Endpoint'
    type: 'http'
    url: 'http://localhost:8080/api/status'
    expected_status: 200
    timeout: 10
```

Run tests:
```bash
dockerpilot test --config integration-tests.yml
```

### Monitoring Alerts

Setup alerts:
```bash
dockerpilot alerts --config alerts.yml
```

Configure alert rules in `alerts.yml`:

```yaml
alerts:
  - name: 'high_cpu_usage'
    condition: 'cpu_percent > 80'
    duration: '5m'
    severity: 'warning'
    message: 'CPU usage is above 80%'

notification_channels:
  - type: 'slack'
    webhook_url: 'https://hooks.slack.com/...'
    channel: '#alerts'
```

### Backup and Restore

Create backup:
```bash
dockerpilot backup create --path ./backup_20240104
```

Restore from backup:
```bash
dockerpilot backup restore ./backup_20240104
```

Backups include:
- Container configurations
- Image information
- Network settings
- Volume definitions

### Production Checklist

Generate deployment checklist:
```bash
dockerpilot checklist --output production-checklist.md
```

### Documentation Generation

Generate complete documentation:
```bash
dockerpilot docs --output ./docs
```

## Troubleshooting

### Common Issues

**Docker Connection Failed:**
```bash
# Check Docker status
docker info

# Add user to docker group
sudo usermod -aG docker $USER

# Restart Docker
sudo systemctl restart docker
```

**Permission Denied:**
```bash
sudo chown $USER:docker /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
```

**Health Check Failures:**
- Verify endpoint exists: `curl http://localhost:8080/health`
- Check container logs
- Increase timeout in deployment config

**Port Already in Use:**
```bash
# Find process using port
netstat -tulpn | grep :8080

# Kill process or use different port
```

### Debug Mode

Enable detailed logging:
```bash
dockerpilot --log-level DEBUG <command>
```

### Log Files

Check logs for detailed information:
- `docker_pilot.log` - Main application log
- `docker_metrics.json` - Performance data
- `deployment_history.json` - Deployment records
- `integration-test-report.json` - Test results

## System Validation

Verify all requirements are met:

```bash
dockerpilot validate
```

Checks:
- Python version (3.9+)
- Docker connectivity
- Required Python modules
- Disk space
- Docker daemon permissions

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- Fork the repository
- Create a feature branch
- Make your changes
- Submit a pull request

## Additional Guides

Detailed documentation for specific features:

- **[Quick Deploy Guide](docs/quick-deploy.md)** - Rapid deployment with automatic cleanup
- **[Multiple Containers Operations](docs/multi-container.md)** - Managing multiple containers at once
- **[Environment Promotion Guide](docs/guides/GRAFANA_PROMOTION_GUIDE.md)** - Promoting containers between environments
- **[Blue-Green Data Migration](docs/guides/blue-green-data-migration.md)** - Data migration during deployments
- **[Sudo Setup](docs/guides/SUDO_SETUP.md)** - Configuring permissions for backups
- **[Sudo Passwordless Setup](docs/guides/SUDO_PASSWORDLESS_SETUP.md)** - Running backup flows without interactive sudo
- **[Health Checks Configuration](docs/guides/HEALTH_CHECKS_CONFIG.md)** - Customizing health check endpoints
- **[Network Searcher Tool](tools/searcher/README.md)** - Optional packet sniffer helper

## Best Practices

1. **Always test deployments** in non-production environments first
2. **Use health checks** to ensure application readiness
3. **Set resource limits** to prevent resource exhaustion
4. **Enable monitoring alerts** for production deployments
5. **Create backups** before major changes
6. **Review deployment history** to track changes
7. **Use blue-green deployments** for critical production updates
8. **Test rollback procedures** regularly
9. **Start with local or staging deployments** before promoting to production

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a list of changes and version history.

## Support

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/DozeyUDK/DockerPilot/issues)
- **Documentation**: Check the `docs/` directory
- **Logs**: Review `docker_pilot.log` for detailed error messages

---

**Version**: Enhanced v3  
**Python**: 3.9+  
**Docker**: 20.10+  
**Components**: CLI package and optional `DockerPilotExtras` web panel
