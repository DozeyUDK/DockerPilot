# Docker Pilot - Professional Container Management Tool

Docker Pilot is a comprehensive container management tool with advanced deployment capabilities, real-time monitoring, and CI/CD integration. Built as an installable Python package for seamless DevOps workflows.

## Features

### Core Container Management
- **Container Operations**: Start, stop, restart, remove, pause, unpause containers
- **Image Management**: List, build, and remove Docker images with detailed metadata
- **Real-time Monitoring**: CPU, memory, network I/O, and process tracking with trend indicators
- **Health Checks**: Automated container health validation with configurable retries
- **Interactive Dashboard**: Live metrics display with historical data tracking

### Advanced Deployment Strategies
- **Rolling Deployment**: Zero-downtime updates with automatic health checks and rollback
- **Blue-Green Deployment**: Parallel environment switching for maximum safety
- **Canary Deployment**: Gradual traffic shifting with performance monitoring

### DevOps Integration
- **CI/CD Pipeline Generation**: GitHub Actions, GitLab CI, and Jenkins configurations
- **Environment Promotion**: Automated promotion workflow (dev → staging → prod)
- **Integration Testing**: HTTP, database, and custom test framework
- **Monitoring & Alerts**: Configurable alerts with Slack/Email notifications
- **Backup & Restore**: Complete deployment state management

## Prerequisites

- Python 3.8 or higher
- Docker Engine 20.10+
- Docker daemon running and accessible

## Installation

### Install from Source

