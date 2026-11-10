# DockerPilot MCP (Model Context Protocol)

DockerPilot includes an **optional MCP server** that exposes structured tools for:

- inspecting Docker state (containers, images, networks, and volumes);
- debugging containers with inspect, logs, stats, and diagnosis;
- performing controlled container actions (start, stop, restart, and container-scoped exec), subject to runtime policy.

The MCP server is separate from the CLI and does not reuse interactive/TUI code paths.

## Install

From the repository root:

~~~bash
pip install -e ".[mcp]"
~~~

Optional (encryption-at-rest for migration bundles):

~~~bash
pip install -e ".[mcp-crypto]"
~~~

For DOCKERPILOT_MCP_MIGRATION_ENCRYPTION=age, the age CLI must also be installed on the host.

## Run (stdio)

~~~bash
dockerpilot-mcp
~~~

Also supported:

~~~bash
python -m dockerpilot.mcp.server
~~~

## Client config examples

Generic MCP client (read-only default):

~~~json
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
~~~

Local operator mode with container allow/deny filters:

~~~json
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
~~~

The second example is still a privileged local-operator configuration if the MCP process has Docker daemon access. Container filters reduce the allowed target set; they do not turn Docker access into a low-privilege capability.

## Environment variables

- DOCKERPILOT_MCP_READONLY (default: true)
  Blocks all state-changing tools when true.
- DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE (default: false)
  Required, in addition to READONLY=false, for destructive tools such as remove/prune.
- DOCKERPILOT_MCP_ALLOWED_CONTAINERS (default: empty = allow all)
  Comma-separated prefixes or exact names (prefix match).
- DOCKERPILOT_MCP_DENIED_CONTAINERS (default: empty)
  Comma-separated prefixes or exact names. Deny wins over allow.
- DOCKERPILOT_MCP_MAX_LOG_LINES (default: 200)
  Maximum log lines returned by dockerpilot_container_logs.
- DOCKERPILOT_MCP_EXEC_TIMEOUT (default: 10)
  Timeout in seconds for dockerpilot_container_exec.
- DOCKERPILOT_MCP_REDACT_SECRETS (default: true)
  Forces redaction of secret-like values in environment, labels, logs, and inspect output where supported.
- DOCKERPILOT_MCP_MIGRATIONS_DIR (default: ~/.dockerpilot/migrations)
  Default output directory for migration bundle exports.
- DOCKERPILOT_MCP_MIGRATION_TIMEOUT (default: 300)
  Timeout in seconds for migration helper containers used to export/restore named volumes.
- DOCKERPILOT_MCP_MIGRATION_MAX_BUNDLE_BYTES (default: 2147483648)
  Maximum allowed bundle size.
- DOCKERPILOT_MCP_MIGRATION_ALLOW_ARBITRARY_OUTPUT_DIR (default: false)
  If false, bundle export output must remain within DOCKERPILOT_MCP_MIGRATIONS_DIR.
- DOCKERPILOT_MCP_MIGRATION_ENCRYPTION (default: unset/disabled)
  Supported values: aesgcm (or true) and age.
- DOCKERPILOT_MCP_MIGRATION_PASSPHRASE
  Passphrase for bundle encryption/decryption. Avoid putting it in shared client configs.
- DOCKERPILOT_MCP_MIGRATION_PASSPHRASE_FILE
  Path to a file containing the passphrase; prefer this over an environment variable.
- DOCKERPILOT_MCP_MIGRATION_AGE_RECIPIENTS
  Comma-separated age recipients. Required for age encryption.
- DOCKERPILOT_MCP_MIGRATION_AGE_IDENTITY_FILE
  Path to an age identity file for importing .age bundles.

## Safety model

1. Read-only tools work by default.
2. State-changing tools are blocked when DOCKERPILOT_MCP_READONLY=true.
3. Destructive tools also require DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true.
4. State-changing tools require an explicit confirm: true argument.
5. Destructive tools default to dry_run: true.
6. Container targeting is checked against allow/deny rules; deny wins.
7. No host shell is exposed; exec is container-scoped.

**Important:** confirm: true is an acknowledgement supplied by the same MCP caller requesting the action. It is not an independent human approval factor and must not be presented as proof that a trusted operator approved the operation. MCP is not the Secure Deploy broker.

The MCP process still uses its own operating-system identity and Docker permissions. Membership in the docker group or unrestricted access to docker.sock is effectively root-equivalent. Do not give those permissions to an AI-facing process unless that authority is explicitly intended and controlled.

## Safe migration (bundle export/import)

DockerPilot MCP can migrate a container between hosts using a bundle workflow:

1. On the source host, call dockerpilot_migration_export_bundle to write a bundle to disk.
2. Copy that bundle to the target host out-of-band (for example, scp or rsync).
3. On the target host, call dockerpilot_migration_import_bundle with dry_run=true, then repeat with dry_run=false after reviewing the result.

Defaults and safety:

- include_data=true by default: named volumes are exported/restored.
- Bind mounts are not included by default.
- Environment values are not restored; secrets must be reinjected safely on the target host.
- Import does not remove or replace an existing target container by default. It fails if the target name already exists.
- on_conflict=rename chooses a new name.
- on_conflict=replace is destructive and requires DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true.

### Secrets and mounts

- Migration bundles do not include unredacted secret values from environment variables or labels.
- Bind mounts are skipped because host paths often contain secrets or machine-specific data and are not portable.

## Example prompts

- “Diagnose why container X is unhealthy.”
- “List containers and explain risky states.”
- “Show the last 200 redacted log lines for X.”
- “Restart X only if the dry run says it is allowed.”
