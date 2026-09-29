# Secure Deploy — code-driven security audit (2026-09-29)

## Purpose

This document records what the current implementation actually guarantees. It is not a product claim and it does not assume that the architecture behaves as intended merely because a design document says so.

Audit base: GitHub `main` at `8668e9945a76265c19f3c981304efc498911a08d`.

Primary question:

> If an AI-facing control plane, MCP client, or DockerPilot Extras process is compromised or follows a malicious instruction, which privileged infrastructure mutations remain possible without passing the Secure Deploy policy / approval / broker boundary?

## Scope reviewed

- `DockerPilotExtras/backend/secure_deploy/*`
- `src/dockerpilot/secure_deploy_broker/*`
- Secure Deploy HTTP resources and approval flow
- broker protocol, peer authentication, artifact integrity and systemd unit
- broker-owned canary execution path
- `src/dockerpilot/mcp/*`
- legacy DockerPilot Extras mutation surfaces, including command execution, deployment and storage-bootstrap routes
- existing Secure Deploy / broker / canary tests

This is a static code audit. It is not yet a host-level penetration test of the installed broker unit.

## Trust domains found in code

```text
AI / MCP client / browser
        |
        v
DockerPilot MCP or DockerPilot Extras
(unprivileged control plane; potentially compromised)
        |
        | Unix socket, expected peer UID
        v
Secure Deploy broker
(root; Docker socket reachable; separate trust domain)
        |
        +--> broker-owned DozeyGuard + root-owned policy
        |
        +--> fixed broker-owned canary template / argv / workdir
        |
        v
Docker daemon
        |
        v
Host
```

The broker is a meaningful privilege boundary only if the AI-facing process cannot mutate Docker through another path.

## What is already strong

### S-01 — Broker protocol is closed

`src/dockerpilot/secure_deploy_broker/protocol.py` exposes a small explicit operation set. Generic `apply`, `deploy`, `exec`, `command`, `shell`, firewall application, secret materialization and rollback are rejected. Canary requests also reject client-controlled Docker fields such as compose content, argv, image, project, service, workdir and port.

This materially limits what a compromised Extras process can ask the root broker to do.

### S-02 — Broker independently revalidates the plan

`verifier.py` validates schemas, recomputes `plan_sha256`, spec and normalized Compose hashes, checks TTL, reruns broker-owned DozeyGuard, verifies its input/result hashes and recomputes firewall semantics.

The broker does not trust a `PASS` result supplied by Extras.

### S-03 — Live canary execution is broker-owned

`canary.py` owns the image digest, project/service identity, Compose model, workdir, command argv, environment, health checks and cleanup behavior. The template uses a non-root user, read-only root filesystem, `no-new-privileges`, `cap_drop: ALL`, resource limits and localhost-only publication. Volumes, secrets, devices, privileged mode and host networking are absent.

The final Compose bytes are written by the broker, hashed, scanned by DozeyGuard and hash-checked again immediately before `docker compose up`.

### S-04 — Replay / race handling exists in the broker ledger

The canary ledger binds staged admission to plan / approval identifiers and a bundle hash, tracks nonce and replay keys, checks TTLs, uses file locking and atomic state transitions, and prevents concurrent double execution from starting the canary twice.

### S-05 — Broker runtime is hardened

The systemd unit runs as root but applies a restrictive sandbox and deliberately exposes only the minimum live path currently required by the canary. Broker policy / binary checks enforce expected SHA-256 values and reject symlinked or writable artifacts.

The residual consequence remains important: broker RCE plus its deliberate Docker socket access is effectively compromise of the Docker trust boundary.

## Findings

### F-01 — HIGH — AI-facing mutation paths can bypass Secure Deploy

**Status:** confirmed architectural gap for a system-wide “safe AI agent” claim.

The repository contains mutation paths that do not pass through the Secure Deploy broker:

- MCP write tools directly use the Docker SDK (`container_start`, `stop`, `restart`, `exec`, `remove`, image prune, migration import/export).
- MCP `confirm=true` is a request field supplied by the caller. It is a UX guard, not an independent human approval boundary.
- DockerPilot Extras exposes `/api/command/execute`, which can invoke `docker` or `dockerpilot` directly.
- Extras exposes `/api/deployment/execute` and `/api/containers/blue-green-replace`, which invoke normal DockerPilot deployment flows.
- promotion and migration endpoints are separate mutation systems and are not broker-mediated Secure Deploy operations.
- Extras exposes `/api/storage/bootstrap-local-postgres`; its request accepts a caller-selected `image`, and the backing service uses `docker.from_env()` to start an existing container or `containers.run()` a new PostgreSQL container directly, outside the broker.

MCP defaults mitigate this: `DOCKERPILOT_MCP_READONLY` defaults to `true`, and destructive actions additionally default to disabled. Those defaults are useful, but they do not create a system invariant once write mode is enabled.

**Consequence:** the current code supports the narrower claim “the Secure Deploy broker path is policy-gated and privilege-separated.” It does **not** yet support the stronger claim “an AI agent with DockerPilot write access can mutate infrastructure only through Secure Deploy.”

**Required direction:** introduce an explicit agent-safe profile in which every AI-facing legacy mutation path is disabled or routed through a broker-mediated operation. This must be enforced server-side, not by UI visibility or prompt instructions.

### F-02 — HIGH for approval provenance; bounded impact for the current fixed canary — Approval authenticity is not broker-verifiable under Extras RCE

**Status:** confirmed trust-model limitation.

The broker verifies that an approval:

- has valid schema,
- names the same `plan_id` and `plan_sha256`,
- has status `approved`,
- is unexpired,
- and, for canary admission, has the same actor as the plan.

