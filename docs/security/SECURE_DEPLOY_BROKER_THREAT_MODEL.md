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
