# Approval provenance v1

## Goal

Make a human/operator approval remain meaningful when the DockerPilot Extras
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

## Why the current approval is insufficient

The current Extras `ApprovalService` creates an `approved` record after the
web step-up flow. The broker checks schema, status, TTL and
`plan_id + plan_sha256`, but the broker has no independent proof that the
record was produced by a human step-up rather than by compromised Extras code.

Therefore a syntactically valid client-supplied approval object is not
broker-verifiable provenance.

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

The follow-up must:

1. add the dedicated approver socket and systemd unit,
2. label/separate activated broker sockets,
3. reject `dockerpilot-extras` on the approver channel,
4. derive approver identity from `SO_PEERCRED`,
5. add challenge creation to the non-authorizing control grammar,
6. route challenge approval only through the approver channel,
7. change dry-run/admission/execution authorization to broker-owned
   `approval_id`,
8. make client-supplied approval JSON non-authoritative,
9. move AT-13 from `FAIL_ARCHITECTURE` to `PASS`,
10. preserve AT-14 through AT-17 plan binding, replay, TTL and revocation
    semantics.

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
