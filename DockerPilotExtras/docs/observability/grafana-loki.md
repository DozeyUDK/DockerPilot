# DockerPilot Extras logging and Grafana Loki

DockerPilot Extras writes structured application logs locally. Grafana, Loki, and Grafana Alloy are optional integrations: Extras does not start, configure, or depend on any of them.

## Local logging

By default, the backend writes one JSON object per line to:

~~~text
~/.dockerpilot_extras/logs/extras.jsonl
~~~

Defaults:

| Setting | Default |
| --- | --- |
| File log level | **INFO** |
| Console log level | **WARNING** |
| Rotation threshold | 10 MiB |
| Retained rotated files | 5 |
| Log directory mode | 0700 |
| Log file mode | 0600 |
| Slow request threshold | 1500 ms |
| Debug reloader | Disabled |

Successful GET polling requests are not written as access-log entries. HTTP errors, slow requests, and successful state-changing requests are recorded. Each request receives an X-Request-ID response header; a valid incoming X-Request-ID is preserved. Application events emitted inside that request inherit the same request_id field for correlation in Loki.

The backend redacts common secret assignments and environment options before serializing messages and exception text. Do not use application logs as a place to print credentials, request bodies, private keys, or raw SSH commands.

### Configuration

Set these variables in the environment of the process that starts Extras:

| Variable | Purpose |
| --- | --- |
| DOCKERPILOT_EXTRAS_LOG_DIR | Log directory |
| DOCKERPILOT_EXTRAS_LOG_LEVEL | File level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| DOCKERPILOT_EXTRAS_CONSOLE_LEVEL | Console level; defaults to WARNING |
| DOCKERPILOT_EXTRAS_LOG_MAX_BYTES | Rotation threshold in bytes |
| DOCKERPILOT_EXTRAS_LOG_BACKUP_COUNT | Number of rotated files to retain |
| DOCKERPILOT_EXTRAS_LOG_DIR_MODE | Directory permissions in octal, for example 0700 |
| DOCKERPILOT_EXTRAS_LOG_FILE_MODE | File permissions in octal, for example 0600 |
| DOCKERPILOT_EXTRAS_SLOW_REQUEST_MS | Slow-request warning threshold |
| DOCKERPILOT_EXTRAS_DEBUG | Set to true only to enable Flask debug mode and reloader |

Example:

~~~bash
export DOCKERPILOT_EXTRAS_LOG_LEVEL=INFO
export DOCKERPILOT_EXTRAS_CONSOLE_LEVEL=WARNING
export DOCKERPILOT_EXTRAS_LOG_MAX_BYTES=10485760
export DOCKERPILOT_EXTRAS_LOG_BACKUP_COUNT=5

tail -f ~/.dockerpilot_extras/logs/extras.jsonl
~~~

The log directory is created automatically with the configured mode. Existing directories are never chmodded by Extras: if an existing directory is more permissive than the configured mode, file logging is disabled and you must correct its permissions explicitly. Symlink components and directories writable by untrusted users are rejected to prevent redirected log writes. Use a dedicated directory for Extras rather than pointing the setting at a shared system directory such as `/var/log` itself.

The log directory is created automatically. If file logging cannot be initialized, Extras continues with console logging and emits a warning.

## Send logs to an existing Grafana + Loki + Alloy

This is the recommended path when Alloy and Loki already exist. No new Grafana, Loki, container, or port is required.

### 1. Give Alloy read access to the log file

The default log directory and file are private to the Extras account (0700 and 0600). Do not make them world-readable just to let Alloy scrape them.

A group-based setup is suitable for a systemd installation. Replace APP_USER with the Linux account running Extras:

~~~bash
APP_USER=dozey
LOG_DIR=/var/log/dockerpilot-extras

sudo groupadd --system dockerpilot-logs 2>/dev/null || true
sudo usermod -aG dockerpilot-logs "$APP_USER"
sudo usermod -aG dockerpilot-logs alloy
sudo install -d -o "$APP_USER" -g dockerpilot-logs -m 2750 "$LOG_DIR"
~~~

Configure Extras with:

~~~bash
DOCKERPILOT_EXTRAS_LOG_DIR=/var/log/dockerpilot-extras
DOCKERPILOT_EXTRAS_LOG_DIR_MODE=2750
DOCKERPILOT_EXTRAS_LOG_FILE_MODE=0640
~~~

The setgid bit on the directory makes newly created and rotated files inherit the shared group. Restart the Extras service after changing its environment and restart Alloy after adding its account to the group so both processes receive their new supplementary groups.

If Alloy runs in a container, mount the log directory read-only and ensure the container's Alloy UID/GID has the required directory traversal and file-read permissions.

### 2. Add a file source to the existing Alloy configuration

Append this component to the existing Alloy configuration. It uses Alloy's built-in file discovery and tails the active file and rotated JSONL files:

