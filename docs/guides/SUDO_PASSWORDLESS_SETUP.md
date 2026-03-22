# Passwordless Sudo Setup for DockerPilot

## Problem

DockerPilot requires sudo access to backup Docker volumes (`/var/lib/docker/volumes/`). 
Currently, sudo prompt appears in console during deployment, which is bad for web UI.

## Solution: Passwordless sudo for Docker operations

### 1. Create sudoers file for DockerPilot

```bash
sudo visudo -f /etc/sudoers.d/dockerpilot
```

### 2. Add the following rules:

```bash
# DockerPilot - passwordless sudo for Docker backup operations
# Allow user to perform backup operations without password

# Backup Docker volumes (tar)
username ALL=(ALL) NOPASSWD: /bin/tar -czf /home/username/DockerPilot/backup_* *
username ALL=(ALL) NOPASSWD: /bin/tar -czf * -C /var/lib/docker/volumes/* *

# Ownership fix for backup files
username ALL=(ALL) NOPASSWD: /bin/chown username\:username /home/username/DockerPilot/backup_*

# Docker volume access
username ALL=(ALL) NOPASSWD: /usr/bin/docker run --rm -v * alpine\:latest *
```

**Note:** Replace `username` with your actual username.

### 3. Configuration verification:

```bash
# Test 1: Check syntax
sudo visudo -c -f /etc/sudoers.d/dockerpilot

# Test 2: Check if it works without password
sudo -n tar --version
# Should show tar version without asking for password
```

### 4. File permissions:

```bash
sudo chmod 440 /etc/sudoers.d/dockerpilot
sudo chown root:root /etc/sudoers.d/dockerpilot
```

## Alternative: Docker group (less secure)

```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and log back in or:
newgrp docker

# Now DockerPilot can use Docker without sudo
```

**WARNING:** Adding to `docker` group gives **root-equivalent access**. 
More secure is to use passwordless sudo with limited commands.

## Future: LDAP/AD Integration

In the future, it will be possible to integrate with:
- **LDAP/Active Directory** for central authentication
- **sudo + pam_ldap** for LDAP-based sudo access
- **Vault/Keycloak** for secret management

### Example LDAP integration:

```bash
# /etc/sudoers.d/dockerpilot-ldap
%docker-admins ALL=(ALL) NOPASSWD: /bin/tar, /bin/chown, /usr/bin/docker
```

Where `%docker-admins` is an LDAP group.

## Security

✅ **Good practices:**
- Passwordless sudo **only for specific commands**
- Path restrictions (e.g., only `/home/username/DockerPilot/backup_*`)
- Logging all sudo operations (`/var/log/auth.log`)

❌ **Avoid:**
- `username ALL=(ALL) NOPASSWD: ALL` - this gives full root without password!
- Storing sudo password in application
- Sending sudo password over HTTP (even HTTPS)

## Restart DockerPilot

After configuring passwordless sudo, restart backend:

```bash
cd /path/to/DockerPilot/DockerPilotExtras
python3 loader.py
```

Now backup will work without password prompt! ✅
