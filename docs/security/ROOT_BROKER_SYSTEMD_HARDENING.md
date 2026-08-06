# Root Broker systemd Hardening

Units (committed templates):

- `deploy/systemd/dockerpilot-secure-broker.socket`
- `deploy/systemd/dockerpilot-secure-broker.service` (canary-capable production unit)
- `deploy/systemd/dockerpilot-secure-broker.service.example` (preview-only / no canary)

## Socket

- `ListenStream=/run/dockerpilot-secure-broker/broker.sock`
- `SocketUser=root`
- `SocketGroup=dockerpilot-secure-broker`
- `SocketMode=0660`
- `RemoveOnStop=true`

Service receives systemd-activated FD (`LISTEN_FDS`); standalone bind remains for tests only.

## Service highlights (canary-capable production unit)

- `User=root` / `Group=root`
- `CapabilityBoundingSet=` / `AmbientCapabilities=`
- `RestrictAddressFamilies=AF_UNIX AF_INET`
  - `AF_UNIX`: broker socket activation + Docker daemon Unix socket
  - `AF_INET`: `port_is_free()` bind check and HTTP GET to `127.0.0.1:18080`
  - `AF_INET6` omitted: canary code uses IPv4 loopback only
- `IPAddressDeny=any` + `IPAddressAllow=127.0.0.1/32`
  - Filters **IP packets only**; Unix sockets (`broker.sock`, `docker.sock`) are unaffected
  - Port filtering is not available via these directives (residual: any TCP port on `127.0.0.1`)
- `PrivateNetwork` must remain **unset/false**
  - Canary publishes on the **host** loopback `127.0.0.1:18080`
  - `PrivateNetwork=true` would place the broker in a private netns and break the health gate
- `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `PrivateDevices=true`
- `NoNewPrivileges=true`, `MemoryDenyWriteExecute=true` (document host breakage if Python fails)
- **No** `InaccessiblePaths` for `/var/run/docker.sock` / `/run/docker.sock`
  - Canary deploy/remove requires broker-owned `docker compose` over the Docker Unix socket
- `ReadWritePaths=/var/lib/dockerpilot-secure-broker /run/dockerpilot-secure-broker`
  - Fixed canary workdir lives under `/var/lib/dockerpilot-secure-broker/canary/...`
- `PYTHONNOUSERSITE=1`, minimal `PATH`, no user `EnvironmentFile`
- `WorkingDirectory=/`

## Preview-only example

`dockerpilot-secure-broker.service.example` keeps the stricter preview posture:

- `RestrictAddressFamilies=AF_UNIX`
- `IPAddressDeny=any` without loopback allow
- `InaccessiblePaths=/var/run/docker.sock` (+ optional `/run/docker.sock`)

Do not install the example unit when canary operations are enabled.

## Residual risk

Compromise of the root broker process with Docker socket access is host Docker compromise
(and typically root-equivalent). The Unix control socket is **not** a general root RPC —
operations remain allowlisted, plans/approvals are independently revalidated, and Extras
never receives `docker.sock`, the docker group, or canary loopback as an execution path.

See `docs/security/SECURE_DEPLOY_BROKER_THREAT_MODEL.md` § Canary / systemd network posture.
