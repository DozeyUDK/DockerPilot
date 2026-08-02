# Root Broker systemd Hardening

Units (committed templates):

- `deploy/systemd/dockerpilot-secure-broker.socket`
- `deploy/systemd/dockerpilot-secure-broker.service`

## Socket

- `ListenStream=/run/dockerpilot-secure-broker/broker.sock`
- `SocketUser=root`
- `SocketGroup=dockerpilot-secure-broker`
- `SocketMode=0660`
- `RemoveOnStop=true`

Service receives systemd-activated FD (`LISTEN_FDS`); standalone bind remains for tests only.

## Service highlights

- `User=root` / `Group=root` (future #11D.2B)
- `CapabilityBoundingSet=` / `AmbientCapabilities=`
- `RestrictAddressFamilies=AF_UNIX`
- `IPAddressDeny=any`
- `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `PrivateDevices=true`
- `NoNewPrivileges=true`, `MemoryDenyWriteExecute=true` (document host breakage if Python fails)
- `InaccessiblePaths=/var/run/docker.sock` (+ optional `/run/docker.sock`)
- `PYTHONNOUSERSITE=1`, minimal `PATH`, no user `EnvironmentFile`
- `WorkingDirectory=/`

## Residual risk

Compromise of the root broker process is still root compromise. The socket is not a general root RPC — operations are allowlisted and plans are independently revalidated.
