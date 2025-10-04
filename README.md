# Docker Pilot Enhanced - User Guide

Docker container management tool with advanced deployment capabilities, real-time monitoring, and CI/CD integration.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Container Management](#container-management)
- [Image Management](#image-management)
- [Monitoring](#monitoring)
- [Deployment Strategies](#deployment-strategies)
- [CI/CD Integration](#cicd-integration)
- [Configuration](#configuration)
- [Advanced Features](#advanced-features)
- [Troubleshooting](#troubleshooting)

## Features

### Core Capabilities
- **Container Operations**: Start, stop, restart, remove, pause, unpause containers
- **Image Management**: List, build, and remove Docker images
- **Real-time Monitoring**: CPU, memory, network I/O, and process tracking
- **Health Checks**: Automated container health validation
- **Interactive Dashboard**: Live metrics with trend indicators

### Advanced Deployment
- **Rolling Deployment**: Zero-downtime updates with automatic rollback
- **Blue-Green Deployment**: Parallel environment switching for maximum safety
- **Canary Deployment**: Gradual traffic shifting with performance monitoring

### DevOps Integration
- GitHub Actions, GitLab CI, and Jenkins pipeline generation
- Environment promotion (dev → staging → prod)
- Integration testing framework
- Monitoring and alerting system
- Backup and restore functionality

## Installation

### Prerequisites
- Python 3.8 or higher
- Docker Engine 20.10+
- Docker daemon running and accessible

### Install Dependencies

```bash
pip install docker pyyaml requests rich
```

### Optional Dependencies

```bash
# For Git integration
pip install GitPython

# For testing
pip install pytest pytest-cov
```

### Verify Installation

```bash
python dockerpilotv3.py validate
```

## Quick Start

### Interactive Mode

Run without arguments to enter interactive mode:

```bash
python dockerpilotv3.py
```

Select from available commands:
- `list` - List all containers
- `start` - Start a container
- `stop` - Stop a container
- `monitor` - Real-time monitoring
- `deploy-init` - Create deployment config
- And many more...

### CLI Mode

Use specific commands directly:

```bash
# List all containers
python dockerpilotv3.py container list --all

# Monitor containers
python dockerpilotv3.py monitor myapp --duration 300

# Deploy application
python dockerpilotv3.py deploy config deployment.yml --type rolling
```

## Container Management

### List Containers

```bash
# Table format (default)
python dockerpilotv3.py container list --all

# JSON format
python dockerpilotv3.py container list --format json
```

### Container Operations

```bash
# Start container
python dockerpilotv3.py container start myapp

# Stop container (with timeout)
python dockerpilotv3.py container stop myapp --timeout 30

# Restart container
python dockerpilotv3.py container restart myapp

# Remove container
python dockerpilotv3.py container remove myapp --force

# Pause/Unpause
python dockerpilotv3.py container pause myapp
python dockerpilotv3.py container unpause myapp
```

### View Container Logs

Interactive mode:
```bash
python dockerpilotv3.py
# Choose: logs
```

### View Container Details (JSON)

Interactive mode:
```bash
python dockerpilotv3.py
# Choose: json
# Enter container name
```

## Image Management

### List Images

```bash
# Table format
python dockerpilotv3.py container list-images --all

# JSON format
python dockerpilotv3.py container list-images --format json
```

### Build Images

```bash
python dockerpilotv3.py build /path/to/dockerfile myapp:latest --no-cache
```

Interactive mode:
```bash
python dockerpilotv3.py
# Choose: build
# Follow prompts
```

### Remove Images

```bash
python dockerpilotv3.py container remove-image myapp:latest --force
```

## Monitoring

### Real-time Dashboard

Monitor all running containers:
```bash
python dockerpilotv3.py monitor --duration 300
```

Monitor specific containers:
```bash
python dockerpilotv3.py monitor webapp database cache --duration 600
```

The dashboard displays:
- Container status
- CPU usage with trend indicators (↗️ ↘️ →)
- Memory usage and percentage
- Network I/O (download/upload)
- Process count (PIDs)
- Uptime

Metrics are automatically saved to `docker_metrics.json`.

## Deployment Strategies

### 1. Create Deployment Configuration

```bash
python dockerpilotv3.py deploy init --output deployment.yml
```

Edit `deployment.yml`:

```yaml
deployment:
  image_tag: 'myapp:latest'
  container_name: 'myapp'
  port_mapping:
    '8080': '8080'
  environment:
    ENV: 'production'
    DEBUG: 'false'
  volumes:
    './data': '/app/data'
  restart_policy: 'unless-stopped'
  health_check_endpoint: '/health'
  health_check_timeout: 30
  health_check_retries: 10
  cpu_limit: '1.0'
  memory_limit: '1g'

build:
  dockerfile_path: '.'
  context: '.'
  no_cache: false
  pull: true
```

### 2. Rolling Deployment (Zero-Downtime)

```bash
python dockerpilotv3.py deploy config deployment.yml --type rolling
```

**Process:**
1. Builds new image
2. Creates new container with temporary name
3. Performs health checks
4. Switches traffic (stops old, renames new)
5. Automatic rollback on failure

### 3. Blue-Green Deployment (Safest)

```bash
python dockerpilotv3.py deploy config deployment.yml --type blue-green
```

**Process:**
1. Builds new image
2. Deploys to inactive slot (blue/green)
3. Runs parallel tests
4. Health checks on new deployment
5. Zero-downtime traffic switch
6. Keeps old version for instant rollback

### 4. Canary Deployment (Gradual Rollout)

```bash
python dockerpilotv3.py deploy config deployment.yml --type canary
```

**Process:**
1. Deploys canary version (5% traffic)
2. Monitors performance for 30 seconds
3. Validates error rates
4. Promotes to full deployment if successful
5. Automatic rollback if issues detected

### View Deployment History

```bash
python dockerpilotv3.py deploy history --limit 20
```

## CI/CD Integration

### Generate Pipeline Configurations

**GitHub Actions:**
```bash
python dockerpilotv3.py pipeline create --type github --output .github/workflows
```

**GitLab CI:**
```bash
python dockerpilotv3.py pipeline create --type gitlab
```

**Jenkins:**
```bash
python dockerpilotv3.py pipeline create --type jenkins
```

### Environment Promotion

Promote from dev to staging:
```bash
python dockerpilotv3.py promote dev staging --config deployment.yml
```

Promote to production:
```bash
python dockerpilotv3.py promote staging prod --config deployment.yml
```

**Features:**
- Environment-specific resource allocation
- Automated pre-promotion checks
- Post-promotion validation
- Rollback on failure

## Configuration

### Logging Levels

```bash
python dockerpilotv3.py --log-level DEBUG container list
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

Export all configs:
```bash
python dockerpilotv3.py config export --output backup.tar.gz
```

Import configs:
```bash
python dockerpilotv3.py config import backup.tar.gz
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
python dockerpilotv3.py test --config integration-tests.yml
```

### Monitoring Alerts

Setup alerts:
```bash
python dockerpilotv3.py alerts --config alerts.yml
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
python dockerpilotv3.py backup create --path ./backup_20240104
```

Restore from backup:
```bash
python dockerpilotv3.py backup restore ./backup_20240104
```

Backups include:
- Container configurations
- Image information
- Network settings
- Volume definitions

### Production Checklist

Generate deployment checklist:
```bash
python dockerpilotv3.py checklist --output production-checklist.md
```

### Documentation Generation

Generate complete documentation:
```bash
python dockerpilotv3.py docs --output ./docs
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
python dockerpilotv3.py --log-level DEBUG <command>
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
python dockerpilotv3.py validate
```

Checks:
- Python version (3.8+)
- Docker connectivity
- Required Python modules
- Disk space
- Docker daemon permissions

## Support

- **Issues**: Report bugs or request features via GitHub Issues
- **Documentation**: Check the `docs/` directory
- **Logs**: Review `docker_pilot.log` for detailed error messages

## Best Practices

1. **Always test deployments** in non-production environments first
2. **Use health checks** to ensure application readiness
3. **Set resource limits** to prevent resource exhaustion
4. **Enable monitoring alerts** for production deployments
5. **Create backups** before major changes
6. **Review deployment history** to track changes
7. **Use blue-green deployments** for critical production updates
8. **Test rollback procedures** regularly

## License

This tool is provided as-is for container management and deployment automation.

---

**Version**: Enhanced v3  
**Python**: 3.8+  
**Docker**: 20.10+
