# DockerPilot release process

DockerPilot uses one monorepo release train for the CLI, DockerPilotExtras frontend
and the broker-shipped DozeyGuard binary.

## Version policy

The release version must match in:

- `pyproject.toml`
- `src/dockerpilot/__init__.py`
- `DockerPilotExtras/frontend/package.json`
- `DockerPilotExtras/frontend/package-lock.json`
- `components/dozeyguard/Cargo.toml`
- `components/dozeyguard/Cargo.lock`
- active broker client defaults and the README release footer

`tests/test_project_metadata.py` is the CI guard for this contract. Frozen
historical fixtures and dated implementation reports may intentionally preserve
the version they were created with.

## Pre-release stages

- `pre.N` builds are development/UAT candidates.
- A version bump alone does not make Secure Deploy production-ready.
- Production claims require a separately reviewed production acceptance gate and
  real-host validation of the installed systemd broker, Docker integration and
  rollback/recovery behavior.

The current target is `0.9.0-pre.3`.

## Release gate

Before tagging a release candidate:

1. Merge the version/docs preparation PR into `main`.
2. Require the complete GitHub CI matrix to pass, including Python, Windows
   smoke, Extras backend/frontend and DozeyGuard Rust checks.
3. Require Demo smoke to pass.
4. Confirm `tests/test_project_metadata.py` is green so release metadata is
   synchronized.
5. Confirm the security status documents describe the code currently on
   `main`, especially `SECURITY_INVARIANTS.md`,
   `ADVERSARIAL_TEST_PLAN.md` and `APPROVAL_PROVENANCE_V1.md`.
6. Review `CHANGELOG.md` for the exact release contents.
7. Tag only the reviewed `main` commit, for example:
   `git tag -a v0.9.0-pre.3 -m "DockerPilot v0.9.0-pre.3"`.

Do not tag a branch commit that has not been merged and reviewed.

## DozeyGuard

DozeyGuard follows the DockerPilot monorepo release version because the installed
root-broker bundle ships that binary as part of the same Secure Deploy release.
Its JSON `contract_version` remains independently versioned; changing the
application release version does not by itself change the JSON contract.

Rust release checks remain:

```bash
cargo fmt --check --manifest-path components/dozeyguard/Cargo.toml
cargo clippy --manifest-path components/dozeyguard/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path components/dozeyguard/Cargo.toml
cargo build --release --manifest-path components/dozeyguard/Cargo.toml
```

## Security documentation

Dated audit reports remain historical evidence. When a later PR remediates a
finding, update the current status section instead of rewriting the original
finding as though it never existed.
