# Secure Deploy Broker Threat Model (#11D.1)

## A. RCE inside DockerPilotExtras

UI approval is **not** a security boundary against Extras RCE.

Broker must still block:

- arbitrary Docker operations
- host bind `/`, docker.sock, privileged, host network, devices, `cap_add: ALL`
- arbitrary firewall rules / SSH allow removal
- plan/hash drift, approval replay
- secret reads / materialization

Primary boundary: **independent broker revalidation + operation allowlist**.

## B. Attacker controls draft/plan store

Broker recomputes `plan_sha256`, Spec/Compose hashes, Dozeyguard result, firewall semantics. Drift → reject.

## C. Attacker replaces user-owned Dozeyguard

Flask may use `DOZEYGUARD_*`. Broker uses **only** `BROKER_DOZEYGUARD_BIN` / `BROKER_DOZEYGUARD_POLICY_PATH` (+ optional checksum). Writable-by-client policy → fail-closed.

## D. Other processes as UID `dozey`

Target: Extras runs as `dockerpilot-extras` without docker group; broker socket via dedicated group. Staged systemd examples only — **not installed in #11D.1**.

## Cookie / CSRF note

SameSite depends on **scheme + registrable domain**, not port. Different localhost ports are usually same-site. Production: `Secure` cookie, CSRF always, Origin required (`SECURE_DEPLOY_REQUIRE_ORIGIN` / production). Trusted-client mode default **false**.

## E. Canary / systemd network posture

### Why the broker needs `/var/run/docker.sock`

Closed canary deploy/remove is **broker-owned**. The broker writes a fixed Compose template,
runs Dozeyguard on the final bytes, then invokes `docker compose` with a controlled argv/env
and cwd. The Docker CLI talks to the daemon only through the host Unix socket. Clients never
supply Compose content, project name, image tag, ports, mounts, or runner argv.

### Why a root broker with docker.sock is a privileged boundary

A process that can speak to the Docker daemon can usually achieve host compromise (privileged
containers, host mounts, etc.). Therefore:

- the broker runs as root with a **closed operation allowlist**;
- canary admission is content-addressed and broker-staged;
- revoke/TTL/replay gates sit in front of any Docker call;
- Extras must **not** receive `docker.sock`, membership in the `docker` group, sudo, or a
  canary loopback HTTP path as an alternate execution mechanism.

Compromise of the broker is treated as compromise of the Docker trust boundary.

### Why broker ingress must stay closed

UI / Extras RCE is assumed. The only host-side execution path is the broker Unix socket
protocol: identifiers + hashes, independent revalidation, no client-controlled Compose,
no generic apply. Opening generic Docker or network RPC on that socket would collapse the
model.

### Why host loopback `127.0.0.1:18080` is required

The canary publishes solely on host loopback (`127.0.0.1:18080`). After `compose up`, the
broker performs:

1. `port_is_free()` via `AF_INET` bind preflight on that address/port;
2. Docker health status via `docker compose ps`;
3. a final **HTTP GET** to `http://127.0.0.1:18080/` expecting a fixed status/body.

That HTTP gate must observe the **host** published port. `PrivateNetwork=true` would put the
broker in a private network namespace and break the gate. systemd `IPAddressAllow` cannot
restrict by destination port; residual risk is any TCP to `127.0.0.1` from the broker unit.
IPv6 (`::1`) is not used by the canary code path and is not enabled in address families.

### Residual risks

- Root + docker.sock ⇒ Docker/host compromise if the broker is RCE'd past the allowlist.
- `IPAddressAllow=127.0.0.1/32` permits any loopback TCP port, not only `18080`.
- Docker CLI/daemon bugs remain outside the broker's revalidation scope once a command is
  intentionally issued.
- Preview-only unit templates must not be confused with the canary-capable production unit.
