# Quick Deploy - User Guide

## Description

The `quick_deploy` function enables rapid deployment of new application versions with automatic management of the entire Docker container and image lifecycle.

## What Does Quick Deploy Do?

1. ✅ **Checks** current container and image
2. 🔨 **Builds** new image from Dockerfile
3. 🛑 **Stops** old container
4. 🗑️ **Removes** old container
5. 🧹 **Removes** old Docker image (if not used by other containers)
6. 🚀 **Starts** new container
7. 🩺 **Checks** health check (optional)

## CLI Usage

### Basic usage

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.2 \
  --container-name myapp \
  --port 80:8080
```

### Full usage with all options

```bash
dockerpilot deploy quick \
  --dockerfile-path ./app \
  --image-tag myapp:v1.2 \
  --container-name myapp \
  --port 80:8080 \
  --env NODE_ENV=production \
  --env DATABASE_URL=postgresql://localhost:5432/mydb \
  --volume /host/data:/app/data \
  --volume /host/logs:/app/logs
```

### Usage with YAML file

```bash
dockerpilot deploy quick \
  --yaml-config quick-deploy.yml \
  --image-tag myapp:v1.2 \
  --container-name myapp
```

### Without removing old image

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.2 \
  --container-name myapp \
  --port 80:8080 \
  --no-cleanup
```

## Interactive Menu Usage

1. Run DockerPilot:
   ```bash
   dockerpilot
   ```

2. Select: `quick-deploy`

3. Answer the questions:
   - Path to Dockerfile (default: `.`)
   - Image tag (e.g., `myapp:v1.2`)
   - Container name
   - Use YAML file? (optional)
   - Port mapping (e.g., `80:8080`)
   - Environment variables (optional)
   - Volume mapping (optional)
   - Remove old image? (default: yes)

## YAML Configuration

Create a `quick-deploy.yml` file:

```yaml
# Required fields
image_tag: "myapp:v1.0"
container_name: "myapp"

# Ports (optional)
port_mapping:
  "80": "8080"
  "443": "8443"

# Environment variables (optional)
environment:
  NODE_ENV: "production"
  DATABASE_URL: "postgresql://localhost:5432/mydb"
  API_KEY: "secret-key"

# Volumes (optional)
volumes:
  "/host/data": 
    bind: "/app/data"
    mode: "rw"
  "/host/logs": 
    bind: "/app/logs"
    mode: "rw"
```

Then run:

```bash
dockerpilot deploy quick --yaml-config quick-deploy.yml
```

## Programmatic Usage (Python)

```python
from dockerpilot.pilot import DockerPilotEnhanced

pilot = DockerPilotEnhanced()

# Basic usage
success = pilot.quick_deploy(
    dockerfile_path=".",
    image_tag="myapp:v1.2",
    container_name="myapp",
    port_mapping={"80": "8080"}
)

# With full configuration
success = pilot.quick_deploy(
    dockerfile_path="./app",
    image_tag="myapp:v1.2",
    container_name="myapp",
    port_mapping={"80": "8080", "443": "8443"},
    environment={
        "NODE_ENV": "production",
        "DATABASE_URL": "postgresql://localhost:5432/mydb"
    },
    volumes={
        "/host/data": {"bind": "/app/data", "mode": "rw"},
        "/host/logs": {"bind": "/app/logs", "mode": "rw"}
    },
    cleanup_old_image=True
)

if success:
    print("✅ Deployment successful!")
else:
    print("❌ Deployment failed!")
```

## Safety Features

### Automatic checking before removing image

Quick Deploy automatically:
- ✅ Checks if old image differs from new one
- ✅ Checks if other containers are using the old image
- ✅ Removes old image **only** if not in use
- ✅ Reports cleanup status

### Example messages

```
✅ Old image removed
ℹ️ Old image already removed
⚠️ Old image in use by 2 other container(s)
⚠️ Old image used by other containers
ℹ️ Same image, no cleanup needed
```

## Example Scenarios

### Scenario 1: First deployment

```bash
# No old container/image
dockerpilot deploy quick \
  --image-tag myapp:v1.0 \
  --container-name myapp \
  --port 80:8080

# Output:
# ℹ️ No existing container (first deployment)
# ✅ Image myapp:v1.0 built successfully
# 🚀 New container started
# ✅ Health check passed
```

### Scenario 2: Application update

```bash
# Existing myapp container with myapp:v1.0 image
dockerpilot deploy quick \
  --image-tag myapp:v1.1 \
  --container-name myapp \
  --port 80:8080

# Output:
# ✅ Found existing container (image: myapp:v1.0)
# ✅ Image myapp:v1.1 built successfully
# ✅ Old container stopped
# ✅ Old container removed
# ✅ Old image removed
# 🚀 New container started
# ✅ Health check passed
```

