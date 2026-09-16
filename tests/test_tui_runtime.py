"""Headless TUI regressions. Docker calls are replaced by in-memory fakes."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip('textual')
from textual.widgets import Button, Static

from dockerpilot.cli import tui
from dockerpilot.cli.parser import build_cli_parser


class FakePilot:
    def __init__(self):
        self.containers = [{'name': 'web', 'state': 'running', 'image': 'demo'},
                           {'name': 'api', 'state': 'running', 'image': 'demo'}]

    def list_containers(self, **kwargs):
        return list(self.containers)

    def list_images(self, **kwargs):
        return []


def command(app, *path):
    nodes = app.command_tree
    for part in path:
        node = next(node for node in nodes if node.name == part)
        nodes = node.children
    return node


async def ready(app, pilot):
    for _ in range(30):
        await pilot.pause()
        if app.query_one('#loading-screen').has_class('hidden'):
            return
    pytest.fail('TUI did not finish initialization')


def test_incomplete_quote_is_editable_and_run_is_rejected(monkeypatch):
    dispatched = []
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *a, **kw: dispatched.append(a) or (0, 'OK'))

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'deploy', 'quick'))
            app.command_widgets['dockerfile_path'].value = '.'
            app.command_widgets['image_tag'].value = 'demo:v1'
            app.command_widgets['container_name'].value = 'demo'
            app.command_widgets['env'].load_text('KEY="unfinished')
            await pilot.pause()
            app._start_run()
            await pilot.pause()
            assert not dispatched
            assert not app._command_running
            app.command_widgets['env'].load_text('KEY="two words"')
            await pilot.pause()
            app._start_run()
            await pilot.pause()
            assert len(dispatched) == 1
            assert 'KEY=two words' in dispatched[0][2]
            assert not app._command_running
    asyncio.run(scenario())


def test_refresh_preserves_options_and_surviving_selections():
    async def scenario():
        backend = FakePilot()
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'restart'))
            app.command_widgets['timeout'].value = '47'
            app.command_widgets['name'].select('web')
            app.command_widgets['name'].select('api')
            await pilot.pause()
            backend.containers = backend.containers[:1]
            await app.on_button_pressed(Button.Pressed(app.query_one('#refresh-targets', Button)))
            await pilot.pause()
            assert app.command_widgets['timeout'].value == '47'
            assert app.command_widgets['name'].selected == ['web']
    asyncio.run(scenario())


def test_run_shortcut_is_locked_before_background_task_starts(monkeypatch):
    dispatched = []
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *a, **kw: dispatched.append(a) or (0, 'OK'))

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'list'))
            app._start_run()
            assert app._command_running
            app._start_run()
            await pilot.pause()
            assert len(dispatched) == 1
            assert not app._command_running
            assert not app.query_one('#run-command', Button).disabled
    asyncio.run(scenario())


def test_missing_arguments_release_busy_state(monkeypatch):
    dispatched = []
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *a, **kw: dispatched.append(a) or (0, 'OK'))

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'restart'))
            app._start_run()
            await pilot.pause()
            assert not dispatched
            assert not app._command_running
            assert not app.query_one('#refresh-targets', Button).disabled
    asyncio.run(scenario())


def test_manual_multitarget_entry_matches_cli_comma_format():
    async def scenario():
        backend = FakePilot()
        backend.containers = []
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'stop'))
            app.command_widgets['name'].load_text('web\napi')
            await pilot.pause()
            values = app._values_from_widgets(app.command_widgets)
            argv = tui.build_command_argv(app.selected_command, values)
            assert argv[:3] == ['container', 'stop', 'web,api']
            backend.containers = [{'name': 'web', 'state': 'running', 'image': 'demo'}]
            await app.on_button_pressed(Button.Pressed(app.query_one('#refresh-targets', Button)))
            await pilot.pause()
            assert app.command_widgets['name'].selected == ['web']
    asyncio.run(scenario())


def test_manual_multitarget_rejects_incomplete_shell_quote(monkeypatch):
    dispatched = []
    monkeypatch.setattr(tui, "capture_cli_execution", lambda *a, **kw: dispatched.append(a) or (0, "OK"))

    async def scenario():
        backend = FakePilot()
        backend.containers = []
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, "container", "stop"))
            app.command_widgets["name"].load_text('web "unfinished')
            await pilot.pause()
            app._start_run()
            await pilot.pause()
            assert not dispatched
            assert "Invalid shell-style value" in str(app.query_one("#status", Static).render())
            assert not app._command_running

    asyncio.run(scenario())


def test_refresh_keeps_edits_made_during_fetch_and_blocks_run(monkeypatch):
    dispatched = []
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *a, **kw: dispatched.append(a) or (0, 'OK'))

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'restart'))
            app.command_widgets['name'].select('web')
            started, release = asyncio.Event(), asyncio.Event()

            async def delayed_refresh():
                started.set()
                await release.wait()
                return 'refreshed'

            monkeypatch.setattr(app, '_refresh_available_targets_async', delayed_refresh)
            task = asyncio.create_task(app.on_button_pressed(Button.Pressed(app.query_one('#refresh-targets', Button))))
            await started.wait()
            try:
                app.command_widgets['timeout'].value = '52'
                app._start_run()
                await pilot.pause()
                assert not dispatched
            finally:
                release.set()
                await task
            assert app.command_widgets['timeout'].value == '52'
            assert app.command_widgets['name'].selected == ['web']
    asyncio.run(scenario())


def test_command_exception_unlocks_controls(monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError('synthetic dispatch failure')

    monkeypatch.setattr(tui, 'capture_cli_execution', failed)

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'list'))
            app._start_run()
            await pilot.pause()
            assert not app._command_running
            assert not app.query_one('#run-command', Button).disabled
            assert not app.query_one('#refresh-targets', Button).disabled
    asyncio.run(scenario())


def test_quit_waits_for_busy_state_then_exits(monkeypatch):
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            exits = []
            monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))
            app._targets_refreshing = True
            app.action_quit()
            assert exits == []
            assert "Wait for" in str(app.query_one("#status", Static).render())
            app._targets_refreshing = False
            app.action_quit()
            assert exits == [None]

    asyncio.run(scenario())


def test_external_terminal_handoff_releases_busy_state(monkeypatch):
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, "container", "exec"))
            app.command_widgets["name"].select("web")
            exits = []
            monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))
            app._start_run()
            await pilot.pause()
            assert len(exits) == 1
            assert isinstance(exits[0], tui.TuiCommandHandoff)
            command_start = exits[0].argv.index("container")
            assert exits[0].argv[command_start : command_start + 3] == ["container", "exec", "web"]
            assert not app._command_running

    asyncio.run(scenario())
