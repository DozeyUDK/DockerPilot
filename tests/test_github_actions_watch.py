"""Mocked watcher tests: never invoke GitHub or Docker during unit tests."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dockerpilot.ci.github_actions import GitHubActionsWatcher, GitHubWatchError


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
    def json(self):
        return self.data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def iter_content(self, chunk_size=4096):
        yield b"job test succeeded\n"


class Session:
    def __init__(self):
        self.calls = []
        self.state = "in_progress"
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "/jobs/" in url and url.endswith("/logs"):
            return Response(status=302, headers={"Location": "https://log.actions.githubusercontent.com/signed"})
        if url.startswith("https://log.actions.githubusercontent.com/"):
            assert "Authorization" not in kwargs.get("headers", {})
            return Response()
        if url.endswith("?per_page=100&page=1"):
            return Response({"total_count": 1, "jobs": [
                {"id": 10, "name": "CI", "status": self.state,
                 "conclusion": "success" if self.state == "completed" else None,
                 "steps": [{"number": 1, "name": "pytest", "status": self.state,
                            "conclusion": "success" if self.state == "completed" else None}]}
            ]})
        if "/runs/5" in url and "?" not in url:
            return Response({"repository": {"full_name": "DozeyUDK/kafka-python-monitor"},
                             "status": self.state, "conclusion": "success" if self.state == "completed" else None,
                             "name": "Tests", "html_url": "https://github.com/test/actions/runs/5"})
        return Response({"total_count": 1, "jobs": []})


def watcher():
    s = Session()
    return GitHubActionsWatcher(allowed_repos=["DozeyUDK/kafka-python-monitor"], token="private", session=s), s


def test_bounded_watch_tracks_job_steps_and_deltas():
    w, session = watcher()
    one = w.watch_run("DozeyUDK/kafka-python-monitor", 5)
    assert one["changed"] and not one["completed"]
    assert one["jobs"][0]["steps"][0]["name"] == "pytest"
    unchanged = w.watch_run("DozeyUDK/kafka-python-monitor", 5, cursor=one["next_cursor"])
    assert not unchanged["changed"]
    session.state = "completed"
    done = w.watch_run("DozeyUDK/kafka-python-monitor", 5, cursor=unchanged["next_cursor"], include_logs=True)
    assert done["completed"] and done["changed"]
    assert len(done["logs_delta"]) == 1
    again = w.watch_run("DozeyUDK/kafka-python-monitor", 5, cursor=done["next_cursor"], include_logs=True)
    assert not again["logs_delta"]


def test_token_not_forwarded_to_signed_log_host():
    w, session = watcher()
    session.state = "completed"
    result = w.watch_run("DozeyUDK/kafka-python-monitor", 5, include_logs=True)
    assert result["logs_delta"]
    for url, kw in session.calls:
        if "githubusercontent.com" in url:
            assert not kw.get("headers", {}).get("Authorization")


@pytest.mark.parametrize("repo,run", [
    ("../etc", 5), ("attacker/repository", 5),
    ("DozeyUDK/kafka-python-monitor", -1),
    ("DozeyUDK/kafka-python-monitor", True),
])
def test_untrusted_repos_or_run_ids_blocked(repo, run):
    w, session = watcher()
    with pytest.raises(GitHubWatchError):
        w.get_run(repo, run)
    assert session.calls == []


def test_cursor_cannot_be_reused_for_another_run():
    w, _ = watcher()
    first = w.watch_run("DozeyUDK/kafka-python-monitor", 5)
    with pytest.raises(GitHubWatchError):
        w.watch_run("DozeyUDK/kafka-python-monitor", 6, cursor=first["next_cursor"])


def test_missing_allowlist_fails_closed(monkeypatch):
    monkeypatch.delenv("DOCKERPILOT_GITHUB_ALLOWED_REPOS", raising=False)
    w = GitHubActionsWatcher(session=Session())
    with pytest.raises(GitHubWatchError):
        w.get_run("DozeyUDK/kafka-python-monitor", 5)


def test_reject_untrusted_log_redirect():
    w, s = watcher()
    class EvilSession(Session):
        def get(self, url, **kwargs):
            if url.endswith("/logs"):
                return Response(status=302, headers={"Location": "http://127.0.0.1/secret"})
            return super().get(url, **kwargs)
    w.session = EvilSession()
    assert "refused" in w._logs("DozeyUDK/kafka-python-monitor", 10)


def test_wait_seconds_guard():
    w, s = watcher()
    with pytest.raises(GitHubWatchError):
        w.watch_run("DozeyUDK/kafka-python-monitor", 5, wait_seconds=26)
    assert s.calls == []
