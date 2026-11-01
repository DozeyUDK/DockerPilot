"""Regression tests for duplicate multi-target CLI and interactive actions."""

from argparse import Namespace

import pytest

from dockerpilot.cli.handlers import handle_container_cli
from dockerpilot.cli.interactive import run_interactive_menu
from dockerpilot.services.runtime_support import parse_multi_target


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message, **_kwargs):
        self.messages.append(str(message))


class FakePilot:
    _parse_multi_target = staticmethod(parse_multi_target)

    def __init__(self, failing=None):
        self.console = FakeConsole()
        self.calls = []
        self.failing = set(failing or ())

    def list_containers(self, **_kwargs):
        return []

    def container_operation(self, action, name, **kwargs):
        self.calls.append((action, name))
        return name not in self.failing

    def stop_and_remove_container(self, name, timeout):
        self.calls.append(("stop-remove", name))
        return name not in self.failing


def test_multi_target_parser_skips_duplicates_after_trimming():
    raw = " b44460c48d37,6e36c984d137, 6e36c984d137 ,b44460c48d37,,different "
    assert parse_multi_target(raw) == ["b44460c48d37", "6e36c984d137", "different"]
    assert parse_multi_target(" , , ") == []


@pytest.mark.parametrize("action", ["remove", "stop-remove"])
def test_cli_runs_each_container_only_once(action):
    pilot = FakePilot()
    args = Namespace(container_action=action, name="first,first, second,first", force=True, timeout=10)

    handle_container_cli(pilot, args)

    assert pilot.calls == [(action, "first"), (action, "second")]
    assert any("All operations completed successfully" in msg for msg in pilot.console.messages)
    assert not any("Some operations failed" in msg for msg in pilot.console.messages)


@pytest.mark.parametrize("action", ["remove", "stop-remove"])
def test_interactive_runs_each_container_only_once(monkeypatch, action):
    pilot = FakePilot()
    replies = [action, "first,first, second,first"]
    if action == "stop-remove":
        replies.append("10")
    replies.append("exit")
    answers = iter(replies)
    monkeypatch.setattr("dockerpilot.cli.interactive.Prompt.ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr("dockerpilot.cli.interactive.Confirm.ask", lambda *args, **kwargs: True)

    run_interactive_menu(pilot)

    assert pilot.calls == [(action, "first"), (action, "second")]
    assert any("All operations completed successfully" in msg for msg in pilot.console.messages)
    assert not any("Some operations failed" in msg for msg in pilot.console.messages)


def test_cli_still_reports_actual_failure_for_unique_target():
    pilot = FakePilot(failing={"missing"})
    args = Namespace(container_action="remove", name="first,missing,first", force=True)

    with pytest.raises(SystemExit) as exc:
        handle_container_cli(pilot, args)

    assert exc.value.code == 1
    assert pilot.calls == [("remove", "first"), ("remove", "missing")]
    assert any("Some operations failed" in msg for msg in pilot.console.messages)
