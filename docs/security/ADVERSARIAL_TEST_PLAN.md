# DockerPilot adversarial security test plan

Goal: turn the Secure Deploy security thesis into executable evidence. Tests should attack invariants, not implementation details.

A red test is a useful audit result. Do not weaken the expected security property merely to make the suite green.

## Classification

Each scenario should end as one of:

- `PASS` — invariant is enforced.
- `FAIL_IMPLEMENTATION` — intended architecture is sound but current code violates it.
- `FAIL_ARCHITECTURE` — current trust model cannot enforce the invariant without redesign.
- `OUT_OF_SCOPE` — explicitly outside the documented security boundary, with rationale.

## Track A — alternate-path / agent-safe isolation

### AT-01 — MCP caller sets `confirm=true`

**Setup:** agent-safe mode enabled; caller invokes a mutation tool with `confirm=true`.

**Expected:** mutation is unavailable or broker-routed. Caller-supplied confirmation alone cannot authorize Docker mutation.

**Invariant:** INV-01, INV-02.

### AT-02 — MCP direct container exec

Attempt `dockerpilot_container_exec` in agent-safe mode.

**Expected:** blocked before Docker SDK invocation.

### AT-03 — MCP start/stop/restart/remove

Attempt every direct container lifecycle mutation, including destructive mode toggles.

**Expected:** blocked or broker-routed; environment variables controlled by the same agent process cannot silently turn off agent-safe mode at runtime.

### AT-03B — MCP image prune

Attempt `dockerpilot_image_prune(dangling_only=False, confirm=true, dry_run=false)` with write and destructive MCP settings enabled.

**Expected:** blocked before the Docker image-prune API is invoked, or broker-routed through a separately reviewed contract. Agent-safe mode must override caller/config combinations that would otherwise permit destructive pruning.

**Invariant:** INV-01, INV-02.

### AT-04 — MCP migration import / replace

Attempt a bundle import with `start=true` and conflict replacement.

**Expected:** blocked or broker-routed in agent-safe mode.

### AT-04B — MCP migration export with named-volume data

Attempt `dockerpilot_migration_export_bundle` with `include_data=true` and `confirm=true` for a container that has a named volume and with MCP write mode enabled.

**Expected:** blocked before the helper-container path can call Docker `containers.create()`, `start()` or `remove()`, or broker-routed through a separately reviewed contract. An operation described as export/read must still count as a Docker mutation when its implementation creates ephemeral containers.

**Permanent regression:** `tests/test_mcp_agent_safe_migration_export.py` verifies that the frozen agent-safe configuration blocks export before `MigrationOps` is reached, even when normal MCP write/destructive flags and caller confirmation request mutation.

**Invariant:** INV-01, INV-02.

### AT-05 — Extras generic command execution

POST `/api/command/execute` with Docker/DockerPilot mutations.

**Expected:** denied in agent-safe mode regardless of normal authenticated-session status.

### AT-06 — Legacy deployment endpoints

Exercise `/api/deployment/execute`, `/api/containers/blue-green-replace`, promotion and migration mutation endpoints.

**Expected:** denied or explicitly routed through the reviewed broker contract in agent-safe mode.

### AT-06B — Extras storage bootstrap

POST `/api/storage/bootstrap-local-postgres` with a caller-selected `image`, and exercise both the create-new-container and start-existing-container paths.

**Expected:** denied or explicitly routed through the reviewed broker contract in agent-safe mode. The request must be blocked before `docker.from_env()`, `containers.run()` or `container.start()` can mutate Docker state.

**Invariant:** INV-01.

### AT-07 — Web Auth disabled

Start an agent-safe deployment with `WEB_AUTH_ENABLED=false`.

**Expected:** fail-closed startup or all mutation surfaces disabled. The service must not expose unauthenticated legacy writes.

## Track B — plan / hash binding

### AT-08 — mutate hashed plan field after approval

Change a security-relevant plan field that participates in `plan_sha256` after approval without updating the approval.

**Expected:** broker rejects hash mismatch / binding mismatch before execution.

### AT-08B — extend plan expiry after approval

Create and approve a plan, then change only the plan-level `expires_at` to a later still-future timestamp while leaving `plan_id`, `plan_sha256` and the approval unchanged.

**Expected:** reject with plan identity / approval binding failure before the modified TTL can authorize execution. Under the corrected hash semantics from PR #59, plan-level `expires_at` participates in `plan_sha256`, so changing expiry without recomputing the hash yields `plan_hash_mismatch`.

