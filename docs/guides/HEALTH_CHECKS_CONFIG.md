# Health Check Configuration

DockerPilot automatically detects the correct health check endpoint based on the Docker image being deployed. This configuration is **fully customizable**.

## How It Works

1. **Default mappings**: DockerPilot loads defaults from `src/dockerpilot/configs/health-checks-defaults.json`
2. **User config**: You can override defaults in your YAML config file
3. **Auto-detection**: DockerPilot checks the image name (e.g., `ollama/ollama`, `qdrant/qdrant`) and selects the appropriate endpoint
4. **Per-container override**: Each deployment config can still manually override the health check endpoint

**Priority (highest to lowest):**
1. Manual override in `deployment-*.yml` → `health_check_endpoint: /custom`
2. User config in `docker-pilot-config.yml` → `health_checks.endpoint_mappings`
3. Defaults from `health-checks-defaults.json`

## Default Mappings

### Non-HTTP Services (No Health Check)
These services don't use HTTP, so health checks are skipped:
- SSH servers (`ssh`)
- Redis (`redis`)
- Databases (`mariadb`, `mysql`, `postgresql`, `mongodb`, `db2`)
- Message queues (`rabbitmq`, `kafka`)

### HTTP Services with Custom Endpoints

| Image Pattern | Health Check Endpoint | Example Image |
|---------------|----------------------|---------------|
| `qdrant` | `/healthz` | `qdrant/qdrant:latest` |
| `ollama` | `/api/version` | `ollama/ollama:latest` |
| `influxdb` | `/ready` | `influxdb:latest` |
| `grafana` | `/api/health` | `grafana/grafana:latest` |
| `homeassistant` | `/` | `homeassistant/home-assistant:2024.10.0` |
| `prometheus` | `/-/healthy` | `prom/prometheus:latest` |
| `nextcloud` | `/status.php` | `nextcloud:latest` |
| `elasticsearch` | `/_cluster/health` | `elasticsearch:8.0` |

**Default for all other HTTP services**: `/health`

## Update Defaults (No Code Changes!)

You can update the default mappings by editing the JSON file:

```bash
# Edit defaults for all users
nano src/dockerpilot/configs/health-checks-defaults.json
```

```json
{
  "health_checks": {
    "non_http_services": [
      "ssh",
      "redis",
      "your-custom-tcp-service"
    ],
    "endpoint_mappings": {
      "ollama": "/api/version",
      "your-custom-app": "/health/check"
    },
    "default_endpoint": "/health"
  }
}
```

**Benefits:**
- ✅ No hardcoded values in Python code
- ✅ Easy to update (just edit JSON)
- ✅ Can be version controlled
- ✅ Can be downloaded from URL (future feature)

## Customize Health Checks

### Option 1: Global Config File

Create a config file with your custom mappings:

```yaml
# docker-pilot-config.yml
health_checks:
  # Add custom non-HTTP services
  non_http_services:
    - ssh
    - redis
    - myCustomTCPService
  
  # Add custom endpoint mappings
  endpoint_mappings:
    # Your custom services
    my-custom-app: /api/health
    another-service: /healthz
    
    # Override defaults
    ollama: /custom/health  # Override default /api/version
    
  # Change default endpoint
  default_endpoint: /api/status
```

Use it with DockerPilot:

```bash
dockerpilot --config docker-pilot-config.yml deploy config deployment.yml
```

### Option 2: Per-Container Override

Override health check in your deployment config:

```yaml
# deployment-prod.yml
deployment:
  container_name: linuxlite-ssh
  image_tag: linux-lite-ssh-linuxlite:latest
  health_check_endpoint: null  # ❌ Skip HTTP health check (non-HTTP service)
  # ... rest of config
```

```yaml
# deployment-prod.yml
deployment:
  container_name: my-api
  image_tag: my-custom-api:v1.2
  health_check_endpoint: /custom/healthcheck  # ✅ Custom endpoint
  # ... rest of config
```

## Use Cases

### 1. Custom API with Non-Standard Endpoint

```yaml
health_checks:
  endpoint_mappings:
    my-api: /admin/health-status
```

### 2. Skip Health Checks for Development

```yaml
health_checks:
  # Treat all services as non-HTTP (skip all health checks)
  non_http_services:
    - '*'  # Skip all
```

### 3. Legacy Application

```yaml
health_checks:
  endpoint_mappings:
    legacy-app: /ping.jsp
```

## Environment Promotion

Health check configuration is **preserved during environment promotion**:

```bash
# Promote from DEV to STAGING
dockerpilot promote dev staging --config deployment-dev.yml
```

The health check endpoint will be automatically detected for the target environment based on the image.

## Troubleshooting

### Health Check Failing?

1. **Check the logs**:
   ```bash
   docker logs <container_name>
   ```

2. **Verify endpoint manually**:
   ```bash
   curl http://localhost:<port>/health
   ```

3. **Add custom mapping** if the default is wrong:
   ```yaml
   health_checks:
     endpoint_mappings:
       my-container: /correct/endpoint
   ```

4. **Skip health check** for non-HTTP services:
   ```yaml
   deployment:
     health_check_endpoint: null
   ```

## Examples

### SSH Server (No Health Check)

```yaml
deployment:
  container_name: ssh-server
  image_tag: linuxserver/openssh-server:latest
  # Health check auto-skipped (detected as SSH)
```

### Custom Monitoring Dashboard

```yaml
# config.yml
health_checks:
  endpoint_mappings:
    my-dashboard: /api/v1/health

# deployment.yml
deployment:
  container_name: monitoring
  image_tag: my-dashboard:latest
  # Will use /api/v1/health automatically
```

### Multiple Prometheus Instances

```yaml
health_checks:
  endpoint_mappings:
    prometheus: /-/healthy
    prometheus-dev: /-/ready  # Different endpoint for dev version
```

## Best Practices

1. ✅ **Use image patterns** (not container names) in mappings
2. ✅ **Test health checks** before deploying to production
3. ✅ **Document custom endpoints** in your team's config file
4. ✅ **Version control** your health check config alongside deployments
5. ❌ **Don't hardcode** health checks in container images - use config

## Related Documentation

- [Environment Promotion](./GRAFANA_PROMOTION_GUIDE.md)
- [Deployment Configurations](./src/dockerpilot/configs/)
- [Sudo Setup for Backups](./SUDO_SETUP.md)

