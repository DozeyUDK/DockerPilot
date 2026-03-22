# DockerPilot - Sudo Configuration for Backups

## Problem
DockerPilot during container data backup may encounter permission issues with:
- Docker volumes (`/var/lib/docker/volumes/`)
- System bind mounts (`/root/`, `/var/lib/docker/`)
- Files owned by other users

## Solution

### 1. Docker Volumes Backup (WITHOUT SUDO!)

DockerPilot uses **Docker API** to backup volumes, which **does not require sudo**:

```bash
docker run --rm \
  -v volume_name:/volume:ro \
  -v /backup_dir:/backup \
  alpine:latest \
  sh -c 'tar -czf /backup/file.tar.gz -C /volume . && chown $(id -u):$(id -g) /backup/file.tar.gz'
```

**Benefits:**
- ✅ Does not require sudo
- ✅ Container runs as root and has access to data
- ✅ Automatic backup file ownership fix
- ✅ Safe (volume mounted as read-only)

### 2. Bind Mounts Backup (OPTIONALLY SUDO)

For bind mounts requiring permissions, DockerPilot automatically uses sudo:

```bash
sudo tar -czf /backup/file.tar.gz -C /source/parent source_name
sudo chown $(id -u):$(id -g) /backup/file.tar.gz
```

### 3. Sudo Configuration (Optional)

If you want DockerPilot to work without asking for sudo password, add to `/etc/sudoers.d/dockerpilot`:

```bash
# Create configuration file
sudo visudo -f /etc/sudoers.d/dockerpilot
```

Add the following lines (replace `username` with your username):

```
# DockerPilot - backup operations
username ALL=(root) NOPASSWD: /bin/tar
username ALL=(root) NOPASSWD: /bin/chown
```

**NOTE:** This is optional! DockerPilot will work without this, but may ask for sudo password when backing up some bind mounts.

### 4. Alternative - Add User to Docker Group

If you don't have Docker access without sudo yet:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

After this, log out and log back in or run `newgrp docker`.

## Testing

Test volume backup without sudo:

```bash
# Test backup of minikube volume
docker run --rm \
  -v minikube:/volume:ro \
  -v /tmp:/backup \
  alpine:latest \
  sh -c 'tar -czf /backup/test.tar.gz -C /volume . && chown $(id -u):$(id -g) /backup/test.tar.gz'

# Check ownership
ls -lh /tmp/test.tar.gz

# Remove test
rm /tmp/test.tar.gz
```

## Summary

- **Docker volumes**: Backup uses `docker run` - **DOES NOT REQUIRE SUDO**
- **Bind mounts**: 
  - Regular paths: backup without sudo
  - Privileged paths: backup with sudo (may ask for password)
- **System bind mounts** (`/lib/modules`, `/proc`, etc.): **AUTOMATICALLY SKIPPED**
- **Backup errors**: Do not interrupt deployment - continue with warning

## Security

DockerPilot uses sudo **only** for:
- `tar` operations (creating archive)
- `chown` operations (fixing ownership)

Sudo is **NOT** used for:
- Docker API access
- Creating/removing containers
- Image management
- Docker volumes backup (uses `docker run`)
