# DockerPilot security invariants

These are architecture-level properties, not UI requirements. A feature that violates one of these invariants must either be redesigned or explicitly declared outside the agent-safe security boundary.

## Agent / control-plane boundary

### INV-01 — No alternate agent mutation path

When agent-safe mode is enabled, an AI-facing client MUST NOT be able to mutate Docker / host state through MCP, legacy Extras endpoints, generic command execution, deployment, promotion, migration, storage bootstrap or another side channel that bypasses the approved broker path.

UI hiding and prompt instructions do not satisfy this invariant.

### INV-02 — Caller `confirm=true` is not human approval

A boolean or string supplied by the same caller requesting the operation MUST NOT be treated as an independent human authorization factor.

### INV-03 — Agent/control-plane does not receive privileged host authority

The AI-facing process MUST NOT receive root credentials, unrestricted sudo credentials, the broker's privileged material, or an unrestricted Docker control socket in the agent-safe profile.

## Plan / policy invariants

### INV-04 — Security-relevant mutation invalidates plan identity

Every security-relevant execution input MUST be represented in the deterministic plan hash or in an equally strong approval-bound mechanism. Mutating such an input after plan creation MUST change `plan_sha256` or otherwise make the approval invalid.

Current remediation in PR #59: plan-level `expires_at` participates in `plan_sha256`. Extending or shortening plan expiry therefore changes plan identity and invalidates an approval bound to the previous hash. The broker still performs an independent TTL check after validating plan identity.

Compatibility rule: plans produced with the earlier expiry-excluding hash algorithm are not grandfathered. They must be regenerated and approved again rather than silently inheriting the previous approval under corrected hash semantics.

### INV-05 — Broker independently validates

The privileged broker MUST NOT trust policy results, normalized output, hashes or execution parameters merely because they came from DockerPilot Extras. It MUST recompute / revalidate the security-relevant subset itself.

### INV-06 — Final applied bytes are the scanned bytes

For file-backed execution, the bytes scanned by the broker-owned policy engine MUST be cryptographically tied to the bytes consumed immediately by the execution step. A TOCTOU mutation between scan and execution MUST fail closed.

### INV-07 — Broker execution grammar is closed

A client MUST NOT be able to provide arbitrary shell text, argv, compose path/content, project name, Docker socket path, image, mounts, devices, privileged mode, host network or equivalent execution overrides unless that field is explicitly part of a separately reviewed broker contract and policy.

## Approval invariants

### INV-08 — Approval is bound to one immutable plan

An approval MUST identify exactly one `plan_id` and `plan_sha256`. It MUST NOT authorize a different plan. Execution-authorizing metadata that affects whether or how the plan may execute MUST participate in plan identity or be independently approval-bound.

For the corrected plan-hash semantics in PR #59, plan-level `expires_at` is included in `plan_sha256`; approval-level expiry remains a separate TTL on the approval record.

### INV-09 — Approval provenance matches the threat model

If the product claims that human approval remains meaningful after AI/control-plane compromise, the broker MUST be able to verify approval provenance using trust material unavailable to the compromised control plane.

Current status on `main`: **satisfied for the documented local approver threat
model after PR #63**. The broker-owned v2 approval path no longer treats
Extras-owned approval JSON as authority.

PR #62 introduced the broker-owned approval authority core: one-shot challenges,
root-broker state, explicit approver UID allowlisting, plan/hash binding and TTL
enforcement. PR #63 completed the live path with a separate root-only approver
socket, Linux `SO_PEERCRED` identity, broker-owned approval lookup/revocation and
`approval_id`-only authorization for live dry-run/admit/deploy.

This property is intentionally scoped: it protects against compromise of the
`dockerpilot-extras` UID, not compromise of root or an allowlisted approver UID,
and it does not claim the compromised Extras UI is an independent trustworthy
plan-display channel. See `APPROVAL_PROVENANCE_V1.md`.

### INV-10 — Approval is bounded and replay-resistant

Approval use MUST be time-bounded and replay semantics MUST be explicit. Nonces / execution claims MUST prevent an approval intended for one admitted execution from silently authorizing another security-distinct execution.

## Privileged broker / TCB invariants

### INV-11 — Broker artifact and configuration trust is explicit

