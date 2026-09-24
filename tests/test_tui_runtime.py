"""Headless TUI regressions. Docker calls are replaced by in-memory fakes."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip('textual')
from textual.widgets import Button, Checkbox, Label, Select, Static, TextArea

from dockerpilot.cli import tui
from dockerpilot.cli.parser import build_cli_parser


class FakePilot:
    def __init__(self):
        self.containers = [{'name': 'web', 'state': 'running', 'image': 'demo'},
                           {'name': 'api', 'state': 'running', 'image': 'demo'}]
        self.error = None
        self.container_list_calls = 0
        self.image_list_calls = 0

    def list_containers(self, **kwargs):
        self.container_list_calls += 1
        if self.error:
            raise self.error
        return list(self.containers)

    def list_images(self, **kwargs):
        self.image_list_calls += 1
        if self.error:
            raise self.error
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


def test_live_resource_selectors_use_clickable_controls_for_multi_and_single_targets():
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)

            for action in ("start", "stop", "restart", "remove", "pause", "unpause", "stop-remove", "exec", "logs"):
                await app._render_command_form(command(app, "container", action))
                widget = app.command_widgets["name"]
                assert isinstance(widget, tui.ResourceMultiSelector)
                assert len(widget.query(Checkbox)) == 2
                widget.select("web")
                await pilot.pause()
                assert widget.selected == ["web"]
                values = app._values_from_widgets(app.command_widgets)
                assert values["name"] == ["web"]

            for path, dest in (
                (("container", "rename"), "name"),
                (("container", "exec-simple"), "name"),
                (("monitor", "live"), "container"),
                (("monitor", "stats"), "container"),
                (("backup", "container-data"), "container"),
                (("backup", "restore-data"), "container"),
            ):
                await app._render_command_form(command(app, *path))
                widget = app.command_widgets[dest]
                assert isinstance(widget, Select)
                widget.value = "web"
                await pilot.pause()
                assert app._values_from_widgets(app.command_widgets)[dest] == "web"

            await app._render_command_form(command(app, "monitor", "dashboard"))
            dashboard = app.command_widgets["containers"]
            assert isinstance(dashboard, tui.ResourceMultiSelector)
            dashboard.select("web")
            dashboard.select("api")
            await pilot.pause()
            assert dashboard.selected == ["web", "api"]

            await app._render_command_form(command(app, "container", "remove-image"))
            # FakePilot has no images, so the image selector deliberately falls back to manual text.
            assert not isinstance(app.command_widgets["name"], tui.ResourceMultiSelector)

    asyncio.run(scenario())


def test_multi_selector_does_not_collapse_inside_form_scroll():
    async def scenario():
        backend = FakePilot()
        backend.containers = [
            {"name": f"app-{index}", "state": "running", "image": "demo"}
            for index in range(12)
        ]
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, "container", "stop"))
            await pilot.pause()

            widget = app.command_widgets["name"]
            assert isinstance(widget, tui.ResourceMultiSelector)
            assert len(widget.query(Checkbox)) == 12
            assert widget.size.height >= 12
            assert widget.parent.size.height >= 12

            widget.select("app-7")
            await pilot.pause()
            assert widget.selected == ["app-7"]
            assert "app-7" in str(app.query_one("#preview", Static).render())

    asyncio.run(scenario())


async def run_finished(app, pilot):
    for _ in range(30):
        await pilot.pause()
        if not app._command_running:
            return
    pytest.fail('TUI command did not finish')


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
            await run_finished(app, pilot)
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


def test_failed_refresh_keeps_cached_targets_and_form_values():
    async def scenario():
        backend = FakePilot()
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'restart'))
            app.command_widgets['timeout'].value = '47'
            app.command_widgets['name'].select('web')
            backend.error = RuntimeError('temporary discovery failure')
            await app.on_button_pressed(Button.Pressed(app.query_one('#refresh-targets', Button)))
            await pilot.pause()
            assert [value for _, value in app.available_targets['container']] == ['web', 'api']
            assert app.command_widgets['name'].selected == ['web']
            assert app.command_widgets['timeout'].value == '47'
            assert 'temporary discovery failure' in str(app.query_one('#status', Static).render())

    asyncio.run(scenario())


def test_navigation_restores_per_command_draft_values():
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            run_command = command(app, 'container', 'run')
            await app._render_command_form(run_command)
            app.command_widgets['image'].value = 'demo:v2'
            app.command_widgets['name'].value = 'demo-app'
            app.command_widgets['env'].load_text('MODE=test\nOWNER=team')
            await app._render_command_form(command(app, 'container', 'list'))
            await app._render_command_form(run_command)
            assert app.command_widgets['image'].value == 'demo:v2'
            assert app.command_widgets['name'].value == 'demo-app'
            assert app.command_widgets['env'].text == 'MODE=test\nOWNER=team'

    asyncio.run(scenario())


def test_overlapping_form_renders_finish_in_request_order(monkeypatch):
    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            first_command = command(app, 'container', 'run')
            final_command = command(app, 'container', 'rename')
            first_mount_started = asyncio.Event()
            release_first_mount = asyncio.Event()
            original_mount = app._mount_argument_widget

            async def delayed_mount(container, argument, widget_store, selected=None, values=None):
                if selected is first_command and not first_mount_started.is_set():
                    first_mount_started.set()
                    await release_first_mount.wait()
                await original_mount(container, argument, widget_store, selected, values)

            monkeypatch.setattr(app, '_mount_argument_widget', delayed_mount)
            first_render = asyncio.create_task(app._render_command_form(first_command))
            await first_mount_started.wait()
            final_render = asyncio.create_task(app._render_command_form(final_command))
            try:
                await pilot.pause()
                assert app.query_one('#run-command', Button).disabled
            finally:
                release_first_mount.set()
            await asyncio.gather(first_render, final_render)

            assert app.selected_command is final_command
            assert set(app.command_widgets) == {argument.dest for argument in final_command.arguments}
            assert set(app.selector_specs) == {'name'}
            assert len(app.query('#command-form .arg-row')) == len(final_command.arguments)
            assert all(widget.is_mounted for widget in app.command_widgets.values())
            assert 'container rename' in str(app.query_one('#selected-command', Static).render())

    asyncio.run(scenario())


def test_tui_required_fields_and_manual_selector_hints_are_explicit():
    async def scenario():
        backend = FakePilot()
        backend.containers = []
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'run'))
            for field in ('image', 'name'):
                label = app.command_widgets[field].parent.query_one(Label)
                assert f'{field} *' in str(label.render())

            await app._render_command_form(command(app, 'container', 'rename'))
            help_text = ' '.join(
                str(widget.render()) for widget in app.command_widgets['name'].parent.query(Static)
            )
            assert 'Enter one value.' in help_text
            assert 'one value per line' not in help_text

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


def test_successful_mutation_refreshes_targets_and_preserves_other_form_values(monkeypatch):
    backend = FakePilot()

    def stop_api(*_args, **_kwargs):
        backend.containers = backend.containers[:1]
        return 0, 'stopped api'

    monkeypatch.setattr(tui, 'capture_cli_execution', stop_api)

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'stop'))
            app.command_widgets['name'].select('api')
            app.command_widgets['timeout'].value = '47'
            initial_container_calls = backend.container_list_calls

            app._start_run()
            await run_finished(app, pilot)

            assert backend.container_list_calls == initial_container_calls + 1
            assert [value for _, value in app.available_targets['container']] == ['web']
            assert app.command_widgets['name'].selected == []
            assert app.command_widgets['timeout'].value == '47'
            assert 'Command completed inside TUI' in str(app.query_one('#status', Static).render())
            assert 'Loaded 1 containers' in str(app.query_one('#status', Static).render())
    asyncio.run(scenario())


def test_successful_read_only_command_does_not_refresh_targets(monkeypatch):
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *_args, **_kwargs: (0, 'listed'))

    async def scenario():
        backend = FakePilot()
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'list'))
            initial_calls = (backend.container_list_calls, backend.image_list_calls)

            app._start_run()
            await run_finished(app, pilot)

            assert (backend.container_list_calls, backend.image_list_calls) == initial_calls

    asyncio.run(scenario())


def test_mutation_removing_last_target_clears_stale_live_selection(monkeypatch):
    backend = FakePilot()
    backend.containers = [{'name': 'api', 'state': 'running', 'image': 'demo'}]

    def remove_api(*_args, **_kwargs):
        backend.containers = []
        return 0, 'removed api'

    monkeypatch.setattr(tui, 'capture_cli_execution', remove_api)

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'remove'))
            app.command_widgets['name'].select('api')

            app._start_run()
            await run_finished(app, pilot)

            assert app.available_targets['container'] == []
            assert isinstance(app.command_widgets['name'], TextArea)
            assert app.command_widgets['name'].text == ''

    asyncio.run(scenario())


def test_partial_mutation_failure_still_refreshes_changed_targets(monkeypatch):
    backend = FakePilot()

    def remove_one_then_fail(*_args, **_kwargs):
        backend.containers = backend.containers[1:]
        return 1, 'removed web; missing target failed'

    monkeypatch.setattr(tui, 'capture_cli_execution', remove_one_then_fail)

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'remove'))
            app.command_widgets['name'].select('web')
            app.command_widgets['name'].select('api')

            app._start_run()
            await run_finished(app, pilot)

            assert [value for _, value in app.available_targets['container']] == ['api']
            assert app.command_widgets['name'].selected == ['api']
            status = str(app.query_one('#status', Static).render())
            assert 'exit status 1' in status
            assert 'Loaded 1 containers' in status

    asyncio.run(scenario())


def test_failed_post_command_refresh_keeps_success_and_cached_targets(monkeypatch):
    backend = FakePilot()

    def successful_command_then_failed_refresh(*_args, **_kwargs):
        backend.error = RuntimeError('refresh unavailable')
        return 0, 'container stopped'

    monkeypatch.setattr(tui, 'capture_cli_execution', successful_command_then_failed_refresh)

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), backend)
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'stop'))
            app.command_widgets['name'].select('api')

            app._start_run()
            await run_finished(app, pilot)

            assert [value for _, value in app.available_targets['container']] == ['web', 'api']
            status = str(app.query_one('#status', Static).render())
            assert 'Command completed inside TUI' in status
            assert 'refresh unavailable' in status
            assert 'container stopped' in '\n'.join(
                str(line) for line in app.query_one('#results').lines
            )
    asyncio.run(scenario())


def test_post_command_refresh_exception_does_not_reclassify_command_success(monkeypatch):
    monkeypatch.setattr(tui, 'capture_cli_execution', lambda *_args, **_kwargs: (0, 'stopped'))

    async def scenario():
        app = tui.DockerPilotTUI(build_cli_parser(), FakePilot())
        async with app.run_test(size=(140, 55)) as pilot:
            await ready(app, pilot)
            await app._render_command_form(command(app, 'container', 'stop'))
            app.command_widgets['name'].select('api')

            async def failed_refresh():
                raise RuntimeError('render refresh failed')

            monkeypatch.setattr(app, '_refresh_targets_after_command', failed_refresh)
            app._start_run()
            await run_finished(app, pilot)

            status = str(app.query_one('#status', Static).render())
            assert 'Command completed inside TUI' in status
            assert 'render refresh failed' in status
            assert 'Inline execution failed' not in status

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
