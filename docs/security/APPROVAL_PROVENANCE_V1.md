# Approval provenance v1

## Current status

Implemented on `main` by PR #62 (authority core) and PR #63 (live transport and
enforcement). Broker-owned v2 approval authority is active for the documented
Extras-RCE threat model.

## Goal

Make a trusted local operator approval remain meaningful when the DockerPilot Extras
process is fully compromised.

The protected property is not "the approval JSON looks valid". The protected
property is:

> The privileged broker can prove that approval authority crossed a trust
> boundary unavailable to the compromised Extras UID.

This is INV-09 / AT-13.

## Threat model

Assume an attacker has arbitrary code execution as `dockerpilot-extras` and can:

- read and modify every Extras-owned file and in-process object,
- call every endpoint or broker operation exposed to the Extras UID,
- fabricate plan-adjacent approval JSON,
- bypass the Extras HTTP/TOTP implementation,
- replay any data visible to Extras.

The attacker does **not** initially have:

- root,
- the broker's root-owned state,
- another trusted local operator UID,
- access to a Unix socket whose filesystem permissions exclude
  `dockerpilot-extras`.

## Why the legacy v1 approval was insufficient

The legacy Extras `ApprovalService` creates an `approved` record after the
web step-up flow. That record remains local metadata for compatibility, but it
is not authoritative for broker-owned v2 execution.

A syntactically valid client-supplied approval object is therefore never treated
as broker-verifiable provenance by the live v2 path.

## Chosen architecture

Approval authority moves into broker-owned state and is split into two channels.

### Control channel

Existing socket, accessible to `dockerpilot-extras`.

It may request a non-authorizing approval challenge for a validated plan. The
challenge is bound to:

- `challenge_id`,
- reserved `approval_id`,
- `plan_id`,
- `plan_sha256`,
- broker-generated nonce,
- short challenge TTL.

Creating a challenge grants no execution authority.

### Approver channel

A separate root-owned Unix socket that is **not accessible** to
`dockerpilot-extras`.

The broker derives the approver UID from `SO_PEERCRED`; the client does not
supply its authoritative identity. The UID must be in a root-owned config
allowlist.

The approver confirms the exact plan hash for one outstanding challenge. The
broker then creates the approval record in its own root-owned ledger.

### Execution channel

The control-plane supplies an `approval_id`, not an authoritative approval
object.

The broker loads the approval from broker-owned state and independently checks:

- status is active,
- `plan_id` matches,
- `plan_sha256` matches,
- approval TTL has not expired,
- provenance kind is the trusted approver channel,
- recorded approver UID remains allowed,
- replay/consumption state permits the requested transition.

A client-fabricated approval JSON is ignored as authority.

## PR #62 scope: authority core

PR #62 introduces the durable authority primitive only:

- broker-owned one-shot challenge records,
- broker-owned approval records,
- explicit approver UID allowlist,
- plan/hash binding,
- bounded TTLs,
- 0700 state directories and 0600 records,
- no-follow bounded reads,
- cross-process file locking,
- create-only approval records,
- fail-closed symlink/mode checks,
- AT-13-core regression proving an Extras-fabricated record is absent from the
  broker authority unless the broker created it.

PR #62 does **not** alter the live broker protocol. INV-09 therefore remains
open after #62 by design.

## PR #63 scope: transport and enforcement

PR #63 implements the live transport/enforcement half:

1. the existing control socket remains available to `dockerpilot-extras`,
2. a separate `approver.sock` is installed as `root:root 0600`,
3. the approver service has no Docker socket access and only `AF_UNIX`,
4. approver identity comes from Linux `SO_PEERCRED`, never request JSON,
5. the production allowlist is root-owned config with `allowed_approver_uids=[0]`,
6. control-plane challenge creation is non-authorizing and independently verifies
   the plan first,
7. `dry_run` accepts `approval_id` only and resolves the approval from the
   broker-owned authority ledger,
8. client-supplied approval JSON is explicitly rejected for `dry_run`,
9. the broker-owned v2 approval is carried into the immutable canary admission
   bundle for later admission/deploy replay checks,
10. AT-13 exercises the live boundary: a fabricated approval object/ID fails,
    while the same reserved approval ID succeeds only after the trusted
    approver channel creates the broker-owned record.

Operationally the initial trusted action is intentionally simple:

```bash
sudo /usr/libexec/dockerpilot-secure-broker/approve \
  --challenge <challenge_id> \
  --plan-sha256 <64-hex-plan-hash>
```

The root-only channel is a deliberate first implementation. It does not claim
that the compromised Extras UI is an independent trustworthy plan-display
channel. What the broker proves is narrower and explicit: a trusted local UID
outside the Extras threat boundary approved the exact broker-verified
`plan_sha256`.

INV-09 / end-to-end AT-13 is considered enforced on `main` for this threat
model after PR #63 merged with green CI and review. AT-13 remains a permanent
regression gate.

## Why not an HMAC key in Extras

A MAC/signing key readable by the Extras process does not cross a trust
boundary. Under Extras RCE the attacker receives the same key and can forge the
same approvals.

## Why Unix peer provenance first

A separate Unix socket keeps the first implementation stdlib-only and matches
the existing local root-broker deployment model. It protects specifically
against compromise of the `dockerpilot-extras` service account without adding
a Python cryptography runtime dependency to the root broker.

This does not claim protection against compromise of an allowed human/operator
UID. WebAuthn or an external hardware/private-key signer can be layered on later
if the threat model is expanded beyond the local UID boundary.
