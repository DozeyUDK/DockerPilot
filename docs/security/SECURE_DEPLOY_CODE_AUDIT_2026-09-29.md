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

## Post-audit remediation status

The finding sections below are preserved as evidence from the original audit base; several findings have since been remediated on `main`. Current security claims must be scoped to the explicit agent-safe profile and the fixed broker canary contract.

- **F-01 / INV-01:** agent-safe mutation isolation merged in PR #57 (`ace7b3498faa6749185d03a00e4d76f051de6e2b`). Direct mutation remains available outside the agent-safe profile by design.
- **F-06 / INV-04:** plan-level `expires_at` is bound into `plan_sha256` after PR #59 (`345f646b0abe0b83a38c57046f393b8d5fbae9f3`), and the live canary path now rechecks immutable plan expiry immediately before Docker execution.
- **F-07 / INV-11:** verified open-file-descriptor pinning for the broker-owned DozeyGuard binary/policy and the AT-22 atomic-replacement regression merged in PR #60 (`c76bbf995a4eaa1c94fe29975484a6f5af5bce3c`).
- **F-04 / AT-23:** root-owner/mode/parent-chain checks and same-FD parsing for root broker runtime config merged in PR #61.
- **F-02 / INV-09:** broker-owned approval authority core merged in PR #62 and live approver-channel enforcement merged in PR #63. Client-supplied approval JSON is no longer authority for v2; `approval_id` resolves broker-owned state with `SO_PEERCRED` approver provenance.

## Current main conclusion

As of the PR #63 merge on `main`, the original audit blockers F-01, F-02, F-04,
F-06 and F-07 have focused remediations with permanent regression coverage.

This does **not** turn the whole repository into a universal production-safe
mutation plane. The stronger claims remain profile- and capability-dependent:
the root broker intentionally has Docker-socket authority, direct DockerPilot
mutation paths still exist outside agent-safe mode, the trusted approval model
does not survive compromise of root or an allowlisted approver UID, and the
reviewed live execution grammar is still the fixed canary contract rather than
an arbitrary application deploy API.

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

### S-05 — Broker runtime has meaningful hardening, but artifact path trust is incomplete

The systemd unit runs as root but applies a restrictive sandbox and deliberately exposes only the minimum live path currently required by the canary. Broker artifact checks validate expected SHA-256 values, reject direct symlinks, and reject writable / incorrectly owned final files.

Those checks do not yet prove that the pathname remains bound to the validated object until use. If an untrusted process can write an artifact's containing directory, it can potentially replace a clean validated pathname before the broker later executes or reopens it. This is recorded as F-07 below.

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

**Permanent regression:** `tests/test_secure_deploy_remove_canary_contract.py` pins this as a closed cleanup capability. The protocol accepts only the broker execution identifier and rejects client-controlled project, path, compose, command/argv, image, environment, cleanup, mount/device, privilege/network and plan/approval override fields. The Extras broker client also rejects cleanup override fields before socket use.

### F-06 — MEDIUM — Plan expiry is execution-authorizing metadata but is not bound to plan identity

`PLAN_HASH_EXCLUDED_FIELDS` excludes the plan-level `expires_at` from `plan_sha256`. The broker later parses that same field and rejects the plan only if the current time has reached it. Approval binding checks the plan ID and plan hash, but not the plan-level expiry.

**Consequence:** after approval, a control-plane process can change only `plan.expires_at` to a later still-future time while preserving `plan_id`, `plan_sha256` and the approval binding. The broker then evaluates the modified expiry and may accept a plan outside the originally intended validity window.

This does not change the approved image, mounts, runtime privileges or other hashed execution content, so the impact is narrower than arbitrary plan tampering. It nevertheless violates the stronger invariant that all execution-authorizing metadata of an approved plan is immutable.

**Required direction:** preferably include plan `expires_at` in `plan_sha256`. If expiry intentionally remains excluded for determinism or lifecycle reasons, it needs a separate broker-verifiable binding that cannot be changed without invalidating approval. Add an adversarial regression that extends expiry after approval while leaving the hash and approval unchanged.

### F-07 — HIGH — Validated broker artifact pathname can be swapped through a writable parent directory

`assert_trusted_artifact()` rejects direct symlinks, checks the current final file's type / ownership / mode, and hashes its contents. After this check returns, broker request handling continues and the DozeyGuard runner later passes `config.executable` and `config.policy_path` as pathnames to `subprocess.Popen()` / the child process.

The trust check does not validate every parent directory in the path and does not hold an open verified file descriptor across the later use. Therefore a clean, root-owned, non-writable, hash-correct artifact can reside inside a directory writable by the compromised Extras UID; after validation, the attacker can atomically rename/replace the pathname before root execution or before DozeyGuard opens the policy path.

**Consequence:** for the executable path, winning this race can cause attacker-controlled bytes to be executed by the root broker despite the earlier trusted-artifact check. For the policy path, it can substitute policy after validation and undermine broker-owned policy enforcement. This directly violates INV-11 and is a privilege-boundary issue, not merely a test-coverage problem.

**Required direction:** make artifact trust race-resistant. At minimum, validate the entire relevant pathname chain as root-controlled and non-writable by the untrusted UID. Stronger designs should avoid check-then-reopen by pathname: open the verified artifact with no-follow semantics, validate identity/content on that open object, and execute/consume the same object or use an equivalent immutable/root-owned deployment location whose parent chain cannot be replaced by Extras. Add a deterministic adversarial race test that swaps the pathname after validation and before consumption.

## Historical strategic conclusion (audit base)

The most defensible current security statement is:

> DockerPilot Secure Deploy demonstrates a closed, independently revalidated broker design with meaningful privilege separation, but the stronger control-plane-compromise claim remains blocked by alternate mutation paths and unresolved trust-boundary gaps documented below.

The following statement is **not yet** defensible:

> Every infrastructure mutation available to an AI agent using DockerPilot is mediated by Secure Deploy and a broker-verifiable human approval, while broker-owned policy artifacts remain immutable against the compromised control plane.

The primary gaps blocking stronger claims are:

1. alternate AI-facing / legacy mutation paths (`F-01`),
2. approval provenance under control-plane compromise (`F-02`),
3. plan-expiry immutability for an already approved plan (`F-06`), and
4. broker artifact pathname TOCTOU when a parent directory is writable by the untrusted control-plane UID (`F-07`).

These should be addressed before adding more deployment UX or broadening broker execution capabilities.

## Historical recommended order of work (audit base)

1. Define and enforce **agent-safe mode**: read-only legacy MCP/API surfaces, no direct Docker mutation, no generic command execution.
2. Add adversarial tests proving no alternate mutation route exists in agent-safe mode.
3. Repair the broker artifact pathname trust / writable-parent TOCTOU and add the atomic-replacement regression.
4. Repair plan-expiry binding and add the expiry-tamper regression.
5. Decide whether human approval is intended to survive Extras compromise.
6. If yes, design broker-verifiable approval provenance and replay/revocation semantics.
7. Add the approval-forgery adversarial tests before implementing the fix.
8. Harden / test broker config ownership as part of the TCB.
9. Only after these invariants hold, consider broadening the broker beyond the current fixed canary.

## Non-goals of this audit

- claiming the full repository is penetration-tested,
- claiming Docker daemon compromise is contained,
- claiming Kubernetes support,
- replacing the existing broker threat model,
- treating UI confirmation or an agent-provided `confirm=true` as human authorization.
