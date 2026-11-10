# Secure Deploy Broker Threat Model

**Status:** Current repository security model for the broker-owned approval v2 and constrained canary execution path.

**Scope:** Protect the Docker host from compromise of the DockerPilotExtras control-plane process running as the unprivileged dockerpilot-extras service account.

This document describes the intended trust boundary in the current code. It does not assert that the broker or its systemd units are installed on every DockerPilot host. Host installation and operational verification are separate privileged steps.

## 1. Security objective

A compromised Extras process must not be able to turn its own API access, local files, or fabricated approval JSON into arbitrary privileged Docker operations.

The broker independently validates the plan and policy inputs, obtains approval authority through a separate local channel, and executes only the operations explicitly implemented by its closed protocol.

## 2. Threat model

Assume an attacker has arbitrary code execution as dockerpilot-extras and can:

- read and modify Extras-owned files, process memory, and local approval metadata;
- call every endpoint and broker operation exposed to the Extras UID;
- fabricate plans, hashes, DozeyGuard result metadata, and approval-shaped JSON;
- bypass the Extras HTTP authentication, CSRF, and step-up TOTP implementation;
- replay any data available to the Extras process.

The attacker does not initially have:

- host root or control of an allowlisted approver UID;
- write access to broker-owned state, configuration, executable, or policy;
- access to the root-only approver socket;
- unrestricted access to the Docker daemon.

The boundary is specifically against compromise of the Extras service account. It is not a defense against host root, broker compromise, Docker daemon compromise, or compromise of an allowlisted approver identity.

## 3. Trust boundaries

### 3.1 Extras control plane

Extras creates and stores deployment drafts, validates the user-facing spec, generates a plan, and sends broker requests. It is not an authority for broker approval.

The ordinary broker control socket authenticates the expected Extras peer using Linux SO_PEERCRED. This identifies the service account; it does not establish human approval.

The Secure Deploy profile must not give the Extras service account membership in the Docker group, unrestricted access to /var/run/docker.sock, or general sudo authority.

### 3.2 Privileged broker

The broker is the privileged enforcement point. It owns its configuration, approval ledger, staged canary bundles, execution ledger, audit records, and trusted DozeyGuard artifacts.

The broker has Docker daemon access for the fixed canary workflow. A process that can control the Docker daemon can generally reach host-level authority. Therefore, compromise of the broker is treated as compromise of the Docker trust boundary.

The broker protocol is a closed operation allowlist, not a general root RPC interface.

### 3.3 Trusted approver channel

The approver service listens on a separate Unix socket:

- path: /run/dockerpilot-secure-broker/approver.sock;
- owner/group: root:root;
- mode: 0600;
- service: root-owned, AF_UNIX only, with Docker socket paths inaccessible.

The broker derives the peer UID from Linux SO_PEERCRED; request JSON cannot supply or override the authoritative UID. The UID must appear in the root-owned allowed_approver_uids configuration. The initial production template allows UID 0.

The initial operator client is:

~~~bash
sudo /usr/libexec/dockerpilot-secure-broker/approve \
  --challenge <challenge_id> \
  --plan-sha256 <64-hex-plan-hash>
~~~

This establishes an allowlisted local process identity. It does not prove which human initiated a root process, and it does not make the Extras UI a trusted display of the plan. The repository currently provides no separate trusted plan viewer for the root approver; plan contents and hash must be verified out of band. Approval by hash alone is not proof that the plan contents were reviewed.

### 3.4 Docker daemon

The root broker is the only component in this profile that needs Docker daemon authority for Secure Deploy canary execution. The Docker daemon and host remain part of the trusted computing base.

## 4. Plan and policy verification

The broker does not trust client-provided plan status, hashes, DozeyGuard findings, or firewall actions.

Before authorizing the constrained workflow, it independently:

1. validates the closed deployment-plan schema;
2. requires the self-contained source spec;
3. recomputes the canonical plan_sha256;
4. checks plan expiry;
5. validates the source spec and recomputes the normalized Compose model and its digest;
6. runs the broker-owned DozeyGuard executable against the recomputed Compose model;
7. compares the fresh DozeyGuard result, exit code, blocking finding count, and policy identity with the plan;
8. recomputes firewall semantics and compares the planned actions.

The plan hash covers security-relevant plan content, including expires_at. Changing plan expiry without generating a new hash therefore invalidates the plan identity and any approval bound to the previous hash.

The broker uses BROKER_DOZEYGUARD_* configuration, not the Flask/Extras DOZEYGUARD_* environment. The executable and policy are broker-owned artifacts. The hardened path pins verified file descriptors and validates ownership, mode, and configuration path trust before use.

DozeyGuard is an offline scanner. It does not access Docker, apply firewall rules, execute containers, or materialize secrets.

## 5. Broker-owned approval v2

Approval authority is split into three steps:

