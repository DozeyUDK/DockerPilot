# DockerPilot - Sudo and Backup Permissions

DockerPilot does not require a global passwordless-sudo configuration.

The old `setup_passwordless_sudo.sh` helper and broad `NOPASSWD` rules are no
longer part of the supported setup. In particular, do not grant DockerPilot a
generic passwordless `docker run`, `tar`, or `chown` rule.

## Docker volumes

Named Docker volumes are backed up through a temporary helper container:

```bash
docker run --rm \
  -v volume_name:/volume:ro \
  -v /backup_dir:/backup \
  alpine:latest \
  sh -c 'tar -czf /backup/file.tar.gz -C /volume .'
```

This path does **not** invoke `sudo`. It does require access to the Docker
daemon, which is a privileged capability in its own right.

Docker daemon access must not be added to the agent-safe DockerPilotExtras
profile merely to enable backups. The agent-safe profile intentionally keeps
direct Docker authority outside the AI-facing process.

## Bind mounts

Normal readable bind mounts are archived without sudo.

A bind mount may require elevation when its source is under a privileged host
path such as:

- `/root/...`
- `/var/lib/docker/...`
- another path the current process cannot read

For this fallback DockerPilot uses `tar` with a scoped sudo credential for the
current operation. If no sudo credential is available, the privileged fallback
fails instead of relying on a persistent passwordless sudo rule.

After an elevated archive is created, DockerPilot may use `chown` to return the
backup file to the invoking user's UID/GID.

## DockerPilotExtras elevation

DockerPilotExtras does not accept an OS sudo password from the browser or API.
Its elevation endpoint issues a short-lived, one-time authorization capability
scoped to one container and environment transition.

For a privileged bind-mount backup, that capability permits the existing
read-only Docker helper path for the current execution. It carries no password
and does not grant Docker access by itself. If the Extras service account does
not already have the intended helper/Docker authority, the backup fails closed.

Direct `sudo tar` remains a trusted local CLI fallback only. Extras never
injects an OS credential into that path.

Secure Deploy is separate. The `dockerpilot-extras` service account in the
agent-safe/Secure Deploy profile must not receive general sudo or unrestricted
Docker-socket access.

## Recommended setup

For a normal local CLI installation:

1. Give the operator the Docker access that is intentionally required for the
   local DockerPilot workflow.
2. Keep backup destinations writable by that operator.
3. Prefer named Docker volumes or readable bind mounts where possible.
4. For an exceptional privileged bind mount, provide elevation only for that
   execution or perform the backup manually as an administrator.
5. Do not create a broad `/etc/sudoers.d/dockerpilot` `NOPASSWD` policy.

## Testing

A named volume can be tested without sudo:

```bash
docker run --rm \
  -v minikube:/volume:ro \
  -v /tmp:/backup \
  alpine:latest \
  sh -c 'tar -czf /backup/dockerpilot-test.tar.gz -C /volume .'

ls -lh /tmp/dockerpilot-test.tar.gz
rm /tmp/dockerpilot-test.tar.gz
```

If `docker run` itself is denied, fix the intended Docker access model rather
than wrapping Docker in a passwordless sudo rule.

## Security note

Membership in the `docker` group and unrestricted access to `docker.sock`
are effectively host-privileged capabilities. They are acceptable only for a
trusted local operator profile where that authority is intended; they are not
part of the agent-safe threat model.