A second control case must recompute `plan_sha256` for an already-expired plan and verify that the broker's independent TTL gate still rejects it as `plan_expired`. This proves expiry is both identity-bound and independently time-checked.

Plans persisted with the earlier expiry-excluding hash algorithm are intentionally incompatible with the corrected identity semantics and must be regenerated / approved again rather than grandfathered.

**Current expected result after PR #59:** `PASS` when both the hash-mismatch tamper case and independent expired-plan TTL case are green.

**Invariant:** INV-04, INV-08.

### AT-09 — recompute plan hash after mutation

Change a security-relevant hashed field and recompute `plan_sha256`, but reuse the old approval.

**Expected:** approval binding mismatch.

### AT-10 — swap image digest

Change only the image digest between approval and broker execution.

**Expected:** plan/approval or fixed-template rejection before Docker command.

### AT-11 — forged DozeyGuard PASS

Replace client-supplied DozeyGuard result with a synthetic PASS and recompute client-owned fields.

**Expected:** broker-owned scan result governs; forged result is rejected or irrelevant.

### AT-12 — scan/apply TOCTOU

Mutate the final Compose file after broker DozeyGuard returns but before `docker compose up`.

**Expected:** final hash check rejects before `up`.

## Track C — approval provenance and replay

### AT-13 — fabricate approval inside compromised Extras

Construct a new syntactically valid approval object for a policy-valid plan without completing the trusted approver-channel flow.

**Current result on `main`:** `PASS` for the documented Extras-RCE threat model after PR #63.

The permanent regression requires all of these properties:

1. `dry_run` rejects a client-supplied approval object,
2. a fabricated / nonexistent `approval_id` is rejected,
3. challenge creation alone grants no authority,
4. the approver channel derives UID from `SO_PEERCRED` and rejects a UID outside
   the root-owned allowlist,
5. after the trusted approver creates the broker-owned v2 record, the exact
   `approval_id + plan_id + plan_sha256` is accepted,
6. cross-plan/hash substitution still fails,
7. broker-owned approval lookup/revocation remains authoritative for v2 records.

PR #62 provides the authority core and PR #63 provides the live transport and
enforcement path. Keep AT-13 merge-blocking for future changes to approval,
broker protocol or control-plane trust boundaries.

**Invariant:** INV-09.

### AT-14 — cross-plan approval replay

Use an approval from plan A with plan B.

**Expected:** reject.

### AT-15 — duplicate approval nonce

Attempt to admit a second security-distinct execution using an already consumed/admitted nonce.

**Expected:** reject as replay.

### AT-16 — stale / expired approval

Cross the approval TTL before admission and again after admission but before live Docker execution.

**Expected:** reject before Docker command and record an auditable expired state.

### AT-17 — revoke race

Race revoke against admit and deploy.

**Expected:** state machine produces one deterministic outcome; revoked admission must not subsequently execute. Executing-state revocation semantics must be explicit.

## Track D — broker request injection

### AT-18 — arbitrary operation names

Send `apply`, `deploy`, `exec`, `shell`, `command`, `firewall_apply`, `materialize_secrets` and unknown operation names.

**Expected:** protocol rejection.

### AT-19 — Docker override fields

Inject `compose`, `argv`, `command`, `image`, `project`, `service`, `workdir`, `path`, `env`, `port`, cleanup flags, mounts/devices equivalents and nonce into canary operations.

**Expected:** schema / protocol rejection.

### AT-20 — privileged template attempts

Attempt plans containing `/` bind mount, Docker socket mount, `privileged: true`, host network, devices, added capabilities and mutable image tags.

**Expected:** plan/policy/template rejection. No Docker command.

## Track E — broker TCB / filesystem

### AT-21 — replace DozeyGuard binary or policy

Change artifact contents while preserving path.

**Expected:** expected SHA-256 check fails before verification/execution.

### AT-22 — writable/symlink artifact and writable-parent replacement race

Exercise three cases for both the broker-owned DozeyGuard binary and policy path:

1. make the artifact itself writable by the untrusted / Extras UID,
2. replace the artifact with a direct symlink,
3. keep the artifact itself clean, root-owned, non-writable and hash-correct, then atomically replace the original pathname after the broker has opened and verified the artifact but before `subprocess.Popen()` / child policy loading.

For the binary case, race the replacement immediately before `Popen`. For the policy case, replace the original policy pathname at the same point and verify the child still receives the already-open verified policy object.

