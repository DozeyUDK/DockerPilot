"""Mouse-friendly Textual UI for DockerPilot."""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union


@dataclass
class ArgumentSpec:
    """Description of a parser argument rendered in the TUI."""

    dest: str
    label: str
    help_text: str
    option_strings: Tuple[str, ...]
    required: bool
    positional: bool
    default: Any
    choices: Tuple[str, ...] = ()
    nargs: Optional[Union[str, int]] = None
    metavar: Optional[str] = None
    is_bool: bool = False
    bool_flag_value: bool = True
    multiple: bool = False

    @property
    def primary_flag(self) -> Optional[str]:
        """Return the preferred option flag for command serialization."""
        return self.option_strings[0] if self.option_strings else None


@dataclass
class CommandNode:
    """Recursive command tree used by the TUI."""

    name: str
    path: Tuple[str, ...]
    help_text: str
    arguments: List[ArgumentSpec] = field(default_factory=list)
    children: List["CommandNode"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        """Return True when this node is directly runnable."""
        return not self.children


@dataclass
class ResourceSelectorSpec:
    """Describe a live Docker-backed selector for an argument."""

    resource_type: str
    mode: str


@dataclass
class TuiCommandHandoff:
    """Request to temporarily leave TUI and run a command in the real terminal."""

    argv: List[str]


def _is_subparsers_action(action: argparse.Action) -> bool:
    """Return True when the action holds nested subparsers."""
    return action.__class__.__name__ == "_SubParsersAction"


def _is_help_action(action: argparse.Action) -> bool:
    """Return True when the action is argparse's built-in help action."""
    return action.__class__.__name__ == "_HelpAction"


def _is_store_true_action(action: argparse.Action) -> bool:
    return action.__class__.__name__ == "_StoreTrueAction"


def _is_store_false_action(action: argparse.Action) -> bool:
    return action.__class__.__name__ == "_StoreFalseAction"


def _is_append_action(action: argparse.Action) -> bool:
    return action.__class__.__name__ == "_AppendAction"


def _default_value(action: argparse.Action) -> Any:
    """Normalize argparse defaults for TUI use."""
    return None if action.default is argparse.SUPPRESS else action.default


def collect_argument_specs(
    parser: argparse.ArgumentParser,
    *,
    exclude_dests: Optional[Set[str]] = None,
) -> List[ArgumentSpec]:
    """Build renderable argument specs for a parser."""
    exclude = exclude_dests or set()
    specs: List[ArgumentSpec] = []

    for action in parser._actions:
        if _is_help_action(action) or _is_subparsers_action(action):
            continue
        if action.dest in exclude:
            continue

        positional = not action.option_strings
        default = _default_value(action)
        is_bool = _is_store_true_action(action) or _is_store_false_action(action)
        bool_flag_value = not _is_store_false_action(action)
        multiple = _is_append_action(action) or action.nargs in ("*", "+")
        choices = tuple(str(choice) for choice in action.choices) if action.choices else ()

        label = action.option_strings[0] if action.option_strings else action.dest
        help_text = action.help or ""
        if multiple:
            helper = "Use one value per line."
            help_text = f"{help_text} {helper}".strip()

        required = bool(getattr(action, "required", False))
        if positional:
            required = action.nargs not in ("?", "*")

        specs.append(
            ArgumentSpec(
                dest=action.dest,
                label=label,
                help_text=help_text,
                option_strings=tuple(action.option_strings),
                required=required,
                positional=positional,
                default=default,
                choices=choices,
                nargs=action.nargs,
                metavar=action.metavar,
                is_bool=is_bool,
                bool_flag_value=bool_flag_value,
                multiple=multiple,
            )
        )

    return specs


def build_command_tree(
    parser: argparse.ArgumentParser,
    *,
    exclude_commands: Optional[Set[str]] = None,
) -> List[CommandNode]:
    """Extract command/subcommand hierarchy from argparse definitions."""
    exclude = exclude_commands or set()
    nodes: List[CommandNode] = []

    def build_nodes(current_parser: argparse.ArgumentParser, path: Tuple[str, ...]) -> List[CommandNode]:
        subparser_action = next(
            (action for action in current_parser._actions if _is_subparsers_action(action)),
            None,
        )
        if not subparser_action:
            return []

        help_by_name: Dict[str, str] = {}
        for choice_action in getattr(subparser_action, "_choices_actions", []):
            help_by_name[choice_action.dest] = getattr(choice_action, "help", "") or ""

        children: List[CommandNode] = []
        for command_name, child_parser in subparser_action.choices.items():
            if not path and command_name in exclude:
                continue

            child_path = (*path, command_name)
            nested_children = build_nodes(child_parser, child_path)
            child_arguments = []
            if not nested_children:
                excluded = {"interactive"} if child_path == ("container", "run") else set()
                child_arguments = collect_argument_specs(child_parser, exclude_dests=excluded)

            children.append(
                CommandNode(
                    name=command_name,
                    path=child_path,
                    help_text=help_by_name.get(command_name, ""),
                    arguments=child_arguments,
                    children=nested_children,
                )
            )

        return children

    nodes.extend(build_nodes(parser, ()))
    return nodes


def split_multi_value(raw_value: Any) -> List[str]:
    """Normalize multiline or shell-style values into argv tokens."""
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]

    value = str(raw_value).strip()
    if not value:
        return []
    if "\n" in value:
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [part.strip() for part in shlex.split(value) if part.strip()]


