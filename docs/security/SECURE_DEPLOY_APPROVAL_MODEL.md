# Secure Deploy Approval Model

This document describes the current broker-owned approval path. The broker protocol is v1; the authoritative broker approval record is version 2.

## Current approval v2

Approval authority is stored by the privileged broker, not by DockerPilotExtras.

### Lifecycle

1. Extras asks the broker to verify the plan and create a non-authorizing challenge.
2. The broker stores the challenge in its root-owned state directory.
3. An allowlisted local process confirms the exact challenge ID and plan_sha256 through the separate root-only approver socket.
4. The broker creates the authoritative approval record in its own ledger.
5. The control plane supplies only approval_id for later broker operations. The broker loads and validates the record itself.

Creating a challenge does not authorize execution. A client-created approval JSON object is not accepted as authority.

### Time limits

| Record | Current TTL | Purpose |
|---|---:|---|
| Approval challenge | 120 seconds | Short window to complete trusted approval |
| Broker approval | 600 seconds | Maximum current approval lifetime |

The plan has its own expiry. Plan-level expires_at is included in plan_sha256, so changing it changes the plan identity and invalidates an approval bound to the previous hash.

### Broker approval fields

The broker-owned v2 record binds:

- approval_version and approval_id;
- challenge_id;
- plan_id and plan_sha256;
- a broker-generated nonce;
- issue, approval, and expiry timestamps;
- status;
- provenance containing kind=unix_peer_uid and the UID obtained from SO_PEERCRED.

The broker checks that the approval is active, belongs to the requested plan ID and hash, has not expired, and was created by a UID still present in the root-owned allowlist.

### Approver transport

The approver socket is separate from the ordinary Extras control socket:

- /run/dockerpilot-secure-broker/approver.sock;
- root:root, mode 0600;
- Linux SO_PEERCRED supplies the peer UID;
- a root-owned broker configuration supplies allowed_approver_uids.

The initial production configuration allows UID 0. The operator client is installed as:

~~~bash
sudo /usr/libexec/dockerpilot-secure-broker/approve \
  --challenge <challenge_id> \
  --plan-sha256 <64-hex-plan-hash>
~~~

This authenticates an allowlisted local process identity. It does not establish which human invoked a root process. The repository currently provides no separate trusted plan viewer for the root approver; plan contents and hash must be verified out of band. Approval by hash alone is not proof that the plan contents were reviewed.

### Execution and replay controls

The control plane sends approval_id, not the approval record. The broker resolves the ID from its root-owned ledger.

A broker dry_run validates the approval but does not consume it. For the canary execution path, replay and one-shot execution state are tracked in the separate broker-owned canary ledger. The admission bundle binds the plan, hash, approval ID, and fixed template; the approval nonce is hashed into the execution record; guarded state transitions prevent a second distinct execution from reusing the same staged approval. A completed canary reaches a terminal state such as consumed.

Revocation and expiry are enforced against broker-owned state. An Extras-local record cannot create or reactivate a broker approval.

## Extras step-up TOTP

Selected Extras Secure Deploy endpoints require authentication, CSRF/origin checks, and step-up TOTP. That protects access to the web control-plane endpoints, including challenge creation and revocation.

The TOTP is not stored in the broker approval record and is not the broker's approval provenance. Under the Extras-RCE threat model, an attacker may bypass the web process's TOTP implementation. The independent approval boundary is the separate broker approver socket and its peer-UID allowlist.

## Legacy approval v1 compatibility

The older Extras-local ApprovalService record (approval version 1) remains local metadata for compatibility. It is not authoritative for broker-owned v2 execution. Its session hash, actor, and TOTP-related fields must not be described as the current broker approval boundary.

Historical dry-run notes that describe v1 actor/nonce behavior are evidence of the earlier implementation, not the current v2 contract. See [Approval Provenance](APPROVAL_PROVENANCE_V1.md) and the [broker threat model](SECURE_DEPLOY_BROKER_THREAT_MODEL.md).