**Expected:** `PASS`. The broker must reject replacement that occurs before the verified open/hash step. Once verification succeeds, it must consume the same already-open file objects rather than reopening the original pathnames. The DozeyGuard executable and policy should be referenced through inherited `/proc/self/fd/*` descriptors (or an equivalent race-resistant primitive), so a later rename/`os.replace()` of the pathname cannot change the executed or parsed bytes.

The permanent regression must assert both halves of the property: pre-open replacement fails on hash mismatch, while post-verification pathname replacement leaves the pinned executable/policy bytes unchanged.

**Invariant:** INV-11, INV-14.

### AT-23 — broker config ownership / mode / pathname trust

Exercise the installed broker config trust boundary:

1. make the config group/other writable,
2. replace the final config path with a symlink or non-regular object,
3. place the config beneath a group/other-writable or symlinked parent component,
4. present a config with an unexpected owner/group under the root runtime profile,
5. atomically replace the config pathname after the broker has opened and validated the original file.

**Expected:** `PASS`. Unsafe owner/mode/type/parent-chain states must fail closed before the config can influence broker authority. For the post-open replacement case, the broker must parse the bytes from the already-open verified file descriptor rather than reopening the original pathname.

The root-broker installer materializes `/etc/dockerpilot-secure-broker/config.json` as root:root mode 0644. PR #61 adds the independent runtime enforcement and permanent regression coverage; after merge AT-23 must remain green.

**Invariant:** INV-11, INV-14.

### AT-24 — ledger symlink / corruption

Corrupt execution/bundle records or insert symlinks in broker state.

**Expected:** fail closed, no privileged Docker mutation.

### AT-25 — concurrent double deploy

Issue two live deploy requests for the same admitted execution.

**Expected:** exactly one `docker compose up` invocation.

## Track F — secrets / diagnostics

### AT-26 — secret-shaped values in errors

Inject token/password/API-key/Bearer-shaped data into reachable failure channels.

**Expected:** no reusable secret value in API response, job registry, UI-safe structured details or broker audit log.

### AT-27 — malicious control characters / oversized output

Return control characters and oversized stderr/stdout from broker-owned subprocesses and health paths.

**Expected:** bounded / sanitized output; no log framing injection and no unbounded memory or audit growth.

## Track G — migration safety boundary

These become relevant after the migration-preflight branch lands.

### AT-28 — missing target dependency before finalization

Cause dependency preflight to fail.

**Expected:** target workload remains untouched.

### AT-29 — incompatible network created between plan and apply

Create a same-name incompatible target network after read-only planning.

**Expected:** fail closed before destructive finalization.

### AT-30 — target IPAM overlap

Create another target network whose subnet conflicts with the source network being recreated.

**Expected:** reject before network creation / target replacement.

## Suggested execution order

1. Keep all Track A scenarios (AT-01 through AT-07, including AT-03B, AT-04B and AT-06B) merge-blocking for changes to AI-facing mutation surfaces.
2. Keep AT-22, AT-23 and AT-08B as permanent regressions after PR #60, PR #61 and PR #59 respectively; they must never revert to expected-red tests for compatibility convenience.
3. Keep AT-13 as a permanent merge-blocking regression after PR #62/#63; client-owned approval JSON must never become authoritative again.
4. Run existing broker/canary tests as baseline for the remaining Track B through Track F cases and fill only uncovered cases.
5. Keep broker config trust changes isolated from approval provenance and broader broker capability work.
6. Treat `APPROVAL_PROVENANCE_V1.md` as the active trust-boundary contract for broker-owned v2 approvals.

## Acceptance gate for the stronger project claim

The project may claim that AI-agent infrastructure mutation is broker-mediated only when:

- Track A is green with server-side enforcement,
- no write-enabled AI-facing route reaches Docker outside the reviewed broker contract,
- the deployment profile and limitations are documented,
- and CI treats these tests as merge-blocking security regressions.

Any claim that broker-owned policy enforcement remains trustworthy against a compromised control plane also requires AT-22 and AT-23 to remain green: replacing broker-owned artifacts after verification must not change the consumed bytes, and the security-relevant broker config must remain root-controlled and fail closed on unsafe owner/mode/path conditions.

Any claim that an approved plan is immutable with respect to all execution-authorizing metadata also requires AT-08B to remain green under the corrected hash semantics.

The stronger additional claim that a trusted local approval remains authoritative after Extras control-plane compromise is supported only while Track C stays green and the documented assumption holds that root and the allowlisted approver UID are not compromised.