The broker executable, policy, their containing pathname components, and security-relevant config MUST be root-controlled / immutable to the unprivileged control-plane UID. Runtime must fail closed when these assumptions cannot be established.

Validating only the final file object is insufficient if an untrusted process can replace that pathname through a writable parent directory after validation. For artifacts that are opened or executed later by pathname, either every relevant parent directory MUST be non-writable by the untrusted UID, or the broker MUST use an already-open verified object / equivalent race-resistant primitive that cryptographically and referentially ties the checked bytes to the consumed bytes.

Current remediation in PR #60: the broker opens the DozeyGuard executable and policy with no-follow semantics, validates type/mode/ownership expectations and hashes the already-open file descriptors, then keeps those descriptors open across subprocess creation. The child receives `/proc/self/fd/*` references through `pass_fds`, so replacing the original binary or policy pathname after validation no longer changes the file objects executed or read.

Current remediation in PR #61: when the installed broker runs as root, the runtime config must be an absolute root:root regular file that is not group/other writable, and every containing directory through `/` must be root-owned/root-group, non-symlink and not group/other writable. The config is opened once with no-follow/nonblocking semantics, metadata is checked with `fstat`, and JSON is read from that same descriptor, so pathname replacement after the verified open cannot swap the parsed bytes.

PR #61 is merged on `main`; AT-23 remains a permanent regression for broker-config trust.

### INV-12 — Peer identity is necessary but not authorization

Unix `SO_PEERCRED` on the ordinary control socket / expected Extras UID may
authenticate the calling process domain, but MUST NOT by itself prove human
authorization. Compromise of the Extras process running under that UID is within
the adversarial model.

A separately permissioned approver socket may use `SO_PEERCRED` as approval
provenance only when the allowed UID is explicitly outside the compromised
control-plane boundary and the socket filesystem permissions prevent the Extras
UID from reaching that channel.

### INV-13 — Failure before destructive finalization is non-destructive

Any operation that has a plan/preflight/finalization split MUST leave the target workload untouched when validation, policy, dependency preflight or approval fails before the destructive boundary.

### INV-14 — Fail closed on ambiguity

Malformed state, unknown fields, unverifiable policy output, missing trusted artifacts, expired state, hash mismatch, unexpected topology or inability to prove a required security property MUST reject the privileged operation rather than silently downgrade security.

## Data / audit invariants

### INV-15 — Secrets do not enter approval / diagnostics / audit artifacts

Secret values MUST NOT appear in deterministic plans, approval records, broker audit events, structured errors, UI failure metadata or migration job registries. Secret references may be represented only where the contract explicitly allows references.

### INV-16 — Security decisions are auditable without leaking authority

The system SHOULD emit bounded records containing plan hash, policy result identity, approval/execution identifiers, state transitions and reason codes, while excluding reusable credentials, raw secret values and privileged command material.

## Current implementation snapshot

The original audit snapshot was taken at `main@8668e9945a76265c19f3c981304efc498911a08d`. Subsequent focused remediation PRs should be read together with that snapshot rather than treating the historical status bullets as immutable current state.

- INV-05/06/07 are strongly represented in the fixed broker canary path.
- INV-04 plan-expiry identity is enforced by PR #59, and the live canary path also rechecks the immutable plan expiry immediately before Docker execution. AT-08B and the deploy-time expiry regression are permanent gates.
- INV-08/10 are substantially implemented for the current broker admission ledger, including bounded approval TTLs, revocation and replay controls.
- INV-09 is enforced on `main` for the documented Extras-RCE threat model after PR #63: the root-owned approver channel uses `SO_PEERCRED`, live authorization accepts `approval_id` only, and broker-owned v2 records remain authoritative.
- INV-01 is addressed by PR #57 for the explicit agent-safe profile; repository-wide direct mutation remains available outside that profile by design.
- INV-02 is violated as a *human-approval interpretation* if MCP `confirm=true` is described as human confirmation; it is acceptable only as a caller acknowledgement flag.
- INV-03 depends on deployment profile; direct MCP Docker access means it is not a universal repository-wide invariant today.
- INV-11 artifact pathname TOCTOU is addressed by PR #60 through verified open-file-descriptor pinning for the broker-owned DozeyGuard executable and policy. PR #61 adds independent runtime owner/mode/parent-chain and same-FD checks for the root broker config; AT-23 must remain green after merge.