def _should_emit_bool(spec: ArgumentSpec, value: bool) -> bool:
    """Return True when a boolean flag should be included in argv."""
    if spec.bool_flag_value:
        return value
    return not value


def serialize_argument_values(arguments: List[ArgumentSpec], values: Dict[str, Any]) -> List[str]:
    """Serialize collected TUI values into CLI argv tokens."""
    argv: List[str] = []

    for spec in arguments:
        value = values.get(spec.dest, spec.default)

        if spec.is_bool:
            normalized = bool(value)
            if _should_emit_bool(spec, normalized) and spec.primary_flag:
                argv.append(spec.primary_flag)
            continue

        if spec.multiple:
            items = split_multi_value(value)
            if not items:
                continue
            if spec.positional:
                argv.extend(items)
            elif spec.primary_flag:
                for item in items:
                    argv.extend([spec.primary_flag, item])
            continue

        if isinstance(value, list):
            normalized_items = [str(item).strip() for item in value if str(item).strip()]
            if not normalized_items:
                continue
            normalized = ",".join(normalized_items)
        else:
            normalized = "" if value is None else str(value).strip()

        if not normalized:
            continue

        if spec.positional:
            argv.append(normalized)
        elif spec.primary_flag:
                argv.extend([spec.primary_flag, normalized])

    return argv


