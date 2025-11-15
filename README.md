# Docker Pilot
<img width="2667" height="465" alt="image" src="https://github.com/user-attachments/assets/c6ae8332-7e0e-4588-b014-ad9a90992087" />

**Docker container management tool with advanced deployment capabilities, real-time monitoring, and CI/CD integration.**

## Quick Install (One-Click)

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
- Installs Docker Pilot with all dependencies
- Sets up `dockerpilot` command globally
- Verifies installation

**Prerequisites:**
- Python 3.9+ 
- Docker 20.10+ (with daemon running)

## Quick Start

After installation:
```bash
# Interactive mode
dockerpilot

# Or use CLI commands
dockerpilot container list --all
dockerpilot monitor --duration 300
dockerpilot deploy config deployment.yml --type rolling
```

## Manual Installation

```bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot
pip install -e .
```

**Optional extras:**
```bash
pip install -e .[git]  # Git integration for CI/CD
pip install -e .[test] # Development dependencies
```

**Verify installation:**
```bash
dockerpilot --help
dockerpilot validate  # Check system requirements
```

---

## Two Versions Available

### Full Version (Default)
- **Advanced features**: Rolling/Blue-Green/Canary deployments, CI/CD pipelines, monitoring alerts, backup/restore  
- **Best for**: Production, DevOps teams, advanced workflows

### Lite Version
- **Core features**: Container management, image operations, basic monitoring  
- **Best for**: Development, learning, simple use cases

**Note:** The installer installs the Full version by default. Both versions share the same `dockerpilot` command.

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

### Core Capabilities (Both Versions)
- **Container Operations**: Start, stop, restart, remove, pause, unpause, exec into containers
- **Image Management**: List, build, and remove Docker images
- **Real-time Monitoring**: CPU, memory, network I/O, and process tracking
- **Health Checks**: Automated container health validation
- **Interactive Dashboard**: Live metrics with trend indicators
- **Container Shell Access**: Execute interactive bash/sh sessions inside running containers

### Advanced Deployment (Full Version Only)
- **Rolling Deployment**: Zero-downtime updates with automatic rollback
- **Blue-Green Deployment**: Parallel environment switching for maximum safety
- **Canary Deployment**: Gradual traffic shifting with performance monitoring

### DevOps Integration (Full Version Only)
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
- `deploy-init` - Create deployment config (Full version only)
- And many more...

### Usage

After installation, the `dockerpilot` command is available globally:

```bash
# Interactive mode
dockerpilot

# CLI commands
dockerpilot container list --all
dockerpilot monitor myapp --duration 300
dockerpilot deploy config deployment.yml --type rolling

# Get help
dockerpilot --help
```

If `pip install -e .` was used, the command is automatically available. Otherwise, you can run:

```bash
python -m dockerpilot
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

> **Note:** CI/CD features are only available in the full version (`pilot.py`)

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

- `deployment.yml` - Deployment configuration (Full version)
- `alerts.yml` - Monitoring alerts (Full version)
- `integration-tests.yml` - Test definitions (Full version)
- `docker_pilot.log` - Application logs
- `docker_metrics.json` - Performance metrics
- `deployment_history.json` - Deployment records (Full version)

### Export/Import Configuration

```bash
# Export all configs (Full version)
dockerpilot config export --output backup.tar.gz

# Import configs (Full version)
dockerpilot config import backup.tar.gz
```

## Advanced Features

> **Note:** Advanced features are only available in the full version (`pilot.py`)

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
- `deployment_history.json` - Deployment records (Full version)
- `integration-test-report.json` - Test results (Full version)

## System Validation

Verify all requirements are met:

```bash
dockerpilot validate
```

Checks:
- Python version (3.8+)
- Docker connectivity
- Required Python modules
- Disk space
- Docker daemon permissions

## Choosing Between Full and Lite Version

### Use Docker Pilot (Full Version) when you need:
- Production-grade deployments with zero downtime
- Advanced deployment strategies (Rolling, Blue-Green, Canary)
- CI/CD pipeline integration
- Environment promotion workflows
- Integration testing
- Monitoring alerts
- Backup and restore capabilities

### Use Docker Pilot Lite when you need:
- Quick container management during development
- Simple deployment scenarios
- Lower resource footprint
- Faster startup time
- Learning Docker basics
- Minimal dependencies

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- Fork the repository
- Create a feature branch
- Make your changes
- Submit a pull request

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a list of changes and version history.

## Support

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/DozeyUDK/DockerPilot/issues)
- **Documentation**: Check the `docs/` directory
- **Logs**: Review `docker_pilot.log` for detailed error messages

## Additional Guides

Detailed documentation for specific features:

- **[Quick Deploy Guide](docs/quick-deploy.md)** - Rapid deployment with automatic cleanup
- **[Multiple Containers Operations](docs/multi-container.md)** - Managing multiple containers at once

## Best Practices

1. **Always test deployments** in non-production environments first
2. **Use health checks** to ensure application readiness
3. **Set resource limits** to prevent resource exhaustion
4. **Enable monitoring alerts** for production deployments (Full version)
5. **Create backups** before major changes (Full version)
6. **Review deployment history** to track changes (Full version)
7. **Use blue-green deployments** for critical production updates (Full version)
8. **Test rollback procedures** regularly
9. **Start with Lite version** for learning, upgrade to Full version for production

## License

This tool is provided as-is for container management and deployment automation.

---

**Version**: Enhanced v3  
**Python**: 3.8+  
**Docker**: 20.10+  
**Available in**: Full (`pilot.py`) and Lite (`dockerpilot-lite.py`) versions
