# DockerPilot MCP (Model Context Protocol)

DockerPilot includes an **optional MCP server** that exposes safe, structured tools for:
- inspecting Docker state (containers/images/networks/volumes),
- debugging containers faster (inspect + logs + stats + diagnosis),
- performing **controlled** container actions (start/stop/restart/exec), guarded by policy.

This MCP server is **separate from the CLI** and does not reuse interactive/TUI code paths.

## Install

From the repo root:

```bash
pip install -e .[mcp]
```

Optional (encryption-at-rest for migration bundles):
```bash
pip install -e .[mcp-crypto]
```
For `DOCKERPILOT_MCP_MIGRATION_ENCRYPTION=age`, you also need the `age` CLI installed on the host.

## Run (stdio)

```bash
dockerpilot-mcp
```

Also supported:

```bash
python -m dockerpilot.mcp.server
```

## Client config examples

Generic MCP client (stdio):

```json
{
  "mcpServers": {
    "dockerpilot": {
      "command": "dockerpilot-mcp",
      "env": {
        "DOCKERPILOT_MCP_READONLY": "true",
        "DOCKERPILOT_MCP_MAX_LOG_LINES": "300"
      }
    }
  }
}
```

Local trusted admin mode (still safe by default):

```json
{
  "mcpServers": {
    "dockerpilot-admin": {
      "command": "dockerpilot-mcp",
      "env": {
        "DOCKERPILOT_MCP_READONLY": "false",
        "DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE": "false",
        "DOCKERPILOT_MCP_ALLOWED_CONTAINERS": "dev-,test-,sandbox-",
        "DOCKERPILOT_MCP_DENIED_CONTAINERS": "gitlab,db2,homeassistant,prod"
      }
    }
  }
}
```

## Environment variables

- `DOCKERPILOT_MCP_READONLY` (default: `true`)  
  Blocks *all* state-changing tools when `true`.
- `DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE` (default: `false`)  
  Required (in addition to `READONLY=false`) for destructive tools like remove/prune.
- `DOCKERPILOT_MCP_ALLOWED_CONTAINERS` (default: empty = allow all)  
  Comma-separated prefixes or exact names (prefix match).
- `DOCKERPILOT_MCP_DENIED_CONTAINERS` (default: empty)  
  Comma-separated prefixes or exact names. **Deny wins over allow.**
- `DOCKERPILOT_MCP_MAX_LOG_LINES` (default: `200`)  
  Maximum log lines returned by `dockerpilot_container_logs`.
- `DOCKERPILOT_MCP_EXEC_TIMEOUT` (default: `10`)  
  Timeout (seconds) for `dockerpilot_container_exec`.
- `DOCKERPILOT_MCP_REDACT_SECRETS` (default: `true`)  
  Redacts secrets from env/labels/logs/inspect where possible. When `true`, redaction is **forced globally** even if a tool exposes a `redact` argument.
- `DOCKERPILOT_MCP_MIGRATIONS_DIR` (default: `~/.dockerpilot/migrations`)  
  Default output directory for migration bundle exports.
- `DOCKERPILOT_MCP_MIGRATION_TIMEOUT` (default: `300`)  
  Timeout (seconds) for migration helper containers used to export/restore named volumes.
- `DOCKERPILOT_MCP_MIGRATION_MAX_BUNDLE_BYTES` (default: `2147483648`)  
  Maximum allowed bundle size. Export fails if exceeded.
- `DOCKERPILOT_MCP_MIGRATION_ALLOW_ARBITRARY_OUTPUT_DIR` (default: `false`)  
  If `false`, `output_dir` for bundle export must be within `DOCKERPILOT_MCP_MIGRATIONS_DIR`.
- `DOCKERPILOT_MCP_MIGRATION_ENCRYPTION` (default: unset/disabled)  
  Enable bundle encryption at rest. Supported values: `aesgcm` (or `true`) and `age`. Export writes `*.tar.enc` (aesgcm) or `*.tar.age` (age). Import accepts `.enc` / `.age` bundles.
- `DOCKERPILOT_MCP_MIGRATION_PASSPHRASE`  
  Passphrase for bundle encryption/decryption (avoid putting this in shared client configs).
- `DOCKERPILOT_MCP_MIGRATION_PASSPHRASE_FILE`  
  Path to a file containing the passphrase (recommended over env var).
- `DOCKERPILOT_MCP_MIGRATION_AGE_RECIPIENTS`  
  Comma-separated age recipients (e.g. `age1...`). Required for `DOCKERPILOT_MCP_MIGRATION_ENCRYPTION=age`.
- `DOCKERPILOT_MCP_MIGRATION_AGE_IDENTITY_FILE`  
  Path to an age identity file for decrypting `.age` bundles. Required for imports of `.age` bundles.

## Safety model (v1)

1. Read-only tools work by default.
2. Any state-changing tool is blocked when `DOCKERPILOT_MCP_READONLY=true`.
3. Destructive tools require `DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true` as well.
4. State-changing tools require an explicit `confirm: true`.
5. Destructive tools default to `dry_run: true`.
6. Container targeting is checked against allow/deny rules (deny wins).
7. No host shell is exposed; exec is container-scoped only.

## Safe migration (bundle export/import)

DockerPilot MCP can migrate a container **between hosts** using a safe “bundle” workflow:
1) On the *source* host, call `dockerpilot_migration_export_bundle` to write a bundle `.tar` to disk.
2) Copy that bundle to the *target* host out-of-band (scp/rsync/Tailscale/etc).
3) On the *target* host, call `dockerpilot_migration_import_bundle` with `dry_run=true`, then re-run with `dry_run=false`.

Defaults and safety:
- `include_data=true` by default: named volumes are exported/restored.
- Bind mounts are **not** included by default.
- **Env values are not restored** (secrets redaction); you must re-inject secrets safely on the target host (e.g. via `env_file`, secret manager, or your deployment configs).
- Import does **not** remove/replace an existing target container by default; it fails fast if the target name already exists.
  - You can set `on_conflict=\"rename\"` to auto-pick a new name.
  - `on_conflict=\"replace\"` is destructive and requires `DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true`.

### Important: secrets and mounts

- Migration bundles never include unredacted secret values from env/labels.
- Bind mounts are skipped: host paths often contain secrets or machine-specific data and are not portable.

## Example prompts

- “Diagnose why container `X` is unhealthy”
- “List containers and explain risky states”
- “Show last 200 redacted logs for `X`”
- “Restart `X` only if the dry run says it is allowed”
