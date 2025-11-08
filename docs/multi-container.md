# Multiple Containers/Images Operations

## Overview

DockerPilot supports executing operations on multiple containers or images simultaneously using comma-separated lists.

## Syntax

Provide names or IDs separated by commas:

```bash
container1,container2,container3
```

Spaces around commas are ignored:

```bash
container1, container2, container3
```

## Supported Operations

### Start Multiple Containers

```bash
# CLI
dockerpilot container start app1,app2,app3

# Interactive
> start
Container name(s): app1,app2,app3
```

### Stop Multiple Containers

```bash
dockerpilot container stop app1,app2,app3 --timeout 15
```

### Restart Multiple Containers

```bash
dockerpilot container restart backend,frontend,worker
```

### Remove Multiple Containers

```bash
dockerpilot container remove old-app1,old-app2,old-app3 --force
```

### Pause/Unpause Containers

```bash
dockerpilot container pause app1,app2
dockerpilot container unpause app1,app2
```

### Execute Commands in Multiple Containers

```bash
# Same command in all containers
dockerpilot container exec web1,web2,web3 --command "nginx -s reload"

# Interactive (executes sequentially)
dockerpilot container exec app1,app2 --command "ls -la"
```

### View Logs from Multiple Containers

```bash
dockerpilot container logs app1,app2,app3 --tail 100
```

Logs from each container are displayed with clear separators.

### Remove Multiple Images

```bash
dockerpilot container remove-image nginx:old,redis:old,postgres:old --force
```

## Practical Examples

### Restart All Microservices

```bash
dockerpilot container restart auth-service,user-service,payment-service,notification-service
```

### Clean Up Development Containers

```bash
dockerpilot container stop test-app1,test-app2,test-app3
dockerpilot container remove test-app1,test-app2,test-app3 --force
```

### Update Multiple Web Servers

```bash
dockerpilot container exec web1,web2,web3 --command "nginx -s reload"
```

### Remove Old Image Versions

```bash
dockerpilot container remove-image myapp:v1.0,myapp:v1.1,myapp:v1.2 --force
```

### Using Container IDs

```bash
dockerpilot container stop fa90f84e0007,5c867ecaebaf
```

## Error Handling

- Operations continue even if one container fails
- Summary displayed after completion:
  - ✅ "All operations completed successfully"
  - ⚠️ "Some operations failed"

## Interactive Mode

All commands support interactive mode with helpful prompts:

```bash
dockerpilot

> start
Container name(s) or ID(s) (comma-separated for multiple): app1,app2,app3
```

## Notes

- Container names and IDs can be mixed in one list
- Operations execute sequentially (one after another)
- Operation order matches input order
- For exec and logs, each container is processed separately with clear indication

For more information, see the main [README.md](../README.md).