~~~alloy
loki.source.file "dockerpilot_extras" {
  targets = [
    {
      "__path__"     = "/var/log/dockerpilot-extras/extras.jsonl*",
      "job"          = "dockerpilot-extras",
      "service_name" = "dockerpilot-extras",
    },
  ]

  forward_to = [loki.process.dockerpilot_extras.receiver]

  file_match {
    enabled = true
    sync_period = "10s"
  }
}
~~~

Add the processing stage below as well. It extracts the JSON timestamp and uses it as the Loki event timestamp. This matters when Alloy replays buffered log files after an outage; without it, old events would appear as newly ingested events in time-window dashboards.

~~~alloy
loki.process "dockerpilot_extras" {
  stage.json {
    expressions = {
      timestamp = "timestamp",
    }
  }

  stage.timestamp {
    source = "timestamp"
    format = "RFC3339Nano"
    action_on_failure = "skip"
  }

  forward_to = [loki.write.default.receiver]
}
~~~

Change loki.write.default.receiver to the receiver of the Loki writer already present in your configuration. The writer label is installation-specific; do not add a second writer if the existing one already sends to the intended Loki instance.
Do not set tail_from_end=true here. Alloy's default is to read from the beginning when a file has no saved position. This is intentional: if Alloy was stopped during log rotation, it can still collect unread content from a newly discovered rotated file such as extras.jsonl.1. On first setup, expect Alloy to ingest the retained JSONL history.

Keep Alloy's configured storage path (the directory holding its positions file) on persistent storage. If that positions state is lost, Alloy can replay retained files from the beginning; this avoids silently skipping data but can create duplicate log entries. The collector is therefore at-least-once, not exactly-once.

 The writer label is installation-specific; do not add a second writer if the existing one already sends to the intended Loki instance.

If your existing Alloy configuration uses a different log directory, update __path__ to match DOCKERPILOT_EXTRAS_LOG_DIR.

Validate and reload Alloy using the method configured for your installation. If Alloy's HTTP reload endpoint is enabled, the usual local reload request is:

~~~bash
curl -X POST http://127.0.0.1:12345/-/reload
~~~

### 3. Check the stream in Grafana

Open **Grafana → Explore**, select your existing Loki data source, and run:

All DockerPilot Extras events:

~~~logql
{job="dockerpilot-extras"} | json
~~~

Errors and critical events:

~~~logql
{job="dockerpilot-extras"} | json | level=~"ERROR|CRITICAL"
~~~

Remote-operation failures and recoveries:

~~~logql
{job="dockerpilot-extras"} | json | event=~"remote_operation_failed|remote_operation_restored"
~~~

HTTP errors and slow requests:

~~~logql
{job="dockerpilot-extras"} | json | event="http_request" | http_status >= 400
~~~

### 4. Import the DockerPilot Extras dashboard

Import observability/grafana/dockerpilot-extras-dashboard.json from this repository:

1. Open **Dashboards → New → Import**.
2. Upload the JSON file.
3. Select the existing Loki data source when prompted.
4. Import the dashboard.

The dashboard includes log volume by level, error events, remote-operation failures, and a searchable log stream.

## Other installation layouts

### Grafana + Loki, but no Alloy

Install Grafana Alloy on the host that can read the Extras log file. Use the complete example in observability/alloy/dockerpilot-extras.alloy, set its Loki push URL to your existing Loki endpoint, and configure the same file permissions described above.

If Loki is remote, use its HTTPS endpoint and the authentication settings required by that Loki deployment. Keep credentials in the Alloy service environment or secret manager, not in a committed configuration file.

### Grafana only (no Loki)

Grafana is the visualization layer; it does not receive and store application log files by itself. Add a Loki log store and an Alloy collector, then follow the steps above. DockerPilot Extras will continue to work normally if those services are unavailable.

## Troubleshooting

- **No stream appears:** confirm the file exists, the Alloy process can traverse every parent directory, and the Alloy user can read the active and rotated files.
- **Permission denied:** check namei -l /var/log/dockerpilot-extras/extras.jsonl, group membership, and the service's supplementary groups.
- **Alloy is healthy but Loki is empty:** inspect Alloy component health and logs, then verify the loki.write endpoint and credentials.
- **Only current logs appear:** ensure the glob includes extras.jsonl* and Alloy has access to rotated files.
- **Too much detail:** leave file level at INFO and console level at WARNING. DEBUG is intended for short, controlled troubleshooting sessions.

Loki retention is controlled by the Loki deployment, not by DockerPilot Extras. Keep the Loki endpoint private or authenticated; do not expose an unauthenticated push endpoint to the public Internet.

## References

- [Grafana Alloy: monitor logs from a file](https://grafana.com/docs/alloy/latest/monitor/monitor-logs-from-file/)
- [Grafana Alloy: loki.source.file](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/)
- [Grafana Alloy: loki.write](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/)
