# Dozeyguard JSON Contract v1

`contract_version: 1` is the stable machine-readable report emitted by:

```bash
dozeyguard scan --input compose.json --input-format compose-json \
  --policy policy.toml --output json
```

## Exit codes (compatibility — unchanged)

| Code | Meaning | `result.status` |
|------|---------|-----------------|
| 0 | No non-excepted finding at or above `--fail-on` | `pass` (no findings) or `warn` (non-blocking findings) |
| 1 | Input, policy, limit, or I/O error | `error` |
| 2 | Blocking policy violation | `fail` |

This is **not** remapped to “1 = warn”. Warn conditions still exit `0` and set `result.status` to `warn`.

## Envelope

Top-level fields:

- `contract_version` (uint, currently `1`)
- `scanner.name` / `scanner.version`
- `input.format` (`compose-json`), `input.sha256`, `input.bytes`
- `policy.sha256`, `policy.exceptions_applied` (`service:rule` list, sorted)
- `summary` — `services`, `findings`, `blocking`, `warnings`, `info`
- `findings[]` — ordered by `service → rule_id → path → message`
- `result.status`, `result.exit_code`, optional `result.error`, `result.result_sha256`

Finding object:

- `rule_id`, `severity`, `blocking`, `service`, `path`, `message`, `remediation`
- `exception`: `null` or `{ "status": "active"|"expired" }`

## Determinism and `result_sha256`

1. Build the logical report **without** `result.result_sha256` and **without** timestamps.
2. Canonicalize JSON: UTF-8, object keys sorted lexicographically, arrays keep order, no insignificant whitespace for the hash payload (`serde_json` value walk).
3. SHA-256 → lowercase hex → store as `result.result_sha256`.

Identical input bytes + policy bytes + scanner version + fail-on outcome ⇒ identical `result_sha256`.

Error envelopes exclude the human `error.message` from the hashed payload so wording changes do not alter the digest of an empty scan context.

## Limits

| Limit | Value |
|-------|-------|
| Default `--max-input-bytes` | 2 MiB (`2097152`) |
| Hard maximum (compile-time) | 16 MiB (`16777216`) |

Oversized input → exit `1`, JSON `error` with code `input_too_large` when `--output json`.

## Secrets

Environment values and secret contents are never printed in findings, envelopes, or stderr. Parser errors never echo input fragments.

## DG026

`cap_add` containing `ALL` (any case, string or list, with duplicates/whitespace) is **Critical** and blocking (`DG026`). Named dangerous caps remain `DG007`.