def build_command_argv(
    command: CommandNode,
    values: Dict[str, Any],
    *,
    global_arguments: Optional[List[ArgumentSpec]] = None,
    global_values: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Construct argv for a selected command."""
    argv: List[str] = []
    if global_arguments:
        argv.extend(serialize_argument_values(global_arguments, global_values or {}))
    argv.extend(command.path)
    argv.extend(serialize_argument_values(command.arguments, values))
    return argv


def infer_resource_selector(command: CommandNode, argument: ArgumentSpec) -> Optional[ResourceSelectorSpec]:
    """Infer whether an argument should use live Docker targets."""
    path = command.path
    dest = argument.dest

    if path == ("container", "remove-image") and dest == "name":
        return ResourceSelectorSpec(resource_type="image", mode="multi")

    if path == ("monitor", "dashboard") and dest == "containers":
        return ResourceSelectorSpec(resource_type="container", mode="multi")

    if path[:1] == ("container",):
        action = path[1] if len(path) > 1 else None
        if action in {"start", "stop", "restart", "remove", "pause", "unpause", "stop-remove", "exec", "logs"} and dest == "name":
            return ResourceSelectorSpec(resource_type="container", mode="multi")
        if action == "exec-simple" and dest == "name":
            return ResourceSelectorSpec(resource_type="container", mode="single")

    if dest == "container":
        return ResourceSelectorSpec(resource_type="container", mode="single")
    if dest == "containers":
        return ResourceSelectorSpec(resource_type="container", mode="multi")

    return None


def format_container_targets(container_rows: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Format container rows into human-readable selector entries."""
    entries: List[Tuple[str, str]] = []
    for row in container_rows:
        name = row.get("name") or row.get("id") or "unknown"
        state = row.get("state") or row.get("status") or "unknown"
        image = row.get("image") or "unknown-image"
        entries.append((f"{name} [{state}] {image}", name))
    return entries


def format_image_targets(image_rows: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Format image rows into human-readable selector entries."""
    entries: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    for row in image_rows:
        for tag in row.get("tags") or []:
            if tag in seen:
                continue
            seen.add(tag)
            size = row.get("size") or "unknown-size"
            entries.append((f"{tag} [{size}]", tag))

    return entries


def selector_height(option_count: int, *, single: bool = False) -> int:
    """Return a practical viewport height for selection widgets."""
    if single:
        return min(max(option_count, 3), 6)
    return min(max(option_count + 1, 4), 10)


def requires_tty_or_live_ui(command: CommandNode, values: Dict[str, Any]) -> Optional[str]:
    """Return a reason when a command is not suitable for inline TUI execution."""
    path = command.path

    if path == ("monitor", "dashboard"):
        return "The monitoring dashboard uses a live full-screen view that does not embed cleanly in the output panel."
    if path == ("monitor", "live"):
        return "Live monitoring clears the screen repeatedly and is not suitable for the inline output panel."
    if path == ("container", "logs") and not str(values.get("name", "")).strip():
        return "Select at least one container for logs in TUI mode."
    if path == ("container", "run"):
        missing = [field for field in ("image", "name") if not str(values.get(field, "")).strip()]
        if missing:
            return "Container run inside TUI requires both image and container name."

    return None


def should_tui_require_value(command: CommandNode, argument: ArgumentSpec) -> bool:
    """Apply TUI-specific validation for flows that would otherwise drop into prompt mode."""
    if command.path == ("container", "run") and argument.dest in {"image", "name"}:
        return True
    if command.path == ("container", "logs") and argument.dest == "name":
        return True
    return argument.required


def should_launch_in_external_terminal(command: CommandNode) -> bool:
    """Return True when a command should temporarily hand off to the real terminal."""
    return command.path == ("container", "exec")


def capture_cli_execution(
    pilot,
    parser: argparse.ArgumentParser,
    argv: List[str],
    *,
    console_width: Optional[int] = None,
) -> Tuple[int, str]:
    """Execute CLI argv and capture console/log output for rendering in the TUI."""
    buffer = io.StringIO()
    capture_console = None

    if getattr(pilot, "console", None) is not None:
        width = console_width or getattr(pilot.console, "width", 120) or 120
        try:
            capture_console = pilot.console.__class__(file=buffer, force_terminal=False, width=width)
        except Exception:
            from rich.console import Console

            capture_console = Console(file=buffer, force_terminal=False, width=width)

    console_targets: List[Any] = [pilot]
    for attr in ("container_manager", "image_manager", "monitoring_manager"):
        target = getattr(pilot, attr, None)
        if target is not None:
            console_targets.append(target)

    original_consoles: Dict[int, Any] = {}
    if capture_console is not None:
        for target in console_targets:
            if hasattr(target, "console"):
                original_consoles[id(target)] = target.console
                target.console = capture_console

    original_logger_streams: List[Tuple[logging.Handler, Any]] = []
    logger = getattr(pilot, "logger", None)
    if logger is not None:
        for handler in getattr(logger, "handlers", []):
            if isinstance(handler, logging.StreamHandler):
                original_logger_streams.append((handler, handler.stream))
                handler.stream = buffer

    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            return_code = execute_cli_argv(pilot, parser, argv)
    finally:
        for handler, stream in original_logger_streams:
            handler.stream = stream
        for target in console_targets:
            if hasattr(target, "console") and id(target) in original_consoles:
                target.console = original_consoles[id(target)]

    output = buffer.getvalue().strip()
    return return_code, output


try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, LoadingIndicator, RichLog, Select, SelectionList, Static, TextArea, Tree

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


if TEXTUAL_AVAILABLE:

    class DockerPilotTUI(App):
        """Mouse-friendly command browser that emits argv for the real CLI."""

        CSS = """
        Screen {
            layout: vertical;
        }

        .hidden {
            display: none;
        }

        #loading-screen {
            align: center middle;
            height: 1fr;
        }

        #loading-card {
            width: 48;
            height: auto;
            border: round $accent;
            padding: 1 2;
            align: center middle;
        }

        #loading-title {
            text-style: bold;
            color: $accent;
            margin-top: 1;
        }

        #loading-message {
            color: $text-muted;
            margin-top: 1;
        }

        #main {
            layout: horizontal;
            height: 1fr;
        }

        #command-tree {
            width: 34;
            border: round $primary;
            min-width: 28;
        }

        #details {
            width: 1fr;
            padding: 1 2;
        }

        #selected-command {
            text-style: bold;
            color: $accent;
        }

        #selected-help {
            color: $text-muted;
            margin-bottom: 1;
        }

        .section-title {
            text-style: bold;
            margin: 1 0 0 0;
        }

        .arg-row {
            margin-bottom: 1;
        }

        .arg-help {
            color: $text-muted;
            margin-bottom: 1;
        }

        .resource-selector {
            min-height: 4;
        }

        .multi-input {
            height: 6;
        }

        #form-scroll {
            height: 1fr;
            border: round $surface;
            padding: 0 1;
        }

        #preview {
            height: 5;
            border: round $accent;
            padding: 0 1;
            margin-top: 1;
        }

        #results {
            height: 12;
            border: round $primary;
            margin-top: 1;
        }

        #button-row {
            height: auto;
            margin-top: 1;
        }

        #status {
            color: $success;
            margin-top: 1;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("ctrl+r", "run_selected", "Run"),
        ]

        def __init__(self, parser: argparse.ArgumentParser, pilot_instance: Any) -> None:
            super().__init__()
            self.global_parser = parser
            self.pilot_instance = pilot_instance
            self.global_arguments = collect_argument_specs(parser, exclude_dests={"version"})
            self.command_tree = build_command_tree(parser, exclude_commands={"tui"})
            self.selected_command: Optional[CommandNode] = None
            self.global_widgets: Dict[str, Any] = {}
            self.command_widgets: Dict[str, Any] = {}
            self.selector_specs: Dict[str, ResourceSelectorSpec] = {}
            self.available_targets: Dict[str, List[Tuple[str, str]]] = {"container": [], "image": []}
            self._selection_syncing = False
            self._command_running = False

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="loading-screen"):
                with Vertical(id="loading-card"):
                    yield LoadingIndicator(id="loading-indicator")
                    yield Static("Launching DockerPilot TUI", id="loading-title")
                    yield Static("Preparing interface...", id="loading-message")
            with Horizontal(id="main", classes="hidden"):
                yield Tree("DockerPilot Commands", id="command-tree")
                with Vertical(id="details"):
                    yield Static("Select a command", id="selected-command")
                    yield Static("Click a command on the left to open its arguments.", id="selected-help")
                    with VerticalScroll(id="form-scroll"):
                        yield Vertical(id="global-form")
                        yield Vertical(id="command-form")
                    yield Static("Command preview will appear here.", id="preview")
                    yield RichLog(id="results", wrap=False, highlight=False, markup=False)
                    with Horizontal(id="button-row"):
                        yield Button("Run In TUI", id="run-command", variant="success", disabled=True)
                        yield Button("Clear Output", id="clear-output")
                        yield Button("Refresh Targets", id="refresh-targets")
                        yield Button("Close", id="close-tui")
                    yield Static(
                        "This UI builds and runs the same DockerPilot commands you already have.",
                        id="status",
                    )
            yield Footer()

        async def on_mount(self) -> None:
            self.call_after_refresh(self._begin_initialization)

        def _begin_initialization(self) -> None:
            """Start async UI initialization after the first paint."""
            asyncio.create_task(self._initialize_ui())

        async def _initialize_ui(self) -> None:
            """Populate the interface while keeping the loading screen visible."""
            self._set_loading_message("Building command tree...")
            tree = self.query_one("#command-tree", Tree)
            tree.show_root = False
            tree.root.expand()

            for command in self.command_tree:
                self._add_tree_node(tree.root, command)

            self._set_loading_message("Loading live Docker targets...")
            status_message = await self._refresh_available_targets_async()
            await self._render_global_form()
            self._refresh_preview()
            self.query_one("#status", Static).update(status_message)
            self._show_main_ui()
            self.query_one("#results", RichLog).write("Output panel ready. Run a command to see results here.")

        def _set_loading_message(self, message: str) -> None:
            """Update the startup loader text."""
            self.query_one("#loading-message", Static).update(message)

        def _show_main_ui(self) -> None:
            """Hide the splash screen and reveal the main UI."""
            self.query_one("#loading-screen", Vertical).add_class("hidden")
            self.query_one("#main", Horizontal).remove_class("hidden")

        def _load_available_targets(self) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], str]:
            """Fetch live Docker targets without touching UI state."""
            try:
                container_rows = self.pilot_instance.list_containers(show_all=True, format_output="json") or []
                image_rows = self.pilot_instance.list_images(show_all=True, format_output="json", hide_untagged=False) or []
                container_targets = format_container_targets(container_rows)
                image_targets = format_image_targets(image_rows)
                message = f"Loaded {len(container_targets)} containers and {len(image_targets)} images."
                return container_targets, image_targets, message
            except Exception as exc:
                return [], [], f"Could not load live Docker targets: {exc}"

        async def _refresh_available_targets_async(self) -> str:
            """Refresh cached Docker targets for selector widgets."""
            container_targets, image_targets, message = await asyncio.to_thread(self._load_available_targets)
            self.available_targets["container"] = container_targets
            self.available_targets["image"] = image_targets
            return message

        def _add_tree_node(self, parent: Any, command: CommandNode) -> None:
            branch = parent.add(command.name, data=command)
            if command.children:
                for child in command.children:
                    self._add_tree_node(branch, child)

        async def _render_global_form(self) -> None:
            container = self.query_one("#global-form", Vertical)
            await container.remove_children()
            self.global_widgets = {}

            if not self.global_arguments:
                return

            await container.mount(Static("Global Options", classes="section-title"))
            for argument in self.global_arguments:
                await self._mount_argument_widget(container, argument, self.global_widgets)

        async def _render_command_form(self, command: Optional[CommandNode]) -> None:
            container = self.query_one("#command-form", Vertical)
            await container.remove_children()
            self.command_widgets = {}
            self.selector_specs = {}

            title = self.query_one("#selected-command", Static)
            help_text = self.query_one("#selected-help", Static)
            run_button = self.query_one("#run-command", Button)

            if command is None:
                title.update("Select a command")
                help_text.update("Click a command on the left to open its arguments.")
                run_button.disabled = True
                self.selected_command = None
                self._refresh_preview()
                return

            self.selected_command = command
            title.update(f"Selected: {' '.join(command.path)}")
            help_text.update(command.help_text or "Configure the arguments below, then click Run Command.")
            run_button.disabled = not command.is_leaf

            if not command.is_leaf:
                await container.mount(Static("This group has subcommands. Pick one from the tree.", classes="arg-help"))
                self._refresh_preview()
                return

            await container.mount(Static("Command Arguments", classes="section-title"))
            if not command.arguments:
                await container.mount(Static("This command has no command-specific arguments.", classes="arg-help"))
            else:
                for argument in command.arguments:
                    await self._mount_argument_widget(container, argument, self.command_widgets, command)

            self._refresh_preview()

        async def _mount_argument_widget(
            self,
            container: Vertical,
            argument: ArgumentSpec,
            widget_store: Dict[str, Any],
            command: Optional[CommandNode] = None,
        ) -> None:
            row = Vertical(classes="arg-row")
            label_text = argument.label
            if argument.required:
                label_text = f"{label_text} *"

            await container.mount(row)
            await row.mount(Label(label_text))

            default_value = "" if argument.default in (None, False) else str(argument.default)

            selector_spec = infer_resource_selector(command, argument) if command else None

            if selector_spec:
                entries = self.available_targets.get(selector_spec.resource_type, [])
                self.selector_specs[argument.dest] = selector_spec
                if entries:
                    widget = SelectionList(*entries, classes="resource-selector")
                    widget.styles.height = selector_height(
                        len(entries),
                        single=selector_spec.mode == "single",
                    )
                else:
                    widget = TextArea("", language=None, classes="multi-input")
            elif argument.is_bool:
                widget = Checkbox(argument.help_text or "Enable this option", value=bool(argument.default))
            elif argument.choices:
                options = [(choice, choice) for choice in argument.choices]
                select_value = str(argument.default) if argument.default in argument.choices else Select.BLANK
                widget = Select(options, value=select_value, allow_blank=not argument.required)
            elif argument.multiple:
                widget = TextArea(default_value, language=None, classes="multi-input")
            else:
                placeholder = argument.metavar or argument.dest.replace("_", "-")
                widget = Input(value=default_value, placeholder=str(placeholder))

            widget_store[argument.dest] = widget
            await row.mount(widget)

            if selector_spec and not self.available_targets.get(selector_spec.resource_type):
                resource_name = "containers" if selector_spec.resource_type == "container" else "images"
                await row.mount(
                    Static(
                        f"No local {resource_name} found. Enter values manually, one per line for multi-value fields.",
                        classes="arg-help",
                    )
                )
            elif selector_spec:
                selector_hint = "Select one or more items from the live Docker list."
                if selector_spec.mode == "single":
                    selector_hint = "Select one item from the live Docker list."
                await row.mount(Static(selector_hint, classes="arg-help"))

            if argument.help_text:
                await row.mount(Static(argument.help_text, classes="arg-help"))

        async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            command = event.node.data
            if isinstance(command, CommandNode):
                await self._render_command_form(command)

        def on_input_changed(self, _event: Input.Changed) -> None:
            self._refresh_preview()

        def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
            self._refresh_preview()

        def on_select_changed(self, _event: Select.Changed) -> None:
            self._refresh_preview()

        def on_text_area_changed(self, _event: TextArea.Changed) -> None:
            self._refresh_preview()

        def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
            if self._selection_syncing:
                self._refresh_preview()
                return

            dest = next((name for name, widget in self.command_widgets.items() if widget is event.selection_list), None)
            spec = self.selector_specs.get(dest) if dest else None

            if spec and spec.mode == "single" and len(event.selection_list.selected) > 1:
                latest = event.selection_list.selected[-1]
                self._selection_syncing = True
                event.selection_list.deselect_all()
                event.selection_list.select(latest)
                self._selection_syncing = False

            self._refresh_preview()

        def _values_from_widgets(self, widget_store: Dict[str, Any]) -> Dict[str, Any]:
            values: Dict[str, Any] = {}
            for dest, widget in widget_store.items():
                if isinstance(widget, Checkbox):
                    values[dest] = widget.value
                elif isinstance(widget, Select):
                    values[dest] = None if widget.is_blank() else widget.value
                elif isinstance(widget, SelectionList):
                    spec = self.selector_specs.get(dest)
                    if spec and spec.mode == "single":
                        values[dest] = widget.selected[0] if widget.selected else ""
                    else:
                        values[dest] = list(widget.selected)
                elif isinstance(widget, TextArea):
                    values[dest] = widget.text
                else:
                    values[dest] = widget.value
            return values

        def _refresh_preview(self) -> None:
            preview = self.query_one("#preview", Static)
            if not self.selected_command or not self.selected_command.is_leaf:
                preview.update("Command preview will appear here.")
                return

            argv = build_command_argv(
                self.selected_command,
                self._values_from_widgets(self.command_widgets),
                global_arguments=self.global_arguments,
                global_values=self._values_from_widgets(self.global_widgets),
            )
            preview.update(f"$ dockerpilot {' '.join(shlex.quote(part) for part in argv)}")

        def _validate_required_arguments(self) -> Tuple[bool, str]:
            if not self.selected_command or not self.selected_command.is_leaf:
                return False, "Pick a concrete command first."

            command_values = self._values_from_widgets(self.command_widgets)
            global_values = self._values_from_widgets(self.global_widgets)

            for spec in [*self.global_arguments, *self.selected_command.arguments]:
                value = (global_values if spec.dest in global_values else command_values).get(spec.dest)
                is_required = spec.required
                if self.selected_command and spec in self.selected_command.arguments:
                    is_required = should_tui_require_value(self.selected_command, spec)

                if spec.is_bool or not is_required:
                    continue
                if spec.multiple and not split_multi_value(value):
                    return False, f"Missing required values for {spec.label}."
                if isinstance(value, list) and not value:
                    return False, f"Missing required value for {spec.label}."
                if value is None or not str(value).strip():
                    return False, f"Missing required value for {spec.label}."

            return True, ""

        def action_run_selected(self) -> None:
            self._start_run()

        async def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "run-command":
                self._start_run()
            elif event.button.id == "clear-output":
                self.query_one("#results", RichLog).clear()
                self.query_one("#status", Static).update("Output panel cleared.")
            elif event.button.id == "refresh-targets":
                status = self.query_one("#status", Static)
                status.update("Refreshing live Docker targets...")
                status_message = await self._refresh_available_targets_async()
                status.update(status_message)
                if self.selected_command:
                    await self._render_command_form(self.selected_command)
            elif event.button.id == "close-tui":
                self.exit(None)

        def _set_run_state(self, running: bool) -> None:
            """Enable or disable controls around command execution."""
            self._command_running = running
            run_button = self.query_one("#run-command", Button)
            refresh_button = self.query_one("#refresh-targets", Button)
            run_button.disabled = running or not (self.selected_command and self.selected_command.is_leaf)
            refresh_button.disabled = running

        def _append_result_block(self, command_text: str, return_code: Optional[int], output: str) -> None:
            """Append a command result block to the output panel."""
            results = self.query_one("#results", RichLog)
            results.write(f"$ dockerpilot {command_text}")
            if return_code is not None:
                results.write(f"[exit {return_code}]")
            if output.strip():
                for line in output.rstrip().splitlines():
                    results.write(line)
            else:
                results.write("[no output]")
            results.write("")

        def _start_run(self) -> None:
            if self._command_running:
                self.query_one("#status", Static).update("A command is already running.")
                return
            asyncio.create_task(self._run_selected_command())

        async def _run_selected_command(self) -> None:
            valid, message = self._validate_required_arguments()
            status = self.query_one("#status", Static)
            if not valid or not self.selected_command:
                status.update(message or "Select a runnable command first.")
                return

            argv = build_command_argv(
                self.selected_command,
                self._values_from_widgets(self.command_widgets),
                global_arguments=self.global_arguments,
                global_values=self._values_from_widgets(self.global_widgets),
            )
            command_values = self._values_from_widgets(self.command_widgets)
            command_text = " ".join(shlex.quote(part) for part in argv)

            if should_launch_in_external_terminal(self.selected_command):
                self.exit(TuiCommandHandoff(argv=argv))
                return

            blocking_reason = requires_tty_or_live_ui(self.selected_command, command_values)
            if blocking_reason:
                self._append_result_block(
                    command_text,
                    None,
                    f"Inline execution skipped.\n{blocking_reason}",
                )
                status.update(blocking_reason)
                return

            self._set_run_state(True)
            status.update(f"Running inside TUI: dockerpilot {command_text}")
            results_widget = self.query_one("#results", RichLog)
            panel_width = max(60, getattr(results_widget.size, "width", 0) or getattr(results_widget.content_size, "width", 0) or 120)
            try:
                return_code, output = await asyncio.to_thread(
                    capture_cli_execution,
                    self.pilot_instance,
                    self.global_parser,
                    argv,
                    console_width=panel_width,
                )
                self._append_result_block(command_text, return_code, output)

                if return_code == 0:
                    status.update("Command completed inside TUI.")
                else:
                    status.update(f"Command finished with exit status {return_code}.")
            except Exception as exc:
                self._append_result_block(command_text, 1, f"Inline execution failed.\n{exc}")
                status.update(f"Inline execution failed: {exc}")
            finally:
                self._set_run_state(False)


def execute_cli_argv(pilot, parser: argparse.ArgumentParser, argv: List[str]) -> int:
    """Execute argv through the same CLI dispatcher used by the regular command."""
    from .handlers import dispatch_cli_args

    try:
        args = parser.parse_args(argv)
        dispatch_cli_args(pilot, args, parser)
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1


def run_tui(pilot, parser: Optional[argparse.ArgumentParser] = None) -> None:
    """Launch the optional mouse-friendly terminal UI."""
    if not TEXTUAL_AVAILABLE:
        pilot.console.print("[red]❌ Textual is not installed.[/red]")
        pilot.console.print(
            'Install it with: python -m pip install -e ".[tui]"',
            style="yellow",
            markup=False,
        )
        pilot.console.print(
            'Or, from PyPI: python -m pip install "dockerpilot[tui]"',
            style="yellow",
            markup=False,
        )
        sys.exit(1)

    active_parser = parser or argparse.ArgumentParser()
    while True:
        result = DockerPilotTUI(active_parser, pilot).run()
        if not isinstance(result, TuiCommandHandoff):
            break

        execute_cli_argv(pilot, active_parser, result.argv)
        pilot.console.print()
