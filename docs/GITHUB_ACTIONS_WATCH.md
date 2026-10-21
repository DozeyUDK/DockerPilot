# GitHub Actions live watcher (read-only)

DockerPilot MCP now exposes two **read-only** tools:

- `dockerpilot_github_actions_get_run(repository, run_id)` — current run, jobs, and steps.
- `dockerpilot_github_actions_watch_run(repository, run_id, cursor=None, wait_seconds=0, include_logs=False)` — bounded poll (0–25 seconds), incremental status and logs of completed jobs; call again with `next_cursor`.

### Configure the MCP server

Set an explicit repository allowlist in the **runtime environment**, not in Git:

```bash
export DOCKERPILOT_GITHUB_ALLOWED_REPOS=DozeyUDK/kafka-python-monitor,DozeyUDK/DockerPilot
# Optional: inject a read-only GitHub token through the deployment secret store.
# For public repos, the watcher also works without a token at reduced rate limits.
```

If a token is configured via `GITHUB_TOKEN`, it must have **Actions: read** (and repository metadata read) only. The watcher never triggers a build, reruns jobs, downloads artifacts, or changes repository settings. Empty allowlist fails closed; arbitrary repository names and URLs are not accepted. Server calls go only to `api.github.com`.

### Example usage

1. Get the workflow run ID from a GitHub Actions run URL, e.g. `https://github.com/DozeyUDK/kafka-python-monitor/actions/runs/123456789`.
2. Call `dockerpilot_github_actions_get_run("DozeyUDK/kafka-python-monitor", 123456789)`.
3. Call `dockerpilot_github_actions_watch_run(..., wait_seconds=15)`. Pass the returned `next_cursor` on subsequent calls.
4. When `completed=true`, stop polling. Display `summary_lines` for phase/status updates and `logs_delta` for new job logs.

**GitHub limitation:** the Actions API exposes job and step status while execution is ongoing, but downloadable job logs are typically published only after a job completes. This adapter shows live **progress** and delivers logs incrementally **between completed jobs**; it cannot stream individual stdout lines from an active GitHub Actions step.

**Secrets and network safety:** secrets stay in the server's environment; the GitHub token is sent only to `https://api.github.com`. Job-log redirects are accepted only over HTTPS to GitHub Actions/blob endpoints and are fetched without the GitHub Authorization header. Logs are capped at 32 KiB per job, with known token patterns redacted. They can still contain application-sensitive output: keep MCP transport and access controls private. No GitHub content is executed.

The existing `dockerpilot_watch_test_run` tool watches local Kubernetes-backed DockerPilot test profiles and remains unchanged. The new GitHub tools are separate; **install and restart the updated DockerPilot MCP server to expose them**. A GitHub PR alone cannot dynamically update the active ChatGPT connector.
