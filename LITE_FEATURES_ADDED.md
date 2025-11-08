# New Features from dockerpilot-Lite Added to pilot.py

## ✅ Added Features

### 1. 📊 **One-Time Container Statistics** - `get_container_stats_once()`

Quick check of container statistics without long-term monitoring.

#### CLI:
```bash
# One-time statistics
dockerpilot monitor stats mycontainer
```

#### Python:
```python
pilot.get_container_stats_once("mycontainer")
```

#### Interactive menu:
```
Select: stats
Container name: mycontainer
```

**Output:**
```
📊 Container Statistics: mycontainer
🖥️  CPU Usage: 23.45%
💾 Memory: 512.34 MB / 2048.00 MB (25.02%)
🌐 Network RX: 45.23 MB, TX: 12.45 MB
⚡ Processes: 15
```

---

### 2. 🎥 **Live Monitoring with Screen Clearing** - `monitor_container_live()`

Real-time monitoring with screen clearing every second (like `htop`).

#### CLI:
```bash
# Live monitoring for 30 seconds
dockerpilot monitor live mycontainer --duration 30

# Longer monitoring
dockerpilot monitor live mycontainer --duration 120
```

#### Python:
```python
# Monitor for 30 seconds
pilot.monitor_container_live("mycontainer", duration=30)
```

#### Interactive menu:
```
Select: live-monitor
Container name: mycontainer
Duration seconds [30]: 60
```

**Display:**
```
📊 Live Monitoring: mycontainer
🖥️  CPU: 23.45%
💾 RAM: 512.3MB / 2048.0MB (25.0%)
⏱️  Time: 15/30s
Press Ctrl+C to stop
```

**Features:**
- ✅ Automatic screen clearing every second
- ✅ Displays CPU, RAM, time
- ✅ Can be interrupted with Ctrl+C
- ✅ Time limit

---

### 3. 🛑🗑️ **Stop and Remove in One Operation** - `stop_and_remove_container()`

Convenient stopping and removing of containers with one command.

#### CLI:
```bash
# Single container
dockerpilot container stop-remove mycontainer

# Multiple containers
dockerpilot container stop-remove app1,app2,app3

# With custom timeout
dockerpilot container stop-remove mycontainer --timeout 30
```

#### Python:
```python
# Stop and remove
pilot.stop_and_remove_container("mycontainer", timeout=10)
```

#### Interactive menu:
```
Select: stop-remove
Container name(s): app1,app2
Timeout seconds [10]: 15
```

**Output:**
```
🛑 Stopping container mycontainer...
✅ Container stopped
🗑️ Removing container mycontainer...
✅ Container mycontainer removed
```

---

### 4. ⚙️ **Non-Interactive Exec** - `exec_command_non_interactive()`

Execute a command in a container and show output (without opening a shell).

#### CLI:
```bash
# Simple command
dockerpilot container exec-simple mycontainer "ls -la"

# Check version
dockerpilot container exec-simple mycontainer "node --version"

# Check application logs
dockerpilot container exec-simple mycontainer "cat /app/logs/error.log"
```

#### Python:
```python
# Execute command
pilot.exec_command_non_interactive("mycontainer", "ls -la /app")
```

#### Interactive menu:
```
Select: exec-simple
Container name: mycontainer
Command to execute: ls -la /app
```

**Output:**
```
⚙️ Executing: ls -la
total 128
drwxr-xr-x 5 root root 4096 Jan 10 12:34 .
drwxr-xr-x 1 root root 4096 Jan 10 12:34 ..
-rw-r--r-- 1 root root  512 Jan 10 12:34 app.js
✅ Command executed successfully
```

---

### 5. 🩺 **Standalone Health Check** - `health_check_standalone()`

Test health check endpoint without deployment.

#### CLI:
```bash
# Basic test
dockerpilot monitor health 8080

# With custom endpoint
dockerpilot monitor health 8080 --endpoint /api/health

# With more retries
dockerpilot monitor health 8080 --endpoint /health --retries 20
```

#### Python:
```python
# Test health check
pilot.health_check_standalone(
    port=8080,
    endpoint="/health",
    max_retries=10
)
```

#### Interactive menu:
```
Select: health-check
Port number: 8080
Health check endpoint [/health]: /api/status
Maximum retries [10]: 15
```

**Output:**
```
🩺 Testing health check: http://localhost:8080/health
✅ Health check OK (attempt 1/10)
Response time: 0.12s
```

---

## 📋 Comparison with Dashboard Monitoring

| Feature | Dashboard | Live Monitor | Stats Once |
|---------|-----------|--------------|------------|
| Multiple containers | ✅ | ❌ | ❌ |
| Screen clearing | ❌ | ✅ | ❌ |
| Advanced UI | ✅ | ❌ | ❌ |
| Speed | Slower | Fast | Fastest |
| CPU/RAM | ✅ | ✅ | ✅ |
| Network | ✅ | ❌ | ✅ |
| Disk I/O | ✅ | ❌ | ❌ |
| History | ✅ | ❌ | ❌ |
| Use Case | Production | Development | Quick check |

---

## 🎯 When to Use Which?

### 📊 `get_container_stats_once()`
**Use when:**
- ✅ You want to quickly check statistics
- ✅ You don't need real-time monitoring
- ✅ Writing automation scripts
- ✅ Want a snapshot at a specific moment

