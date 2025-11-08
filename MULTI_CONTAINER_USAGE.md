# Handling Multiple Containers/Images Simultaneously

## Description

DockerPilot now supports executing commands for multiple containers or images simultaneously by passing them as a comma-separated list.

## Syntax

To execute a command for multiple containers/images, provide their names or IDs separated by commas:
```
name1,name2,name3
```
or
```
id1,id2,id3
```

Spaces around commas are ignored, so you can also use:
```
name1, name2, name3
```

## Supported Commands

### 1. Start containers
```bash
# CLI
dockerpilot container start app1,app2,app3

# Interactive mode
> start
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2,app3
```

### 2. Stop containers
```bash
# CLI
dockerpilot container stop app1,app2 --timeout 15

# Interactive mode
> stop
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Timeout seconds [10]: 15
```

### 3. Restart containers
```bash
# CLI
dockerpilot container restart app1,app2,app3

# Interactive mode
> restart
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2,app3
```

### 4. Remove containers
```bash
# CLI
dockerpilot container remove app1,app2 --force

# Interactive mode
> remove
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Force removal? [y/N]: y
```

### 5. Pause/Unpause containers
```bash
# CLI
dockerpilot container pause app1,app2
dockerpilot container unpause app1,app2

# Interactive mode
> pause
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
```

### 6. Exec in containers
```bash
# CLI - executes command in each container sequentially
dockerpilot container exec app1,app2 --command "ls -la"

# Interactive mode
> exec
Container name(s) or ID(s) (comma-separated for multiple, e.g., app1,app2): app1,app2
Command to execute [/bin/bash]: ls -la
```

**Note:** Exec commands are executed sequentially (one after another), allowing interaction with each container.

### 7. Logs from containers
```bash
# CLI
dockerpilot container logs app1,app2,app3 --tail 100

# Interactive mode
> logs
Container name(s) or ID(s) (comma-separated for multiple, empty for interactive select): app1,app2
```

Logs from each container are displayed sequentially with clear separators.

### 8. Remove images
```bash
# CLI
dockerpilot container remove-image nginx:latest,redis:alpine,postgres:13 --force

# Interactive mode
> remove-image
Image name(s) or ID(s) to remove (comma-separated for multiple, e.g., img1:tag,img2:tag): nginx:latest,redis:alpine
Force removal? [y/N]: n
```

## Usage Examples

### Example 1: Restart multiple application containers
```bash
dockerpilot container restart backend-api,frontend-web,worker-queue
```

### Example 2: Stop all microservice containers
```bash
dockerpilot container stop auth-service,user-service,payment-service,notification-service --timeout 20
```

### Example 3: Remove old images
```bash
dockerpilot container remove-image myapp:v1.0,myapp:v1.1,myapp:v1.2 --force
```

### Example 4: Execute command in multiple containers
```bash
dockerpilot container exec web1,web2,web3 --command "nginx -s reload"
```

### Example 5: Display logs from multiple containers
```bash
dockerpilot container logs app1,app2,app3 --tail 50
```

### Example 6: Use container IDs
```bash
dockerpilot container stop fa90f84e0007,5c867ecaebaf
```

## Error Handling

- If an operation fails for one container, the remaining ones will still be processed
- After all operations complete, a summary is displayed:
  - ✅ All operations completed successfully
  - ⚠️ Some operations failed

## Interactive Mode vs CLI

Both versions (CLI and interactive) support the same functionality. The choice depends on user preference:

- **CLI**: Fast, suitable for scripting
- **Interactive**: User-friendly interface with hints

## Additional Information

- Container names and IDs can be mixed in one list
- Operation order matches the order of provided containers/images
- Operations are executed synchronously (one after another)
- For exec and logs, each container is processed separately with clear indication