It does not verify a signature, MAC, hardware-backed assertion, broker-issued challenge, or other provenance rooted outside the compromised Extras process.

The initial broker `dry_run` receives the full approval object from Extras. An attacker with arbitrary code execution in Extras can therefore manufacture a syntactically valid fresh approval for a policy-valid plan. Once a broker admission bundle is staged, its later mutation is strongly constrained, but the broker cannot prove that the original approval came from the genuine human step-up flow.

This is consistent with the existing broker threat model statement that UI approval is not itself a security boundary against Extras RCE.

**Consequence:** “human approval remains trustworthy after control-plane compromise” is not currently a valid claim.

This does **not** mean compromised Extras can ask the current broker canary to mount `/`, mount `docker.sock`, use privileged mode, select arbitrary images or execute arbitrary commands. The closed broker template / policy still constrains execution.

**Required direction:** if human approval is intended to become a trust boundary, bind it to broker-verifiable provenance. Candidate designs include a broker-issued challenge plus a separate approval signer/service, or an asymmetric signature whose private key is unavailable to Extras. The design must also define replay, revocation and signer compromise semantics.

### F-03 — HIGH when exposed — Legacy Extras authentication can be disabled while legacy mutation routes remain registered

`WEB_AUTH_ENABLED` defaults to false. When it is false, the generic `/api/*` authentication guard returns immediately. Secure Deploy correctly refuses to run without Web Auth + MFA, but the legacy mutation routes remain separate.

**Consequence:** an Extras instance that is network-reachable with Web Auth disabled must not be treated as an agent-safe control plane.

**Required direction:** an agent-safe / production-safe profile should fail closed at startup unless authentication and the intended mutation policy are explicitly enabled. Legacy mutation endpoints should be denied independently of browser auth when agent-safe mode is active.

### F-04 — MEDIUM — Broker config is an assumed trust root, not fully verified by runtime code

The broker verifies the configured DozeyGuard binary and policy hashes / ownership expectations, and the systemd unit mounts `/etc/dockerpilot-secure-broker` read-only. `load_broker_config()` rejects a symlink and unknown fields, but does not itself verify config owner, mode or a pinned config hash.

On a correctly installed root-owned `/etc` this is a normal administrative trust assumption. It should nevertheless be explicit because modification of broker config can change expected peer UID, policy hash, allowed operations and canary parameters.

**Required direction:** document the config file as part of the TCB and add install/runtime checks for root ownership and non-writability by the Extras UID. A pinned config hash is optional if ownership/mount guarantees are sufficient and tested.

### F-05 — INFO / accepted by current design — `remove_canary` is not approval-bound

The broker permits removal by broker execution ID without a plan/approval tuple. This is not equivalent to arbitrary Docker deletion: the operation maps to the fixed broker-owned canary project and fixed `docker compose down` argv.

Keep this explicitly documented as a cleanup capability. Add regression tests ensuring the client can never supply project, path, compose file or cleanup argv.

### F-06 — MEDIUM — Plan expiry is execution-authorizing metadata but is not bound to plan identity

`PLAN_HASH_EXCLUDED_FIELDS` excludes the plan-level `expires_at` from `plan_sha256`. The broker later parses that same field and rejects the plan only if the current time has reached it. Approval binding checks the plan ID and plan hash, but not the plan-level expiry.

**Consequence:** after approval, a control-plane process can change only `plan.expires_at` to a later still-future time while preserving `plan_id`, `plan_sha256` and the approval binding. The broker then evaluates the modified expiry and may accept a plan outside the originally intended validity window.

This does not change the approved image, mounts, runtime privileges or other hashed execution content, so the impact is narrower than arbitrary plan tampering. It nevertheless violates the stronger invariant that all execution-authorizing metadata of an approved plan is immutable.

**Required direction:** preferably include plan `expires_at` in `plan_sha256`. If expiry intentionally remains excluded for determinism or lifecycle reasons, it needs a separate broker-verifiable binding that cannot be changed without invalidating approval. Add an adversarial regression that extends expiry after approval while leaving the hash and approval unchanged.

## Strategic conclusion

The most defensible current security statement is:

> DockerPilot Secure Deploy demonstrates an independently revalidated, privilege-separated and closed broker execution path. The fixed canary remains constrained even if DockerPilot Extras supplies malicious plan metadata, provided the broker / Docker host trust domain is intact.

The following statement is **not yet** defensible:

> Every infrastructure mutation available to an AI agent using DockerPilot is mediated by Secure Deploy and a broker-verifiable human approval.

The primary gaps blocking stronger claims are:

1. alternate AI-facing / legacy mutation paths (`F-01`),
2. approval provenance under control-plane compromise (`F-02`), and
3. plan-expiry immutability for an already approved plan (`F-06`).

These should be addressed before adding more deployment UX or broadening broker execution capabilities.

## Recommended order of work

1. Define and enforce **agent-safe mode**: read-only legacy MCP/API surfaces, no direct Docker mutation, no generic command execution.
2. Add adversarial tests proving no alternate mutation route exists in agent-safe mode.
3. Repair plan-expiry binding and add the expiry-tamper regression.
4. Decide whether human approval is intended to survive Extras compromise.
5. If yes, design broker-verifiable approval provenance and replay/revocation semantics.
6. Add the approval-forgery adversarial tests before implementing the fix.
7. Harden / test broker config ownership as part of the TCB.
8. Only after these invariants hold, consider broadening the broker beyond the current fixed canary.

## Non-goals of this audit

- claiming the full repository is penetration-tested,
- claiming Docker daemon compromise is contained,
- claiming Kubernetes support,
- replacing the existing broker threat model,
- treating UI confirmation or an agent-provided `confirm=true` as human authorization.
