# Guide: Promoting Containers to Production with DockerPilot

## What does promoting a container to production look like?

### 1. Configuration Preparation

First, you need to prepare a deployment configuration file (e.g., `myapp-deployment.yml`):

```bash
# Create deployment configuration
dockerpilot deploy init --output myapp-deployment.yml

# Edit configuration as needed
nano myapp-deployment.yml
```

### 2. Promotion Process

#### Example scenario:
```
dev → staging → prod
```

#### Promotion commands:

**From dev to staging:**
```bash
dockerpilot promote dev staging --config myapp-deployment.yml
```

**From staging to production:**
```bash
dockerpilot promote staging prod --config myapp-deployment.yml
```

**Or directly to production (if you don't have staging):**
```bash
# First you would need to create 'dev' or 'staging' environment
dockerpilot promote staging prod --config myapp-deployment.yml
```

### 3. What happens during promotion?

#### Automatic resource adjustments by environment:

| Environment | CPU | Memory | Replicas | Image Suffix |
|------------|-----|--------|----------|--------------|
| **dev**    | 0.5 | 512Mi  | 1       | `-dev`        |
| **staging**| 1.0 | 1Gi    | 2       | `-staging`    |
| **prod**   | 2.0 | 2Gi    | 3       | (no suffix)  |

#### Promotion process (steps):

1. **Pre-promotion checks** (before promotion):
   - Check source environment health
   - Verify target environment readiness
   - Check if all required tests passed
   - Verify no blocking issues in monitoring

2. **Deployment strategy**:
   - For **production**: `blue-green deployment` (safest)
   - For **staging**: `rolling deployment` (zero-downtime)
   - Automatic switching between old and new environment

3. **Post-promotion validation** (after promotion):
   - Check health check endpoint (`/api/health`)
   - Verify all services are running correctly
   - Check performance metrics
   - Verify no errors in logs

### 4. Example command output

```bash
$ dockerpilot promote staging prod --config myapp-deployment.yml

╭──────────────────────────── Docker Managing Tool ────────────────────────────╮
│                                                                              │
│   _____             _             _____ _ _       _                          │
│  |  __ \           | |           |  __ (_) |     | |                         │
│  | |  | | ___   ___| | _____ _ __| |__) || | ___ | |_                        │
│  | |  | |/ _ \ / __| |/ / _ \ '__|  ___/ | |/ _ \| __|                       │
│  | |__| | (_) | (__|   <  __/ |  | |   | | | (_) | |_                        │
│  |_____/ \___/ \___|_|\_\___|_|  |_|   |_|_|\___/ \__|                       │
│                                                                              │
│          by Dozey                                                            │
│                                                                              │
╰──────────────────────────────────────────────────────────────────────────────╯

Promoting from staging to prod...

OK Source environment (staging) is healthy
OK Target environment (prod) is ready
OK All required tests have passed
OK No blocking issues in monitoring systems

REFRESH Starting Blue-Green Deployment...
  [████████████████████████████████] 100% - Building green environment...
  
OK Green environment ready
REFRESH Switching traffic to green...
OK Traffic switched successfully
REFRESH Removing old blue environment...

OK Application is responding to health checks
OK All services are running correctly
OK Performance metrics are within acceptable ranges
OK No error spikes in logs

OK Successfully promoted to prod
```

### 5. Configuration Example

#### Resource changes during promotion to prod:
- **CPU**: increased to 2.0 (from 1.0 in staging)
- **Memory**: increased to 2Gi (from 1Gi in staging)
- **Replicas**: increased to 3 (from 2 in staging)
- **Strategy**: Blue-Green deployment (safest for production)

#### Example deployment.yml configuration:
```yaml
deployment:
  image_tag: 'myapp:latest'
  container_name: 'myapp'
  port_mapping: {'8080': '8080'}
  environment:
    ENV: 'production'
  volumes:
    'app_data': '/app/data'
  restart_policy: 'unless-stopped'
  health_check_endpoint: '/health'
  health_check_timeout: 30
  health_check_retries: 10
  network: 'bridge'
  cpu_limit: '2.0'
  memory_limit: '2Gi'
```

### 6. What to check before promotion?

1. Is the container running correctly in the source environment
2. Is data backed up (volume `app_data`)
3. Is configuration correct (especially passwords and environment variables)
4. Is port 8080 available in target environment
5. Do you have sufficient resources (CPU, memory) for target environment

### 7. Rollback

If something goes wrong, DockerPilot automatically:
- Detects problems during health check
- Rolls back changes
- Restores previous version

### 8. Monitoring

After promotion you can monitor the container:
```bash
# Monitor container
dockerpilot monitor myapp --duration 300

# Check deployment history
dockerpilot deploy history --limit 10
```

## Notes

- **IMPORTANT**: Make sure passwords and environment variables are properly configured for production
- **Backup**: Always backup data before promoting to production (volume `app_data`)
- **Testing**: Best to test promotion on staging first
- **Monitoring**: Monitor container for several hours after promotion

## Related Commands

```bash
# List containers
dockerpilot container list --all

# Container details
dockerpilot container inspect myapp

# Container logs
dockerpilot container logs myapp

# Deployment history
dockerpilot deploy history
```
