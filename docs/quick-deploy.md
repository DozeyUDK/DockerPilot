# Quick Deploy Guide

## Overview

Quick Deploy enables rapid deployment of Docker containers with automatic image building and cleanup.

## Basic Usage

### CLI

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.0 \
  --container-name myapp \
  --port 80:8080
```

### With Environment Variables

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.0 \
  --container-name myapp \
  --port 80:8080 \
  --env NODE_ENV=production \
  --env DATABASE_URL=postgresql://localhost:5432/db
```

### Using YAML Configuration

Create `quick-deploy.yml`:

```yaml
image_tag: "myapp:v1.0"
container_name: "myapp"

port_mapping:
  "80": "8080"

environment:
  NODE_ENV: "production"
  DATABASE_URL: "postgresql://localhost:5432/mydb"

volumes:
  "/host/data": 
    bind: "/app/data"
    mode: "rw"
```

Deploy with:

```bash
dockerpilot deploy quick --yaml-config quick-deploy.yml
```

## What Quick Deploy Does

1. ✅ Checks existing container and image
2. 🔨 Builds new image from Dockerfile
3. 🛑 Stops old container
4. 🗑️ Removes old container
5. 🧹 Removes old image (if not used elsewhere)
6. 🚀 Starts new container
7. 🩺 Performs health check

## Interactive Mode

```bash
dockerpilot
# Select: quick-deploy
```

Follow the prompts to configure your deployment.

## Common Scenarios

### First Deployment

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.0 \
  --container-name myapp \
  --port 80:8080
```

### Update Existing Application

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.1 \
  --container-name myapp \
  --port 80:8080
```

Quick Deploy will automatically stop and remove the old container and image.

### Skip Old Image Cleanup

```bash
dockerpilot deploy quick \
  --image-tag myapp:v1.0 \
  --container-name myapp \
  --port 80:8080 \
  --no-cleanup
```

## When to Use Quick Deploy

✅ **Use Quick Deploy for:**
- Local development
- Testing new features
- Simple deployments
- Fast iterations

❌ **Don't use Quick Deploy for:**
- Production environments (use `blue-green` or `rolling` deployment)
- Zero-downtime requirements
- Complex rollback scenarios

## Examples

### Node.js Application

```bash
dockerpilot deploy quick \
  --image-tag nodeapp:v1.0 \
  --container-name nodeapp \
  --port 3000:3000 \
  --env NODE_ENV=production
```

### Python Flask with Volume

```bash
dockerpilot deploy quick \
  --image-tag flaskapp:v2.0 \
  --container-name flaskapp \
  --port 5000:5000 \
  --volume ./uploads:/app/uploads
```

## Troubleshooting

**Dockerfile not found:**
```bash
dockerpilot deploy quick \
  --dockerfile-path /path/to/dockerfile \
  --image-tag myapp:v1.0 \
  --container-name myapp
```

**Port already in use:**
```bash
docker ps  # Find conflicting container
docker stop <container>  # Stop it first
```

**View deployment history:**
```bash
dockerpilot deploy history --limit 10
```

For more information, see the main [README.md](../README.md).