### Scenario 3: Same tag (rebuild)

```bash
# Rebuild same tag
dockerpilot deploy quick \
  --image-tag myapp:latest \
  --container-name myapp \
  --port 80:8080

# Output:
# ✅ Found existing container (image: myapp:latest)
# ✅ Image myapp:latest built successfully
# ✅ Old container stopped
# ✅ Old container removed
# ℹ️ Same image, no cleanup needed (or new build)
# 🚀 New container started
```

### Scenario 4: Image used by other containers

```bash
# Old image is used by other containers
dockerpilot deploy quick \
  --image-tag myapp:v2.0 \
  --container-name myapp \
  --port 80:8080

# Output:
# ✅ Found existing container (image: myapp:v1.0)
# ✅ Image myapp:v2.0 built successfully
# ✅ Old container stopped
# ✅ Old container removed
# ⚠️ Old image used by 2 other container(s)
# 🚀 New container started
```

## Troubleshooting

### Problem: "Dockerfile not found"

**Solution:**
```bash
# Make sure the path is correct
dockerpilot deploy quick \
  --dockerfile-path /path/to/dockerfile/directory \
  --image-tag myapp:v1.0 \
  --container-name myapp
```

### Problem: "Port already in use"

**Solution:**
```bash
# Check if another container is using this port
docker ps
# Stop conflicting container
docker stop conflicting_container
```

### Problem: "Health check failed"

**Note:** Container will still be running, only the health check didn't pass.

**Solution:**
- Check logs: `docker logs myapp`
- Check if application is listening on the correct port
- Check health check endpoint

### Problem: "Cannot remove old image"

**Causes:**
- Image is used by other containers ✅ This is OK
- Docker API error - check logs

## Comparison with Other Deployment Methods

| Feature | Quick Deploy | Rolling | Blue-Green | Canary |
|---------|--------------|---------|------------|--------|
| Speed | ⚡⚡⚡ | ⚡⚡ | ⚡ | ⚡ |
| Zero-downtime | ❌ | ✅ | ✅ | ✅ |
| Automatic image cleanup | ✅ | ❌ | ❌ | ❌ |
| Rollback | Manual | Automatic | Automatic | Automatic |
| Best for | Development, Testing | Production | Production | Production |
| Health checks | Basic | Advanced | Advanced | Advanced |

## When to Use Quick Deploy?

✅ **Use Quick Deploy when:**
- Developing application locally
- Testing new features
- Don't require zero-downtime
- Want fast deployment
- Need automatic cleanup of old images

❌ **DON'T use Quick Deploy when:**
- Deploying to production (use `blue-green` or `rolling`)
- Need zero-downtime
- Need automatic rollback
- Require parallel testing

## Additional Information

### Deployment History

All deployments are saved in history:

```bash
dockerpilot deploy history --limit 10
```

### System Validation

Before first use, check requirements:

```bash
dockerpilot validate
```

### Logs

All operations are logged to:
- `docker_pilot.log` - detailed logs
- `deployment_history.json` - deployment history

## Real-World Examples

### Example 1: Node.js application

```bash
# Dockerfile in current directory
dockerpilot deploy quick \
  --image-tag nodeapp:v1.0 \
  --container-name nodeapp \
  --port 3000:3000 \
  --env NODE_ENV=production \
  --env PORT=3000
```

### Example 2: Python Flask with database

```yaml
# quick-deploy.yml
image_tag: "flaskapp:v2.0"
container_name: "flaskapp"

port_mapping:
  "5000": "5000"

environment:
  FLASK_ENV: "production"
  DATABASE_URL: "postgresql://postgres:password@db:5432/mydb"
  SECRET_KEY: "super-secret-key"

volumes:
  "./uploads": 
    bind: "/app/uploads"
    mode: "rw"
```

```bash
dockerpilot deploy quick --yaml-config quick-deploy.yml
```

### Example 3: React application with Nginx

```bash
# Build React app and deploy with Nginx
dockerpilot deploy quick \
  --dockerfile-path ./frontend \
  --image-tag react-app:prod \
  --container-name react-app \
  --port 80:80 \
  --volume ./nginx.conf:/etc/nginx/nginx.conf
```

## Support

If you need help:
1. Check logs: `cat docker_pilot.log`
2. View history: `dockerpilot deploy history`
3. Validate system: `dockerpilot validate`
4. Check documentation: `dockerpilot --help`

## Changelog

### v1.0 (2024)
- ✨ Added `quick_deploy` function
- ✨ Automatic removal of old images
- ✨ Support for YAML files
- ✨ Integration with CLI and interactive menu
- ✨ Advanced checking before image removal