1. **Clone the repository:**
```bash
git clone https://github.com/DozeyUDK/DockerPilot.git
cd DockerPilot

Build the package:

bashpython -m build

Install the package:

Windows:
powershellpip install dist\dockerpilot-0.1.0-py3-none-any.whl
Linux/macOS:
bashpip install dist/dockerpilot-0.1.0-py3-none-any.whl

Verify installation:

bashdockerpilot validate
Dependencies
All dependencies are automatically installed with the package:

docker
pyyaml
requests
rich

Quick Start
Interactive Mode
Launch the interactive menu:
bashdockerpilot
Select from available commands:

list - List all containers
list-img - List Docker images
start/stop/restart - Container operations
monitor - Real-time monitoring dashboard
build - Build Docker image
deploy-init - Create deployment configuration
deploy-config - Deploy from configuration
And many more...

CLI Mode
Use specific commands directly:
bash# Container Management
dockerpilot container list --all
dockerpilot container start myapp
dockerpilot container stop myapp --timeout 30
dockerpilot container restart myapp
dockerpilot container remove myapp --force

# Image Management
dockerpilot container list-images --all
dockerpilot container remove-image myapp:latest --force
dockerpilot build /path/to/dockerfile myapp:latest --no-cache

# Monitoring
dockerpilot monitor myapp --duration 300
dockerpilot monitor webapp database cache --duration 600

# Deployment
dockerpilot deploy init --output deployment.yml
dockerpilot deploy config deployment.yml --type rolling
dockerpilot deploy config deployment.yml --type blue-green
dockerpilot deploy history --limit 20

# Advanced Features
dockerpilot validate
dockerpilot backup create --path ./backup
dockerpilot backup restore ./backup
dockerpilot test --config integration-tests.yml
dockerpilot promote dev staging
dockerpilot alerts --config alerts.yml
dockerpilot docs --output ./docs
Container Management
List Containers
bash# Table format (default)
dockerpilot container list --all

# JSON format for scripting
dockerpilot container list --format json
The table view displays:

Container ID and Name
Current Status (running/exited/paused)
Image information
Port mappings
Size and Uptime

Container Operations
bash# Start container
dockerpilot container start myapp

# Stop with custom timeout
dockerpilot container stop myapp --timeout 30

# Restart container
dockerpilot container restart myapp

# Remove container (with confirmation for running containers)
dockerpilot container remove myapp
dockerpilot container remove myapp --force

# Pause and unpause
dockerpilot container pause myapp
dockerpilot container unpause myapp
Container Logs and Details
Interactive mode only:
bashdockerpilot
# Choose: logs - View container logs with configurable tail
# Choose: json - View complete container configuration as JSON
Image Management
List Images
bash# Table format with metadata
dockerpilot container list-images --all

# JSON format
dockerpilot container list-images --format json
Displays:

Image ID and Repository
Tag and Size
Creation date (relative or absolute)
Number of containers using the image

Build Images
bashdockerpilot build /path/to/dockerfile myapp:latest
dockerpilot build . myapp:dev --no-cache --pull
Interactive mode:
bashdockerpilot
# Choose: build
# Follow prompts for path, tag, and build options
Remove Images
bashdockerpilot container remove-image myapp:latest
dockerpilot container remove-image myapp:old --force
Monitoring
Real-time Dashboard
Monitor all running containers:
bashdockerpilot monitor --duration 300
Monitor specific containers:
bashdockerpilot monitor webapp database cache --duration 600
Dashboard displays:

Container status with color coding
CPU usage percentage with trend indicators (↗️ ↘️ →)
Memory usage (MB and percentage)
Network I/O (download ↓ / upload ↑ in MB)
Process count (PIDs)
Container uptime

Features:

Auto-refresh every second
Historical data tracking (last 60 measurements)
Metrics saved to docker_metrics.json
Summary statistics after monitoring session
Press Ctrl+C to stop monitoring

Deployment Strategies
1. Create Deployment Configuration
bashdockerpilot deploy init --output deployment.yml
Edit the generated deployment.yml:
yamldeployment:
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
  network: 'bridge'
  cpu_limit: '1.0'
  memory_limit: '1g'

build:
  dockerfile_path: '.'
  context: '.'
  no_cache: false
  pull: true
2. Rolling Deployment (Zero-Downtime)
bashdockerpilot deploy config deployment.yml --type rolling
Process:

✅ Builds new Docker image
✅ Creates new container with temporary name
✅ Starts new container and waits for stabilization
✅ Performs comprehensive health checks
✅ Stops old container and renames new container
✅ Automatic rollback on any failure

Best for: Most production deployments, regular updates
3. Blue-Green Deployment (Maximum Safety)
bashdockerpilot deploy config deployment.yml --type blue-green
Process:

✅ Builds new image
✅ Deploys to inactive slot (blue/green)
✅ Runs parallel tests on temporary port
✅ Comprehensive health checks
✅ Zero-downtime traffic switch
✅ Old version kept for instant rollback

Best for: Critical production updates, high-availability requirements
4. Canary Deployment (Gradual Rollout)
bashdockerpilot deploy config deployment.yml --type canary
Process:

✅ Deploys canary version (5% traffic simulation)
✅ Monitors performance for 30 seconds
✅ Validates error rates (< 5% threshold)
✅ Promotes to full deployment if successful
✅ Automatic rollback if issues detected

Best for: Risk-averse deployments, testing new features
View Deployment History
bashdockerpilot deploy history --limit 20
Displays:

Deployment timestamp and ID
Deployment type (rolling/blue-green/canary)
Image and container information
Success/failure status
Deployment duration

History is saved to deployment_history.json.
CI/CD Integration
Generate Pipeline Configurations
GitHub Actions:
bashdockerpilot pipeline create --type github --output .github/workflows
Generates complete workflow with:

Automated testing
Docker image building and pushing
Deployment using Docker Pilot
Code coverage reporting

GitLab CI:
bashdockerpilot pipeline create --type gitlab
Creates .gitlab-ci.yml with:

Multi-stage pipeline (test, build, deploy)
Docker-in-Docker support
Environment-specific deployments

Jenkins:
bashdockerpilot pipeline create --type jenkins
Generates Jenkinsfile with:

Declarative pipeline syntax
Docker registry integration
Slack notifications

Environment Promotion
Promote between environments with automatic validation:
bash# Dev to Staging
dockerpilot promote dev staging --config deployment.yml

# Staging to Production
dockerpilot promote staging prod --config deployment.yml
Features:

Environment-specific resource allocation
Pre-promotion validation checks
Automated deployment with appropriate strategy
Post-promotion health validation
Automatic rollback on failure

Environment Configurations:

Dev: 1 replica, 0.5 CPU, 512Mi memory
Staging: 2 replicas, 1.0 CPU, 1Gi memory
Production: 3 replicas, 2.0 CPU, 2Gi memory

Advanced Features
Integration Testing
Create integration-tests.yml:
yamltests:
  - name: 'Health Check'
    type: 'http'
    url: 'http://localhost:8080/health'
    expected_status: 200
    timeout: 5
  
  - name: 'API Endpoint'
    type: 'http'
    method: 'POST'
    url: 'http://localhost:8080/api/status'
    expected_status: 200
    data:
      key: 'value'
  
  - name: 'Custom Script'
    type: 'custom'
    script: './tests/integration_test.py'
    timeout: 30
Run tests:
bashdockerpilot test --config integration-tests.yml
Features:

HTTP endpoint testing (GET, POST, etc.)
Custom test scripts
Detailed test reports saved to JSON
Success rate tracking

Monitoring Alerts
Setup monitoring and alerting:
bashdockerpilot alerts --config alerts.yml
Create alerts.yml:
yamlalerts:
  - name: 'high_cpu_usage'
    condition: 'cpu_percent > 80'
    duration: '5m'
    severity: 'warning'
    message: 'CPU usage is above 80% for 5 minutes'
  
  - name: 'high_memory_usage'
    condition: 'memory_percent > 85'
    duration: '3m'
    severity: 'critical'
    message: 'Memory usage critical'
  
  - name: 'container_restart'
    condition: 'container_restarts > 3'
    duration: '10m'
    severity: 'warning'
    message: 'Container restarting frequently'

notification_channels:
  - type: 'slack'
    webhook_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK'
    channel: '#alerts'
  
  - type: 'email'
    smtp_server: 'smtp.gmail.com'
    smtp_port: 587
    username: 'alerts@example.com'
    recipients: ['admin@example.com', 'devops@example.com']
Backup and Restore
Create backup:
bashdockerpilot backup create --path ./backup_20250106
Backs up:

Container configurations
Image information
Network settings
Volume definitions
Complete deployment state

Restore from backup:
bashdockerpilot backup restore ./backup_20250106
Features:

JSON format for easy inspection
Timestamped backups
Summary statistics
Selective restoration

Configuration Management
Export configuration:
bashdockerpilot config export --output docker-pilot-config.tar.gz
Exports:

All YAML configurations
Deployment history
Metrics data
Log files

Import configuration:
bashdockerpilot config import docker-pilot-config.tar.gz
Production Checklist
Generate comprehensive deployment checklist:
bashdockerpilot checklist --output production-checklist.md
Includes:

Pre-deployment tasks
During deployment monitoring
Post-deployment validation
Rollback procedures
Emergency contacts
Useful commands

Documentation Generation
Generate complete project documentation:
bashdockerpilot docs --output ./docs
Creates:

README.md - Complete user guide
API.md - API documentation
TROUBLESHOOTING.md - Common issues and solutions

Configuration Files
Docker Pilot uses several configuration files:

deployment.yml - Deployment configuration
alerts.yml - Monitoring alerts
integration-tests.yml - Test definitions
docker_pilot.log - Application logs (rotated, max 10MB)
docker_metrics.json - Performance metrics
deployment_history.json - Deployment records

Troubleshooting
Common Issues
Docker Connection Failed:
bash# Check Docker status
docker info

# Verify Docker daemon is running
sudo systemctl status docker

# Start Docker
sudo systemctl start docker

# Add user to docker group (Linux)
sudo usermod -aG docker $USER
newgrp docker
Permission Denied:
bash# Fix socket permissions (Linux)
sudo chown $USER:docker /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
Health Check Failures:

Verify endpoint exists: curl http://localhost:8080/health
Check container logs for errors
Increase health_check_timeout in deployment config
Verify application is listening on correct port

Port Already in Use:
bash# Find process using port (Linux)
sudo netstat -tulpn | grep :8080

# Find process (Windows)
netstat -ano | findstr :8080

# Change port in deployment.yml or stop conflicting service
Build Failures:

Check Dockerfile syntax
Verify build context path
Use --no-cache flag for clean build
Check available disk space

Debug Mode
Enable detailed logging:
bashdockerpilot --log-level DEBUG container list
dockerpilot --log-level DEBUG deploy config deployment.yml
Log Files
Check logs for detailed information:

docker_pilot.log - Main application log with rotation
docker_metrics.json - Real-time performance data
deployment_history.json - Complete deployment records
integration-test-report.json - Test execution results

System Validation
Verify all requirements:
bashdockerpilot validate
Checks:

✅ Python version (3.8+)
✅ Docker connectivity and version
✅ Required Python modules
✅ Disk space (>1GB)
✅ Docker daemon permissions
✅ Docker API accessibility

Best Practices

Test Deployments: Always test in non-production environments first
Use Health Checks: Configure proper health check endpoints
Set Resource Limits: Prevent resource exhaustion with CPU/memory limits
Monitor Actively: Use the monitoring dashboard during deployments
Enable Alerts: Configure alerts for production environments
Create Backups: Backup state before major changes
Review History: Track deployments via deploy history
Use Blue-Green: For critical production updates requiring instant rollback
Test Rollbacks: Regularly practice rollback procedures
Document Changes: Maintain deployment notes and changelogs

Architecture
Package Structure
DockerPilot/
├── src/
│   └── dockerpilot/
│       ├── __init__.py
│       ├── __main__.py
│       ├── main.py
│       └── pilot.py          # Core implementation
├── dist/                      # Built packages
├── build/                     # Build artifacts
├── setup.py                   # Package configuration
└── README.md
Key Components

DockerPilotEnhanced: Main class with all functionality
DeploymentConfig: Dataclass for deployment parameters
ContainerStats: Dataclass for container metrics
LogLevel: Enum for logging configuration

Development
Building from Source
bash# Install build tools
pip install build

# Build package
python -m build

# Install in development mode
pip install -e .
Running Tests
bashpip install pytest pytest-cov
pytest tests/ --cov=dockerpilot
Support

Issues: GitHub Issues
Documentation: Check the docs/ directory after running dockerpilot docs
Logs: Review docker_pilot.log for detailed error messages

License
This tool is provided as-is for container management and deployment automation.

Version: 0.1.0
Python: 3.8+
Docker: 20.10+
Author: dozey
Repository: https://github.com/DozeyUDK/DockerPilot