**Example:**
```bash
# Check before deployment
dockerpilot monitor stats myapp

# In a script
for container in app1 app2 app3; do
    dockerpilot monitor stats $container
done
```

---

### 🎥 `monitor_container_live()`
**Use when:**
- ✅ Debugging performance issues
- ✅ Want to see changes in real-time
- ✅ Testing application load
- ✅ Prefer simple interface (like htop)

**Example:**
```bash
# Monitor during load test
dockerpilot monitor live myapp --duration 120 &
# Run test
ab -n 10000 -c 100 http://localhost:8080/
```

---

### 🛑🗑️ `stop_and_remove_container()`
**Use when:**
- ✅ Want to quickly clean up containers
- ✅ Don't need to keep the container
- ✅ Cleaning development environment
- ✅ Removing old versions

**Example:**
```bash
# Clean up all test containers
dockerpilot container stop-remove test-app1,test-app2,test-app3

# In a cleanup script
dockerpilot container stop-remove old-version
dockerpilot deploy quick --image-tag myapp:new --container-name myapp
```

---

### ⚙️ `exec_command_non_interactive()`
**Use when:**
- ✅ Want to execute a single command
- ✅ Need output in terminal
- ✅ Writing automation scripts
- ✅ Don't need interactive shell

**Example:**
```bash
# Check configuration
dockerpilot container exec-simple myapp "cat /etc/nginx/nginx.conf"

# Check processes
dockerpilot container exec-simple myapp "ps aux"

# Check disk space
dockerpilot container exec-simple myapp "df -h"
```

---

### 🩺 `health_check_standalone()`
**Use when:**
- ✅ Testing if application is running
- ✅ Debugging health check issues
- ✅ Want to check response time
- ✅ Testing new endpoint

**Example:**
```bash
# Test after deployment
dockerpilot monitor health 8080

# Test custom endpoint
dockerpilot monitor health 8080 --endpoint /api/v2/health

# Long stability test
dockerpilot monitor health 8080 --retries 100
```

---

## 📚 Full CLI Command List

### Container Operations
```bash
# List
dockerpilot container list
dockerpilot container list-images

# Management
dockerpilot container start myapp
dockerpilot container stop myapp
dockerpilot container restart myapp
dockerpilot container remove myapp
dockerpilot container stop-remove myapp          # ⭐ NEW

# Exec
dockerpilot container exec myapp                 # Interactive
dockerpilot container exec-simple myapp "ls -la" # ⭐ NEW - Non-interactive

# Info
dockerpilot container logs myapp
```

### Monitoring
```bash
# Dashboard (for multiple containers)
dockerpilot monitor dashboard app1 app2 --duration 300

# Live monitoring (single container)         # ⭐ NEW
dockerpilot monitor live myapp --duration 30

# One-time stats                             # ⭐ NEW
dockerpilot monitor stats myapp

# Health check                               # ⭐ NEW
dockerpilot monitor health 8080 --endpoint /health
```

### Deployment
```bash
# Quick deploy
dockerpilot deploy quick --image-tag myapp:v1 --container-name myapp --port 80:8080

# Rolling/Blue-green/Canary
dockerpilot deploy config deployment.yml --type rolling
```

---

## 🔄 Migration from dockerpilot-Lite

If you were using `dockerpilot-Lite.py`, you can now use `pilot.py` with the same features:

### Command Mapping

| dockerpilot-Lite | pilot.py (CLI) | pilot.py (Menu) |
|------------------|----------------|-----------------|
| `stats_container()` | `monitor stats` | `stats` |
| `monitor_container_live()` | `monitor live` | `live-monitor` |
| `stop_and_remove()` | `container stop-remove` | `stop-remove` |
| `exec_in_container()` | `container exec-simple` | `exec-simple` |
| `health_check_menu()` | `monitor health` | `health-check` |

### Migration Example

**Before (Lite):**
```python
from dockerpilot.lite import stats_container
stats_container("myapp")
```

**After (pilot.py):**
```python
from dockerpilot.pilot import DockerPilotEnhanced
pilot = DockerPilotEnhanced()
pilot.get_container_stats_once("myapp")
```

---

## 🎉 Summary

Features added from `dockerpilot-Lite.py`:

1. ✅ **One-time statistics** - quick check without monitoring
2. ✅ **Live monitoring with clearing** - monitoring like `htop`
3. ✅ **Stop and remove** - more convenient cleanup
4. ✅ **Non-interactive exec** - execute command and show output
5. ✅ **Standalone health check** - test endpoints

**All features are available through:**
- 🖥️ CLI (`dockerpilot monitor stats myapp`)
- 🐍 Python API (`pilot.get_container_stats_once("myapp")`)
- 🎮 Interactive Menu (select `stats`)

**Advantages:**
- ✅ Full compatibility with existing features
- ✅ Logging and operation history
- ✅ Better error handling
- ✅ Support for multiple containers
- ✅ Integration with deployment system

---

## 📖 More Information

- **Quick Deploy Guide**: `QUICK_DEPLOY_GUIDE.md`
- **Full Documentation**: `README.md`
- **API Reference**: `python -m pydoc dockerpilot.pilot`

## 🚀 Quick Start

```bash
# Install/update
pip install -e .

# Check statistics
dockerpilot monitor stats mycontainer

# Live monitoring
dockerpilot monitor live mycontainer

# Stop and remove
dockerpilot container stop-remove old-app

# Health check
dockerpilot monitor health 8080

# Interactive menu
dockerpilot
```
