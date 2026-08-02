# Dozeyguard

Offline static policy enforcer for Secure Deploy (DockerPilot).

## Role

Dozeyguard scans a normalized Compose JSON model against a TOML policy and emits
structured findings (DG001–DG026). It is used by:

- DockerPilotExtras Secure Deploy (plan generation / preview)
- Root Secure Deploy broker (`verify_plan` / `dry_run` revalidation)

It does **not** talk to the Docker daemon, open `docker.sock`, call the Docker
API, apply firewall rules, or materialize secrets.

## Contract

- **Input:** Compose-JSON (or Compose YAML where supported by the CLI) via
  `--input` / stdin, with an explicit byte limit (`--max-input-bytes`).
- **Output:** JSON report with `result.result_sha256`, exit codes `0` / `1` / `2`.
- **Policy:** broker-owned or Extras-owned TOML path — never request-controlled.

See `docs/JSON_CONTRACT_V1.md` for the JSON contract details.

## Build / test

From the DockerPilot repo root:

```bash
cargo fmt --check --manifest-path components/dozeyguard/Cargo.toml
cargo clippy --manifest-path components/dozeyguard/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path components/dozeyguard/Cargo.toml
cargo build --release --manifest-path components/dozeyguard/Cargo.toml
```

Release binary: `components/dozeyguard/target/release/dozeyguard` (local build only).

**Do not commit `target/`.** It is gitignored.

## Integration with root broker

`tools/secure_deploy/build_root_broker_staging.py` builds the release binary from
`components/dozeyguard` (or `DOZEYGUARD_SRC` override) and stages it under
`/usr/libexec/dockerpilot-secure-broker/bin/dozeyguard` in the install bundle.

Runtime broker and Extras adapter must use an **installed / staged binary** path
(`DOZEYGUARD_BIN` / broker config), never `components/dozeyguard/target/...`.

## Known follow-ups

Bounded compose input (`read_bounded` / metadata precheck) is implemented (#11B.1).
Policy files still use a normal text read (small TOML); compose payloads never use
unbounded `fs::read`.
