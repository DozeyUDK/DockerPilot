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

Current status: **target invariant, not fully satisfied for plan expiry**. `PLAN_HASH_EXCLUDED_FIELDS` currently excludes the plan-level `expires_at`, while the broker uses that field to decide whether execution is still permitted. Approval binding checks `plan_id` and `plan_sha256`, not the plan expiry. Therefore changing a still-future plan `expires_at` can preserve both plan identity and approval binding. The preferred repair is to include plan `expires_at` in plan identity; an alternative is a separately broker-verifiable binding that makes expiry mutation invalidate approval.

### INV-05 — Broker independently validates

The privileged broker MUST NOT trust policy results, normalized output, hashes or execution parameters merely because they came from DockerPilot Extras. It MUST recompute / revalidate the security-relevant subset itself.

### INV-06 — Final applied bytes are the scanned bytes

For file-backed execution, the bytes scanned by the broker-owned policy engine MUST be cryptographically tied to the bytes consumed immediately by the execution step. A TOCTOU mutation between scan and execution MUST fail closed.

### INV-07 — Broker execution grammar is closed

A client MUST NOT be able to provide arbitrary shell text, argv, compose path/content, project name, Docker socket path, image, mounts, devices, privileged mode, host network or equivalent execution overrides unless that field is explicitly part of a separately reviewed broker contract and policy.

## Approval invariants

### INV-08 — Approval is bound to one immutable plan

An approval MUST identify exactly one `plan_id` and `plan_sha256`. It MUST NOT authorize a different plan. Any execution-authorizing metadata excluded from `plan_sha256`, such as plan expiry, MUST be independently bound if it can affect whether or how the approved operation may execute.

### INV-09 — Approval provenance matches the threat model

If the product claims that human approval remains meaningful after AI/control-plane compromise, the broker MUST be able to verify approval provenance using trust material unavailable to the compromised control plane.

Current status: **target invariant, not yet satisfied against Extras RCE**.

### INV-10 — Approval is bounded and replay-resistant

Approval use MUST be time-bounded and replay semantics MUST be explicit. Nonces / execution claims MUST prevent an approval intended for one admitted execution from silently authorizing another security-distinct execution.

## Privileged broker / TCB invariants

### INV-11 — Broker artifact and configuration trust is explicit

The broker executable, policy and security-relevant config MUST be root-controlled / immutable to the unprivileged control-plane UID. Runtime must fail closed when these assumptions cannot be established.

### INV-12 — Peer identity is necessary but not authorization

Unix `SO_PEERCRED` / expected UID may authenticate the calling process domain, but MUST NOT by itself prove human authorization. Compromise of a process running under the expected UID is within the adversarial model.

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

As of `main@8668e9945a76265c19f3c981304efc498911a08d`:

- INV-05/06/07 are strongly represented in the fixed broker canary path.
- INV-04 is not fully satisfied because plan-level `expires_at` is execution-authorizing metadata but is excluded from `plan_sha256` and is not independently approval-bound.
- INV-08/10 are substantially implemented for the current broker admission ledger, subject to the plan-expiry binding gap above.
- INV-09 is not satisfied against arbitrary Extras-process compromise.
- INV-01 is not satisfied when legacy MCP/API write surfaces are enabled.
- INV-02 is violated as a *human-approval interpretation* if MCP `confirm=true` is described as human confirmation; it is acceptable only as a caller acknowledgement flag.
- INV-03 depends on deployment profile; direct MCP Docker access means it is not a universal repository-wide invariant today.
- INV-11 is partly implemented for broker binary/policy; config ownership remains an explicit TCB assumption to harden/test.