1. **Challenge creation:** Extras asks the broker to revalidate the plan and create a non-authorizing challenge. The challenge binds a plan ID, plan hash, broker-generated nonce, and a short expiry. The current challenge TTL is 120 seconds.
2. **Trusted approval:** an allowlisted peer connects to the separate root-only approver socket and confirms the exact challenge ID and plan hash. The broker writes the approval record to its own ledger. The current approval TTL is 600 seconds.
3. **Authorization:** the control plane supplies only approval_id. The broker loads the record from its own ledger and checks active status, plan ID, plan hash, expiry, and approver provenance. Client-supplied approval JSON is not authority.

The Extras step-up TOTP gate protects access to selected Extras endpoints, including challenge creation and revocation. It is not the broker's approval provenance and does not replace the separate approver channel.

The broker approval record remains active until revoked or expired. Replay control for actual canary execution is enforced separately by the broker-owned canary admission/execution ledger: the staged bundle is bound to the plan and approval, the approval nonce is hashed into the execution record, state transitions are guarded, and a completed execution reaches a terminal state such as consumed. A successful dry_run does not itself consume the approval.

See [Secure Deploy Approval Model](SECURE_DEPLOY_APPROVAL_MODEL.md) and [Approval Provenance](APPROVAL_PROVENANCE_V1.md).

## 6. Closed broker operations

The current control protocol supports liveness/capability checks, plan verification, broker-owned challenge and approval lookup/revocation, dry-run verification, and the fixed canary admission/deploy/remove operations.

It does not expose generic apply, shell, command, exec, arbitrary Compose input, arbitrary host paths, client-selected Docker socket paths, arbitrary firewall application, or secret materialization.

The canary workflow uses a broker-owned template and policy. The client cannot choose the Compose project, service, work directory, image, mounts, devices, host network, command, or cleanup flags. The current policy binds the canary to a digest-pinned image and loopback-only host port.

## 7. Canary / systemd network posture

### Docker daemon access

The root broker needs /var/run/docker.sock (or the platform's equivalent Docker socket) for the broker-owned canary docker compose operation. Access to that socket is host-privileged.

The Extras process must not receive /var/run/docker.sock, membership in the docker group, sudo access that recreates general Docker authority, or a separate canary HTTP endpoint as an alternate execution path.

### Broker service network restrictions

The canary-capable systemd service uses:

- RestrictAddressFamilies=AF_UNIX AF_INET;
- IPAddressDeny=any with IPAddressAllow=127.0.0.1/32;
- PrivateNetwork unset/false;
- ProtectSystem=strict, ProtectHome=true, PrivateTmp=true, and PrivateDevices=true;
- no user-controlled EnvironmentFile and a minimal PATH.

AF_UNIX is required for the broker control socket and Docker daemon socket. AF_INET is required for the host-loopback canary health gate on 127.0.0.1:18080.

IPAddressAllow=127.0.0.1/32 filters IP packets only. It does not restrict Unix sockets. The systemd unit explicitly documents that IP filters do NOT gate Unix sockets such as broker.sock and docker.sock.

PrivateNetwork=true would place the broker in a separate network namespace and break the host-loopback health gate. The canary code uses IPv4 loopback only; it does not use ::1.

### Separate approver service

The approver socket is root-only (0600). Its service is restricted to AF_UNIX, has no Docker socket access, and has /var/run/docker.sock and /run/docker.sock inaccessible. It can create broker-owned approval records but cannot execute Docker operations.

### Deployment status

The repository contains systemd unit templates and staging/installation tooling. Their presence in Git does not prove they are installed or active on a particular host. Verify the target host's installed bundle, systemd units, socket ownership/mode, broker configuration, and service state before making an operational security claim.

## 8. Residual risks and non-goals

- Root broker compromise is host-level compromise because the broker has Docker daemon authority.
- Host root or an allowlisted approver UID can bypass the Extras boundary.
- SO_PEERCRED proves a local process UID, not a human identity or a user's review of plan contents.
- The Extras UI is not an independent trusted plan-display channel under the Extras-RCE threat model.
- Docker daemon and kernel vulnerabilities remain outside the broker's policy checks.
- The broker is not a general-purpose deployment engine, shell, firewall manager, or secret manager.
- The current canary implementation is a constrained execution path, not a claim that arbitrary application deployments are supported by Secure Deploy.

## 9. Related implementation references

- [Broker protocol v1](SECURE_DEPLOY_BROKER_PROTOCOL_V1.md)
- [Approval provenance](APPROVAL_PROVENANCE_V1.md)
- [Security invariants](SECURITY_INVARIANTS.md)
- [Adversarial test plan](ADVERSARIAL_TEST_PLAN.md)
- [Systemd hardening](ROOT_BROKER_SYSTEMD_HARDENING.md)
- [DozeyGuard](../../components/dozeyguard/README.md)
