"""Read-only GitHub Actions run/job watcher for DockerPilot MCP.

This adapter observes *existing* GitHub Actions runs; it cannot trigger,
rerun, cancel, or modify workflows. GitHub only publishes downloadable job
logs after jobs finish, so live feedback uses job/step statuses first.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import zlib
from urllib.parse import urlsplit

import requests


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SECRET = re.compile(
    r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b"
)
_MAX_JOBS = 200
_MAX_LOG_BYTES = 32768
_MAX_CURSOR_CHARS = 32768
_MAX_CURSOR_JSON_BYTES = 500000


class GitHubWatchError(ValueError):
    """Invalid arguments, disallowed repository, or GitHub API error."""


class GitHubActionsWatcher:
    def __init__(self, allowed_repos=None, token=None, session=None):
        raw = os.getenv("DOCKERPILOT_GITHUB_ALLOWED_REPOS", "")
        self.allowed = frozenset(allowed_repos if allowed_repos is not None
                                 else (part.strip() for part in raw.split(",") if part.strip()))
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        self.session = session if session is not None else requests.Session()

    def _path(self, repo, run_id):
        if not isinstance(repo, str) or not _REPO.fullmatch(repo) or ".." in repo:
            raise GitHubWatchError("Expected owner/repository")
        if repo not in self.allowed:
            raise GitHubWatchError("Repository is not in DOCKERPILOT_GITHUB_ALLOWED_REPOS")
        if type(run_id) is not int or run_id < 1:
            raise GitHubWatchError("run_id must be a positive integer")
        return f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"

    def _request(self, url, *, stream=False):
        # Never send GitHub authorization to arbitrary hosts.
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise GitHubWatchError("Only GitHub Actions API endpoints are allowed")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        response = self.session.get(url, headers=headers, timeout=12,
                                    allow_redirects=False, stream=stream)
        if response.status_code >= 400:
            raise GitHubWatchError("GitHub Actions API HTTP %d" % response.status_code)
        return response

    def _get_json(self, url):
        with self._request(url) as response:
            return response.json()

    def get_run(self, repo, run_id):
        base = self._path(repo, run_id)
        run = self._get_json(base)
        if run.get("repository", {}).get("full_name", repo) != repo:
            raise GitHubWatchError("GitHub returned a run from another repository")
        jobs = []
        for page in (1, 2):
            data = self._get_json(f"{base}/jobs?per_page=100&page={page}")
            chunk = data.get("jobs", [])
            if not isinstance(chunk, list):
                raise GitHubWatchError("Invalid jobs API response")
            jobs.extend(chunk)
            if len(chunk) < 100:
                break
        if len(jobs) >= _MAX_JOBS and data.get("total_count", 0) > _MAX_JOBS:
            raise GitHubWatchError("More than 200 jobs; refine the run")
        items = []
        for job in jobs:
            items.append({
                "id": job["id"], "name": job.get("name", ""),
                "status": job.get("status", ""),
                "conclusion": job.get("conclusion"),
                "steps": [
                    {"number": s.get("number"), "name": s.get("name", ""),
                     "status": s.get("status", ""),
                     "conclusion": s.get("conclusion")}
                    for s in (job.get("steps") or [])
                ],
            })
        return {
            "repository": repo, "run_id": run_id, "name": run.get("name"),
            "url": run.get("html_url"), "status": run.get("status"),
            "conclusion": run.get("conclusion"), "jobs": items,
        }

    @staticmethod
    def _signature(snapshot):
        return {
            "status": snapshot["status"], "conclusion": snapshot["conclusion"],
            "jobs": [
                [j["id"], j["status"], j["conclusion"],
                 [[s["number"], s["status"], s["conclusion"]] for s in j["steps"]]]
                for j in snapshot["jobs"]
            ],
        }

    def _logs(self, repo, job_id):
        # GitHub REST redirects completed job logs to a signed download URL.
        base = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
        with self._request(base) as redirect:
            if redirect.status_code != 302:
                raise GitHubWatchError("GitHub job logs not available yet")
            signed_url = redirect.headers.get("Location", "")
        destination = urlsplit(signed_url)
        host = (destination.hostname or "").lower()
        allowed = host.endswith(".actions.githubusercontent.com") or host.endswith(".blob.core.windows.net")
        if destination.scheme != "https" or not allowed or destination.username or destination.password:
            raise GitHubWatchError("GitHub log redirect refused: untrusted host")
        # Do not forward GitHub auth, cookies, or other headers to the signed host.
        with self.session.get(signed_url, timeout=12, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise GitHubWatchError("GitHub logs HTTP %d" % response.status_code)
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=4096):
                if total + len(chunk) > _MAX_LOG_BYTES:
                    chunks.append(chunk[:_MAX_LOG_BYTES-total])
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_LOG_BYTES:
                    break
        value = b"".join(chunks).decode("utf-8", "replace")
        return _SECRET.sub("[REDACTED]", value)

    def watch_run(self, repo, run_id, cursor=None, wait_seconds=0, include_logs=False):
        """Bounded watch returning jobs, steps, and newly completed-job logs.

        Cursor is returned by the prior call and is scoped to one repo/run ID.
        No perpetual server loop: clients invoke again using next_cursor.
        """
        self._path(repo, run_id)
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 25:
            raise GitHubWatchError("wait_seconds must be within 0..25")
        if cursor is None:
            previous = {}
        else:
            if not isinstance(cursor, str) or len(cursor) > _MAX_CURSOR_CHARS:
                raise GitHubWatchError("Invalid cursor")
            try:
                compressed = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
                decompressor = zlib.decompressobj()
                content = decompressor.decompress(compressed, _MAX_CURSOR_JSON_BYTES + 1)
                if (len(content) > _MAX_CURSOR_JSON_BYTES or not decompressor.eof
                        or decompressor.unused_data or decompressor.unconsumed_tail):
                    raise ValueError("Cursor payload limit exceeded")
                previous = json.loads(content)
                if not isinstance(previous, dict):
                    raise ValueError("Cursor must be an object")
            except (ValueError, TypeError, UnicodeError, zlib.error):
                raise GitHubWatchError("Invalid cursor") from None
            if previous.get("repo") != repo or previous.get("run") != run_id:
                raise GitHubWatchError("Cursor does not match repository/run")
        old = previous.get("signature")
        done_logs = set(previous.get("logged") or [])
        deadline = time.monotonic() + wait_seconds
        while True:
            data = self.get_run(repo, run_id)
            signature = self._signature(data)
            if old != signature or time.monotonic() >= deadline:
                break
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        logs = []
        if include_logs:
            for job in data["jobs"]:
                if job["status"] != "completed" or job["id"] in done_logs:
                    continue
                try:
                    body = self._logs(repo, job["id"])
                except (requests.RequestException, GitHubWatchError):
                    body = "(Job log temporarily unavailable)"
                if body == "(Job log temporarily unavailable)":
                    continue  # Retry on next invocation, not a permanent missed log.
                logs.append({"job_id": job["id"], "job": job["name"], "text": body})
                done_logs.add(job["id"])
        payload = json.dumps({
            "repo": repo, "run": run_id, "signature": signature,
            "logged": sorted(done_logs),
        }, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_CURSOR_JSON_BYTES:
            raise GitHubWatchError("Too many job/step states to serialize")
        current_cursor = base64.urlsafe_b64encode(zlib.compress(payload, level=6)).decode("ascii")
        if len(current_cursor) > _MAX_CURSOR_CHARS:
            raise GitHubWatchError("Workflow cursor exceeds supported limit")
        states = {"success": "✓", "failure": "✗", "in_progress": "▶", "queued": "○"}
        summary = [
            "%s %s (%s)" % (
                states.get(j["conclusion"] or j["status"], "○"),
                j["name"], j["conclusion"] or j["status"])
            for j in data["jobs"]
        ]
        return {
            **data, "changed": old != signature, "next_cursor": current_cursor,
            "logs_delta": logs, "summary_lines": summary,
            "completed": data["status"] == "completed",
        }
