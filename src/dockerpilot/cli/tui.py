"""Mouse-friendly Textual UI for DockerPilot."""

from __future__ import annotations
from dockerpilot.ui_icons import icon

import argparse
import asyncio
import io
import logging
import shlex
import signal
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
    negative_flag: Optional[str] = None

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


_CONTAINER_ACTIONS = {
    "logs": (("container", "logs"), "name", "Logs"),
    "stats": (("monitor", "stats"), "container", "Stats"),
    "shell": (("container", "exec"), "name", "Shell"),
    "start": (("container", "start"), "name", "Start"),
    "stop": (("container", "stop"), "name", "Stop"),
    "restart": (("container", "restart"), "name", "Restart"),
    "resume": (("container", "unpause"), "name", "Resume"),
}

_CONTAINER_ACTIONS_BY_STATE = {
    "running": {"stats", "shell", "stop", "restart"},
    "created": {"start"},
    "exited": {"start"},
    "paused": {"resume"},
}


_CONFIRM_REMOVAL = {
    ("container", "remove"),
    ("container", "remove-image"),
    ("container", "stop-remove"),
    ("container", "prune-images"),
}


_TARGET_MUTATING_COMMANDS = frozenset(
    {
        ("container", "start"),
        ("container", "stop"),
        ("container", "restart"),
        ("container", "remove"),
        ("container", "pause"),
        ("container", "unpause"),
        ("container", "rename"),
        ("container", "stop-remove"),
        ("container", "run"),
        ("container", "remove-image"),
        ("container", "prune-images"),
        ("deploy", "config"),
        ("deploy", "quick"),
        ("promote",),
        ("build",),
    }
)


def should_refresh_targets_after_command(
    command: CommandNode,
    values: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return whether a command attempt can stale cached Docker targets."""
    if command.path == ("container", "prune-images") and (values or {}).get("dry_run"):
        return False
    return command.path in _TARGET_MUTATING_COMMANDS


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
        negative_flag = next(
            (flag for flag in action.option_strings if flag.startswith("--no-")), None
        ) if isinstance(action, argparse.BooleanOptionalAction) else None
        is_bool = negative_flag is not None or _is_store_true_action(action) or _is_store_false_action(action)
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
                negative_flag=negative_flag,
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
            if spec.negative_flag and not normalized:
                argv.append(spec.negative_flag)
            elif _should_emit_bool(spec, normalized) and spec.primary_flag:
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
                    if item.startswith("-") and spec.primary_flag.startswith("--"):
                        argv.append(f"{spec.primary_flag}={item}")
                    else:
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
            if normalized.startswith("-") and spec.primary_flag.startswith("--"):
                argv.append(f"{spec.primary_flag}={normalized}")
            else:
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


RESOURCE_SELECTOR_RULES: Dict[Tuple[Tuple[str, ...], str], Tuple[str, str]] = {
    (("container", "remove-image"), "name"): ("image", "multi"),
    (("container", "start"), "name"): ("container", "multi"),
    (("container", "stop"), "name"): ("container", "multi"),
    (("container", "restart"), "name"): ("container", "multi"),
    (("container", "remove"), "name"): ("container", "multi"),
    (("container", "pause"), "name"): ("container", "multi"),
    (("container", "unpause"), "name"): ("container", "multi"),
    (("container", "stop-remove"), "name"): ("container", "multi"),
    (("container", "exec"), "name"): ("container", "multi"),
    (("container", "logs"), "name"): ("container", "multi"),
    (("container", "rename"), "name"): ("container", "single"),
    (("container", "exec-simple"), "name"): ("container", "single"),
    (("monitor", "dashboard"), "containers"): ("container", "multi"),
    (("monitor", "live"), "container"): ("container", "single"),
    (("monitor", "stats"), "container"): ("container", "single"),
    (("backup", "container-data"), "container"): ("container", "single"),
    (("backup", "restore-data"), "container"): ("container", "single"),
}


def infer_resource_selector(command: CommandNode, argument: ArgumentSpec) -> Optional[ResourceSelectorSpec]:
    """Return the audited live-resource selector for one CLI argument."""
    rule = RESOURCE_SELECTOR_RULES.get((command.path, argument.dest))
    if not rule:
        return None
    resource_type, mode = rule
    return ResourceSelectorSpec(resource_type=resource_type, mode=mode)


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
        image_id = row.get("id")
        for tag in row.get("tags") or ([image_id] if image_id else []):
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


def should_tui_require_value(command: CommandNode, argument: ArgumentSpec) -> bool:
    """Apply TUI-specific validation for flows that would otherwise drop into prompt mode."""
    if command.path == ("container", "run") and argument.dest in {"image", "name"}:
        return True
    if command.path == ("container", "logs") and argument.dest == "name":
        return True
    return argument.required


_COMMAND_LABELS = {
    ("container",): "Container commands",
    ("container", "exec"): "Container shell",
    ("container", "exec-simple"): "Run one command",
    ("container", "run"): "Create container",
    ("container", "list"): "List containers",
    ("container", "logs"): "View logs",
    ("container", "remove-image"): "Remove images",
    ("container", "prune-images"): "Prune dangling images",
    ("container", "stop-remove"): "Stop and remove",
    ("container", "unpause"): "Resume",
    ("monitor",): "Monitoring",
    ("monitor", "dashboard"): "Live dashboard",
    ("monitor", "live"): "Watch container",
    ("monitor", "stats"): "Show stats once",
}

_TERMINAL_HELP = {
    ("container", "exec"): "Exit the container shell to return here.",
    ("monitor", "dashboard"): "Select containers, or leave blank to watch all running containers. Press Ctrl+C to return.",
    ("monitor", "live"): "Watch CPU and memory for one container. Press Ctrl+C to return.",
}


def should_launch_in_external_terminal(command: CommandNode) -> bool:
    """Return True for commands that take control of the terminal."""
    return command.path in _TERMINAL_HELP


def command_label(command: CommandNode) -> str:
    return _COMMAND_LABELS.get(command.path, command.name.replace("-", " ").capitalize())


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
    for attr in ("container_manager", "image_manager", "monitoring_manager", "alert_service"):
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
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                original_logger_streams.append((handler, handler.stream))
                handler.stream = buffer

    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            return_code = execute_cli_argv(pilot, parser, argv, interactive=False)
    finally:
        for handler, stream in original_logger_streams:
            handler.stream = stream
        for target in console_targets:
            if hasattr(target, "console") and id(target) in original_consoles:
                target.console = original_consoles[id(target)]

    output = buffer.getvalue().strip()
    return return_code, output


try:
    from rich.text import Text
    from textual.app import App, ComposeResult, SuspendNotSupported
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.screen import ModalScreen
    from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, LoadingIndicator, RichLog, Select, Static, TextArea, Tree

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


if TEXTUAL_AVAILABLE:

    class ConfirmContainerAction(ModalScreen[bool]):
        """Confirm a container action that interrupts a running service."""

        DEFAULT_CSS = """
        ConfirmContainerAction {
            align: center middle;
            background: $background 65%;
        }
        ConfirmContainerAction > Vertical {
            width: 56;
            max-width: 95%;
            height: auto;
            padding: 1 2;
            border: round $warning;
            background: $surface;
        }
        ConfirmContainerAction Horizontal {
            height: auto;
            margin-top: 1;
        }
        """
        BINDINGS = [Binding("escape", "cancel", "Cancel")]

        def __init__(self, label: str, name: str, *, question: Optional[str] = None) -> None:
            super().__init__()
            self.label = label
            self.question = question or f"{label} {name}?"

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Static(Text(self.question))
                with Horizontal():
                    yield Button("Cancel", id="cancel")
                    yield Button(self.label, id="confirm", variant="warning")

        def action_cancel(self) -> None:
            self.dismiss(False)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            event.stop()
            self.dismiss(event.button.id == "confirm")


    class ResourceMultiSelector(VerticalScroll):
        """Simple checkbox-backed multi selector with reliable mouse interaction."""

        def __init__(
            self,
            entries: List[Tuple[str, str]],
            selected_values: Optional[List[str]] = None,
            **kwargs: Any,
        ) -> None:
            self.entries = list(entries)
            self._initial_selected = set(selected_values or [])
            super().__init__(**kwargs)

        def compose(self) -> ComposeResult:
            for label, value in self.entries:
                yield Checkbox(Text(label), value=value in self._initial_selected)

        @property
        def selected(self) -> List[str]:
            checkboxes = list(self.query(Checkbox))
            return [
                value
                for (_label, value), checkbox in zip(self.entries, checkboxes)
                if checkbox.value
            ]

        def select(self, value: str) -> "ResourceMultiSelector":
            for (_label, entry_value), checkbox in zip(self.entries, self.query(Checkbox)):
                if entry_value == value:
                    checkbox.value = True
                    break
            return self

        def deselect(self, value: str) -> "ResourceMultiSelector":
            for (_label, entry_value), checkbox in zip(self.entries, self.query(Checkbox)):
                if entry_value == value:
                    checkbox.value = False
                    break
            return self

        def deselect_all(self) -> "ResourceMultiSelector":
            for checkbox in self.query(Checkbox):
                checkbox.value = False
            return self


    class DockerPilotTUI(App):
        """Browse containers and run actions through the DockerPilot CLI dispatcher."""

        TITLE = "DockerPilot"

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
            width: 35%;
            min-width: 27;
            max-width: 42;
            border: round $primary;
            padding: 0 1;
        }

        #details {
            width: 1fr;
            padding: 0 1;
        }

        #container-view, #command-view, #container-table {
            height: 1fr;
        }

        #containers-header, .container-actions {
            height: auto;
        }

        #containers-title {
            width: 1fr;
        }

        #container-filter {
            width: 1fr;
        }

        #containers-title, #selected-command {
            text-style: bold;
            color: $accent;
        }

        #container-summary {
            height: auto;
            color: $text-muted;
        }

        .container-actions Button, #refresh-containers {
            min-width: 0;
            padding: 0 1;
        }

        .container-actions Button {
            border: none;
            height: 1;
            margin: 0 1 1 0;
        }

        .copy-host-action { height: 1; min-width: 0; border: none; }

        #container-results {
            height: 40%;
            border: round $primary;
        }

        #selected-help {
            color: $text-muted;
        }

        .arg-row {
            height: auto;
            margin-bottom: 1;
        }

        .arg-help {
            color: $text-muted;
        }

        .arg-row Input, .arg-row Select {
            max-width: 72;
        }

        .arg-row > Checkbox {
            height: auto;
            max-width: 100%;
            border: none;
            padding: 0;
        }

        .arg-row SelectCurrent {
            padding: 0 1;
        }

        .resource-selector {
            height: auto;
            border: round $surface;
            padding: 0 1;
        }

        .resource-selector Checkbox {
            width: 1fr;
            border: none;
            height: 1;
        }

        .resource-single-selector {
            width: 1fr;
        }

        .multi-input {
            height: 4;
            max-width: 88;
        }

        #command-form {
            height: auto;
        }

        #form-scroll {
            height: auto;
            max-height: 38%;
            padding: 0 1;
        }

        #preview {
            height: auto;
            max-height: 2;
            color: $text-muted;
            padding: 0 1;
        }

        #results {
            height: 1fr;
            border: round $primary;
        }

        #button-row {
            height: auto;
        }

        #button-row Button {
            min-width: 0;
            height: 1;
            border: none;
            padding: 0 1;
            margin-right: 1;
        }

        #run-command {
            width: auto;
        }

        #status {
            color: $success;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("ctrl+r", "run_selected", "Run"),
            Binding("ctrl+f", "find_container", "Find container"),
            Binding("escape", "close_container_filter", "Clear filter", show=False),
        ]

        def __init__(
            self,
            parser: argparse.ArgumentParser,
            pilot_instance: Any,
            *,
            global_values: Optional[Dict[str, Any]] = None,
        ) -> None:
            super().__init__()
            self.global_parser = parser
            self.pilot_instance = pilot_instance
            self.global_arguments = collect_argument_specs(parser, exclude_dests={"version"})
            self.global_values = global_values or {}
            self.command_tree = build_command_tree(parser, exclude_commands={"tui"})
            self.selected_command: Optional[CommandNode] = None
            self.command_widgets: Dict[str, Any] = {}
            self.command_drafts: Dict[Tuple[str, ...], Dict[str, Any]] = {}
            self.command_result_path: Optional[Tuple[str, ...]] = None
            self.selector_specs: Dict[str, ResourceSelectorSpec] = {}
            self.available_targets: Dict[str, List[Tuple[str, str]]] = {"container": [], "image": []}
            self.container_rows: Dict[str, Dict[str, Any]] = {}
            self.selected_container_id: Optional[str] = None
            self._command_running = False
            self._targets_refreshing = False
            self._form_render_lock = asyncio.Lock()
            self._form_render_requests = 0

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
                    with Vertical(id="container-view"):
                        with Horizontal(id="containers-header"):
                            yield Static("Containers", id="containers-title")
                            yield Button("Refresh", id="refresh-containers", tooltip="Reload Docker containers and images")
                        yield Input(placeholder="Filter by name, image, state or ID", id="container-filter")
                        yield DataTable(id="container-table", cursor_type="row", zebra_stripes=True)
                        yield Static("Click a row or press Enter.", id="container-summary")
                        for actions in (("logs", "stats", "shell"), ("start", "stop", "restart", "resume")):
                            with Horizontal(classes="container-actions"):
                                for action in actions:
                                    yield Button(
                                        _CONTAINER_ACTIONS[action][2],
                                        id=f"container-action-{action}",
                                        disabled=True,
                                    )
                        yield Button("Copy to host…", id="copy-container-to-host", disabled=True, classes="copy-host-action")
                        yield RichLog(id="container-results", wrap=True, markup=False, classes="hidden")
                    with Vertical(id="command-view", classes="hidden"):
                        yield Static("Select a command", id="selected-command")
                        yield Static("", id="selected-help")
                        with VerticalScroll(id="form-scroll"):
                            yield Vertical(id="command-form")
                        yield Static("", id="preview")
                        with Horizontal(id="button-row"):
                            yield Button("Run", id="run-command", variant="success", disabled=True)
                            yield Button("Clear output", id="clear-output")
                            yield Button("Refresh", id="refresh-targets", tooltip="Reload Docker containers and images")
                        yield Static("", id="command-result-hint")
                        yield RichLog(id="results", wrap=False, highlight=False, markup=False, auto_scroll=False, min_width=1, classes="hidden")
                    yield Static("", id="status")
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

            containers = tree.root.add_leaf("Containers")
            for command in self.command_tree:
                self._add_tree_node(tree.root, command)

            table = self.query_one("#container-table", DataTable)
            table.add_columns("Name", "State", "Image")
            self._set_loading_message("Loading live Docker targets...")
            status_message = await self._refresh_available_targets_async()
            self.query_one("#status", Static).update(status_message)
            self._show_main_ui()
            tree.move_cursor(containers)
            table.focus()

        def _set_loading_message(self, message: str) -> None:
            """Update the startup loader text."""
            self.query_one("#loading-message", Static).update(message)

        def _show_main_ui(self) -> None:
            """Hide the splash screen and reveal the main UI."""
            self.query_one("#loading-screen", Vertical).add_class("hidden")
            self.query_one("#main", Horizontal).remove_class("hidden")

        def _load_available_targets(
            self,
        ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[Tuple[str, str]]], str]:
            """Fetch live Docker targets without touching UI state."""
            try:
                container_rows = self.pilot_instance.list_containers(show_all=True, format_output="json")
                image_rows = self.pilot_instance.list_images(show_all=True, format_output="json", hide_untagged=False)
                if container_rows is None or image_rows is None:
                    return None, None, "Could not reload Docker targets. Showing the last loaded list."
                image_targets = format_image_targets(image_rows)
                message = f"Loaded {len(container_rows)} containers and {len(image_targets)} images."
                return container_rows, image_targets, message
            except Exception as exc:
                return (
                    None,
                    None,
                    f"Could not load live Docker targets; existing choices were kept: {exc}",
                )

        async def _refresh_available_targets_async(self) -> str:
            """Refresh cached Docker targets for selector widgets."""
            container_rows, image_targets, message = await asyncio.to_thread(self._load_available_targets)
            if container_rows is not None:
                self.available_targets["container"] = format_container_targets(container_rows)
                self._update_container_table(container_rows)
            if image_targets is not None:
                self.available_targets["image"] = image_targets
            return message

        def _update_container_table(self, rows: List[Dict[str, Any]]) -> None:
            self.container_rows = {
                row.get("id") or row["name"]: row
                for row in rows
                if row.get("id") or row.get("name")
            }
            self._render_container_table()

        def _render_container_table(self) -> None:
            table = self.query_one("#container-table", DataTable)
            query = self.query_one("#container-filter", Input).value.strip().casefold()
            table.clear()
            selected_row = None
            for container_id, row in self.container_rows.items():
                name = row.get("name") or container_id
                state = row.get("state") or row.get("status") or "unknown"
                image = row.get("image") or "—"
                if not any(query in field.casefold() for field in (name, state, image, container_id)):
                    continue
                if container_id == self.selected_container_id:
                    selected_row = table.row_count
                table.add_row(Text(name), Text(state), Text(image), key=container_id)

            title = "Containers"
            if query:
                title = f"Containers ({table.row_count}/{len(self.container_rows)})"
            self.query_one("#containers-title", Static).update(title)
            if selected_row is None:
                self._select_container(None)
            else:
                table.move_cursor(row=selected_row, animate=False)
                self._update_container_summary()

        def _select_container(self, container_id: Optional[str]) -> None:
            if container_id != self.selected_container_id:
                results = self.query_one("#container-results", RichLog)
                results.clear()
                results.add_class("hidden")
            self.selected_container_id = container_id
            self._update_container_summary()

        def _update_container_summary(self) -> None:
            row = self.container_rows.get(self.selected_container_id)
            table = self.query_one("#container-table", DataTable)
            if table.row_count:
                summary = "Click a row or press Enter."
            elif self.container_rows:
                summary = "No containers match the filter."
            else:
                summary = "No containers loaded."
            if row:
                name = row.get("name") or self.selected_container_id
                state = row.get("state") or row.get("status") or "unknown"
                summary = f"{name} · {state}"
            self.query_one("#container-summary", Static).update(Text(summary))
            self._set_run_state(self._command_running)

        def _container_action_available(self, action: str) -> bool:
            row = self.container_rows.get(self.selected_container_id)
            if not row:
                return False
            state = row.get("state") or row.get("status")
            return action == "logs" or action in _CONTAINER_ACTIONS_BY_STATE.get(state, ())

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            container_id = event.row_key.value
            if not self._controls_busy() and container_id in self.container_rows:
                self._select_container(container_id)

        def _request_container_action(self, action: str) -> None:
            container_id = self.selected_container_id
            if (
                container_id is None
                or self.selected_command is not None
                or self._controls_busy()
                or not self._container_action_available(action)
            ):
                return
            if action in {"stop", "restart"}:
                name = self.container_rows[container_id].get("name") or container_id

                def on_confirm(confirmed: bool | None) -> None:
                    if confirmed and self.selected_container_id == container_id:
                        self._start_run(action)

                self.push_screen(ConfirmContainerAction(_CONTAINER_ACTIONS[action][2], name), on_confirm)
            else:
                self._start_run(action)

        def _add_tree_node(self, parent: Any, command: CommandNode) -> None:
            """Mount one command node, showing an expand marker only for real branches."""
            branch = parent.add(
                command_label(command),
                data=command,
                allow_expand=bool(command.children),
            )
            for child in command.children:
                self._add_tree_node(branch, child)

        async def _render_command_form(
            self,
            command: Optional[CommandNode],
            values: Optional[Dict[str, Any]] = None,
        ) -> None:
            """Render one complete form at a time so rapid tree navigation stays ordered."""
            self._form_render_requests += 1
            self._set_run_state(self._command_running)
            try:
                async with self._form_render_lock:
                    await self._render_command_form_locked(command, values)
            finally:
                self._form_render_requests -= 1
                self._set_run_state(self._command_running)

        async def _render_command_form_locked(
            self,
            command: Optional[CommandNode],
            values: Optional[Dict[str, Any]] = None,
        ) -> None:
            """Replace the command form while the caller holds the render lock."""
            previous_command = self.selected_command
            if previous_command and self.command_widgets:
                self.command_drafts[previous_command.path] = self._snapshot_widget_values(
                    self.command_widgets
                )
            if values is None and command:
                values = self.command_drafts.get(command.path)

            container = self.query_one("#command-form", Vertical)
            await container.remove_children()
            self.command_widgets = {}
            self.selector_specs = {}

            title = self.query_one("#selected-command", Static)
            help_text = self.query_one("#selected-help", Static)
            run_button = self.query_one("#run-command", Button)
            run_button.label = "Run"
            self.selected_command = command
            if previous_command is not command and not (self._command_running or self._targets_refreshing):
                self.query_one("#status", Static).update("")
            self._show_command_result()
            self.query_one("#command-view").set_class(command is None, "hidden")
            self.query_one("#container-view").set_class(command is not None, "hidden")
            self.refresh_bindings()

            if command is None:
                self.query_one("#container-table", DataTable).focus()
                return

            title.update(command_label(command))
            description = command.help_text
            if should_launch_in_external_terminal(command):
                run_button.label = "Open terminal"
                description = _TERMINAL_HELP[command.path]
            help_text.update(description)
            run_button.disabled = self._controls_busy() or not command.is_leaf

            if not command.is_leaf:
                help_text.update("Choose a subcommand from the menu.")
                self._refresh_preview()
                return

            for argument in sorted(command.arguments, key=lambda spec: not should_tui_require_value(command, spec)):
                await self._mount_argument_widget(
                    container,
                    argument,
                    self.command_widgets,
                    command,
                    values,
                )

            if previous_command is not command:
                self.query_one("#form-scroll", VerticalScroll).scroll_home(animate=False)
            self._refresh_preview()

        async def _mount_argument_widget(
            self,
            container: Vertical,
            argument: ArgumentSpec,
            widget_store: Dict[str, Any],
            command: Optional[CommandNode] = None,
            values: Optional[Dict[str, Any]] = None,
        ) -> None:
            row = Vertical(classes="arg-row")
            label_text = argument.label
            if argument.required or (command and should_tui_require_value(command, argument)):
                label_text = f"{label_text} *"

            await container.mount(row)
            if not argument.is_bool:
                await row.mount(Label(Text(label_text)))

            value = (values or {}).get(argument.dest, argument.default)
            if command and command.path == ("container", "prune-images") and argument.dest == "dry_run":
                value = (values or {}).get(argument.dest, True)
            default_value = "" if value is None else str(value)

            selector_spec = infer_resource_selector(command, argument) if command else None

            if selector_spec:
                entries = self.available_targets.get(selector_spec.resource_type, [])
                self.selector_specs[argument.dest] = selector_spec
                if entries:
                    selected_values = self._selector_values(value)
                    available_values = {entry_value for _, entry_value in entries}
                    selected_values = [item for item in selected_values if item in available_values]
                    if selector_spec.mode == "single":
                        selected_value = selected_values[0] if selected_values else Select.BLANK
                        widget = Select(
                            [(Text(label), value) for label, value in entries],
                            value=selected_value,
                            allow_blank=True,
                            classes="resource-single-selector",
                        )
                    else:
                        widget = ResourceMultiSelector(
                            entries,
                            selected_values,
                            classes="resource-selector",
                        )
                        widget.styles.height = selector_height(len(entries) + 1)
                else:
                    widget = TextArea(
                        self._textarea_value(value), language=None, classes="multi-input"
                    )
            elif argument.is_bool:
                widget = Checkbox(Text(argument.help_text or argument.label), value=bool(value))
            elif argument.choices:
                options = [(choice, choice) for choice in argument.choices]
                select_value = str(value) if str(value) in argument.choices else Select.BLANK
                widget = Select(options, value=select_value, allow_blank=not argument.required)
            elif argument.multiple:
                widget = TextArea(self._textarea_value(value), language=None, classes="multi-input")
            else:
                placeholder = argument.metavar or argument.dest.replace("_", "-")
                widget = Input(value=default_value, placeholder=str(placeholder))

            widget_store[argument.dest] = widget
            await row.mount(widget)

            if selector_spec and not self.available_targets.get(selector_spec.resource_type):
                resource_name = "containers" if selector_spec.resource_type == "container" else "images"
                entry_hint = (
                    "Enter one value per line."
                    if selector_spec.mode == "multi"
                    else "Enter one value."
                )
                await row.mount(
                    Static(
                        f"No local {resource_name} found. {entry_hint}",
                        classes="arg-help",
                    )
                )
            elif selector_spec:
                selector_hint = "Tick one or more items from the live Docker list."
                if selector_spec.mode == "single":
                    selector_hint = "Choose one item from the live Docker list."
                await row.mount(Static(selector_hint, classes="arg-help"))

            if argument.help_text and not argument.is_bool and not selector_spec:
                await row.mount(Static(Text(argument.help_text), classes="arg-help"))

        async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            await self._render_command_form(event.node.data)

        def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
            if action == "run_selected":
                return bool(self.selected_command and self.selected_command.is_leaf)
            if action == "find_container":
                return self.selected_command is None and not self._controls_busy()
            if action == "close_container_filter":
                return isinstance(self.focused, Input) and self.focused.id == "container-filter"
            return super().check_action(action, parameters)

        def action_find_container(self) -> None:
            self.query_one("#container-filter", Input).focus()

        def action_close_container_filter(self) -> None:
            self.query_one("#container-filter", Input).value = ""
            self.query_one("#container-table", DataTable).focus()

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "container-filter":
                self._render_container_table()
            else:
                self._refresh_preview()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "container-filter" and not self._controls_busy():
                table = self.query_one("#container-table", DataTable)
                if table.row_count:
                    table.focus()
                    table.action_select_cursor()

        def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
            self._refresh_preview()

        def on_select_changed(self, _event: Select.Changed) -> None:
            self._refresh_preview()

        def on_text_area_changed(self, _event: TextArea.Changed) -> None:
            self._refresh_preview()

        def _values_from_widgets(self, widget_store: Dict[str, Any]) -> Dict[str, Any]:
            values: Dict[str, Any] = {}
            for dest, widget in widget_store.items():
                if isinstance(widget, ResourceMultiSelector):
                    values[dest] = list(widget.selected)
                elif isinstance(widget, Checkbox):
                    values[dest] = widget.value
                elif isinstance(widget, Select):
                    values[dest] = None if widget.is_blank() else widget.value
                elif isinstance(widget, TextArea):
                    selector_spec = self.selector_specs.get(dest)
                    if selector_spec and selector_spec.mode == "multi":
                        values[dest] = split_multi_value(widget.text)
                    else:
                        values[dest] = widget.text
                else:
                    values[dest] = widget.value
            return values

        def _snapshot_widget_values(self, widget_store: Dict[str, Any]) -> Dict[str, Any]:
            """Capture editable widget state without parsing partially typed shell values."""
            values: Dict[str, Any] = {}
            for dest, widget in widget_store.items():
                if isinstance(widget, TextArea):
                    values[dest] = widget.text
                elif isinstance(widget, ResourceMultiSelector):
                    values[dest] = list(widget.selected)
                elif isinstance(widget, Checkbox):
                    values[dest] = widget.value
                elif isinstance(widget, Select):
                    values[dest] = None if widget.is_blank() else widget.value
                else:
                    values[dest] = widget.value
            return values

        def _snapshot_values_after_target_refresh(self) -> Dict[str, Any]:
            """Keep manual values but discard vanished live-resource selections."""
            values = self._snapshot_widget_values(self.command_widgets)
            for dest, selector_spec in self.selector_specs.items():
                widget = self.command_widgets.get(dest)
                entries = self.available_targets.get(selector_spec.resource_type, [])

                # A TextArea is the manual fallback used while no live targets exist.
                # Preserve user-entered text if discovery still has no targets. If
                # targets appeared, reconcile the manual values with the live list.
                if isinstance(widget, TextArea) and not entries:
                    continue

                available = {value for _label, value in entries}
                selected = self._selector_values(values.get(dest))
                surviving = [value for value in selected if value in available]
                values[dest] = (
                    surviving
                    if selector_spec.mode == "multi"
                    else (surviving[0] if surviving else "")
                )
            return values

        @staticmethod
        def _textarea_value(value: Any) -> str:
            """Convert selector/list values to the editable multiline representation."""
            if isinstance(value, list):
                return "\n".join(str(item) for item in value)
            return "" if value is None else str(value)

        @staticmethod
        def _selector_values(value: Any) -> List[str]:
            """Normalize saved selector values without losing a TextArea fallback."""
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            try:
                return split_multi_value(value)
            except ValueError:
                # A partially typed shell value cannot match a live resource yet.
                return [line.strip() for line in str(value or "").splitlines() if line.strip()]

        def _controls_busy(self) -> bool:
            return (
                self._command_running
                or self._targets_refreshing
                or self._form_render_requests > 0
            )

        def _refresh_preview(self) -> None:
            # A queued field change can arrive after a confirmation screen opens.
            previews = self.query("#preview")
            if not previews:
                return
            preview = previews.first(Static)
            if not self.selected_command or not self.selected_command.is_leaf:
                preview.update("")
                return

            try:
                argv = build_command_argv(
                    self.selected_command,
                    self._values_from_widgets(self.command_widgets),
                    global_arguments=self.global_arguments,
                    global_values=self.global_values,
                )
            except ValueError as exc:
                preview.update(Text(f"Command preview error: {exc}"))
                return
            preview.update(Text(f"$ dockerpilot {shlex.join(argv)}"))

        def _validate_required_arguments(self) -> Tuple[bool, str]:
            if not self.selected_command or not self.selected_command.is_leaf:
                return False, "Pick a concrete command first."

            try:
                command_values = self._values_from_widgets(self.command_widgets)
                for spec in self.selected_command.arguments:
                    value = command_values.get(spec.dest)
                    if spec.is_bool or not should_tui_require_value(self.selected_command, spec):
                        continue
                    if spec.multiple and not split_multi_value(value):
                        return False, f"Missing required values for {spec.label}."
                    selector_spec = self.selector_specs.get(spec.dest)
                    if (
                        selector_spec
                        and selector_spec.mode == "single"
                        and isinstance(self.command_widgets.get(spec.dest), TextArea)
                        and len(split_multi_value(value)) > 1
                    ):
                        return False, f"Select only one value for {spec.label}."
                    if isinstance(value, list) and not value:
                        return False, f"Missing required value for {spec.label}."
                    if value is None or not str(value).strip():
                        return False, f"Missing required value for {spec.label}."

                # Optional multi-value fields may also call shlex.split during serialization.
                build_command_argv(
                    self.selected_command,
                    command_values,
                    global_arguments=self.global_arguments,
                    global_values=self.global_values,
                )
            except ValueError as exc:
                return False, f"Invalid shell-style value: {exc}"

            return True, ""

        def action_run_selected(self) -> None:
            command = self.selected_command
            if command is None or self._controls_busy():
                return
            valid, message = self._validate_required_arguments()
            if not valid:
                self.query_one("#status", Static).update(Text(message))
                return
            if command.path in _CONFIRM_REMOVAL:
                values = self._values_from_widgets(self.command_widgets)
                if not values.get("dry_run"):
                    targets = self._selector_values(values.get("name"))
                    shown = ", ".join(targets[:3])
                    if len(targets) > 3:
                        shown += f" and {len(targets) - 3} more"
                    question = f"{command_label(command)}: {shown}?" if shown else "Remove all dangling images?"
                    if values.get("force"):
                        question += "\nForce removal is enabled."

                    def confirm(confirmed: Optional[bool]) -> None:
                        if confirmed and self.selected_command is command:
                            self._start_run()

                    self.push_screen(ConfirmContainerAction("Remove", "", question=question), confirm)
                    return
            self._start_run()

        async def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""
            if button_id.startswith("container-action-"):
                self._request_container_action(button_id.removeprefix("container-action-"))
            elif button_id == "copy-container-to-host":
                if self._controls_busy() or self.selected_container_id is None:
                    return
                from .tui_copy_host import CopyHostScreen

                row = self.container_rows[self.selected_container_id]
                try:
                    engine = await asyncio.to_thread(self.pilot_instance.client.info)
                    engine_id = engine["ID"]
                except Exception:
                    self.query_one("#status", Static).update("Could not identify the local Docker engine.")
                    return
                self.push_screen(CopyHostScreen(row["name"], row["id"], engine_id))
            elif button_id == "run-command":
                self.action_run_selected()
            elif button_id == "clear-output":
                self.query_one("#results", RichLog).clear()
                self.command_result_path = None
                self._show_command_result()
                self.query_one("#status", Static).update("Output panel cleared.")
            elif button_id in {"refresh-targets", "refresh-containers"}:
                await self._refresh_targets()

        def _set_run_state(self, running: bool) -> None:
            """Enable or disable controls around command execution."""
            self._command_running = running
            try:
                run_button = self.query_one("#run-command", Button)
                refresh_button = self.query_one("#refresh-targets", Button)
            except NoMatches:
                return
            busy = self._controls_busy()
            run_button.disabled = busy or not (self.selected_command and self.selected_command.is_leaf)
            refresh_button.disabled = busy
            self.query_one("#refresh-containers", Button).disabled = busy
            self.query_one("#container-filter", Input).disabled = busy
            self.query_one("#container-table", DataTable).disabled = busy
            self.refresh_bindings()
            selected = self.container_rows.get(self.selected_container_id, {})
            self.query_one("#copy-container-to-host", Button).disabled = busy or not (selected.get("id") and selected.get("name"))
            for action in _CONTAINER_ACTIONS:
                button = self.query_one(f"#container-action-{action}", Button)
                button.disabled = busy or not self._container_action_available(action)

        async def _refresh_targets(self) -> None:
            """Reload live selectors while retaining the form the user sees on completion."""
            status = self.query_one("#status", Static)
            if self._controls_busy():
                status.update("A command or target refresh is already running.")
                return

            self._targets_refreshing = True
            self._set_run_state(self._command_running)
            status.update("Refreshing live Docker targets...")
            try:
                status_message = await self._refresh_available_targets_async()
                # Capture only a fully rendered form after the await. Navigation may
                # have completed while Docker discovery was running.
                async with self._form_render_lock:
                    command = self.selected_command
                    values = self._snapshot_values_after_target_refresh()
                    if command:
                        await self._render_command_form_locked(command, values)
                status.update(status_message)
            finally:
                self._targets_refreshing = False
                self._set_run_state(self._command_running)

        async def _refresh_targets_after_command(self) -> str:
            """Refresh selectors after a mutation without changing command success."""
            status_message = await self._refresh_available_targets_async()
            # Capture the current form after discovery: users may navigate or edit
            # while the command and refresh run in background threads.
            async with self._form_render_lock:
                command = self.selected_command
                values = self._snapshot_values_after_target_refresh()
                if command:
                    await self._render_command_form_locked(command, values)
            return status_message

        def action_quit(self) -> None:
            """Keep the app mounted until a pending command or refresh finishes."""
            if self._controls_busy():
                self.query_one("#status", Static).update("Wait for the current command or target refresh to finish.")
                return
            self.exit(None)

        def _show_command_result(self) -> None:
            command = self.selected_command
            has_result = bool(command and command.path == self.command_result_path)
            self.query_one("#results", RichLog).set_class(not has_result, "hidden")
            hint = self.query_one("#command-result-hint", Static)
            hint.set_class(has_result or not (command and command.is_leaf), "hidden")
            hint.update("Run to see output here.")

        def _append_result_block(self, results: RichLog, command_text: str, return_code: Optional[int], output: str) -> None:
            """Append a command result block to the output panel."""
            results.write(f"$ dockerpilot {command_text}")
            if return_code is not None:
                results.write(f"[exit {return_code}]")
            if output.strip():
                for line in output.rstrip().splitlines():
                    results.write(line)
            else:
                results.write("[no output]")
            results.write("")

        def _start_run(self, container_action: Optional[str] = None) -> None:
            if self._controls_busy():
                self.query_one("#status", Static).update("A command or target refresh is already running.")
                return
            if container_action and not self._container_action_available(container_action):
                return
            # Claim execution before scheduling so repeated clicks or Ctrl+R cannot race.
            self._set_run_state(True)
            asyncio.create_task(self._run_selected_command(container_action))

        async def _run_selected_command(self, container_action: Optional[str]) -> None:
            status = self.query_one("#status", Static)
            results = self.query_one("#container-results" if container_action else "#results", RichLog)
            command_text = "<unavailable>"
            try:
                if container_action:
                    path, argument, _ = _CONTAINER_ACTIONS[container_action]
                    group = next(group for group in self.command_tree if group.name == path[0])
                    executed_command = next(command for command in group.children if command.name == path[1])
                    command_values = {argument: self.selected_container_id}
                    if container_action == "shell":
                        command_values["command"] = "/bin/sh"
                else:
                    valid, message = self._validate_required_arguments()
                    if not valid or not self.selected_command:
                        status.update(message or "Select a runnable command first.")
                        return
                    executed_command = self.selected_command
                    command_values = self._values_from_widgets(self.command_widgets)
                argv = build_command_argv(
                    executed_command,
                    command_values,
                    global_arguments=self.global_arguments,
                    global_values=self.global_values,
                )
                command_text = shlex.join(argv)

                if should_launch_in_external_terminal(executed_command):
                    self._run_in_terminal(argv, status)
                    return

                results.clear()
                if container_action:
                    results.remove_class("hidden")
                else:
                    self.command_result_path = executed_command.path
                    self._show_command_result()
                status.update(Text(f"Running: {command_label(executed_command)}"))
                panel_width = max(12, min(160, self.query_one("#details").content_size.width - 3))
                return_code, output = await asyncio.to_thread(
                    capture_cli_execution,
                    self.pilot_instance,
                    self.global_parser,
                    argv,
                    console_width=panel_width,
                )
                if container_action and return_code == 0 and output.strip():
                    results.write(output.rstrip())
                else:
                    self._append_result_block(results, command_text, return_code, output)
                if not container_action and self.selected_command is executed_command:
                    results.scroll_home(animate=False)

                refresh_message = None
                if should_refresh_targets_after_command(executed_command, command_values):
                    try:
                        refresh_message = await self._refresh_targets_after_command()
                    except Exception as exc:
                        refresh_message = f"Could not refresh Docker targets: {exc}"

                if return_code == 0:
                    completion_message = f"{command_label(executed_command)} · Command completed inside TUI."
                    if refresh_message:
                        completion_message = f"{completion_message} {refresh_message}"
                    status.update(completion_message)
                else:
                    completion_message = f"{command_label(executed_command)} · Command finished with exit status {return_code}."
                    if refresh_message:
                        completion_message = f"{completion_message} {refresh_message}"
                    status.update(completion_message)
            except Exception as exc:
                self._append_result_block(results, command_text, 1, f"Inline execution failed.\n{exc}")
                status.update(f"Inline execution failed: {exc}")
            finally:
                self._set_run_state(False)

        def _run_in_terminal(self, argv: List[str], status: Static) -> None:
            """Give the command the terminal and resume the current screen afterwards."""
            try:
                with self.suspend():
                    # Ctrl+C belongs to the CLI command while the asyncio app is suspended.
                    previous_sigint = signal.signal(signal.SIGINT, signal.default_int_handler)
                    # Catch interruptions inside the context so Textual can resume its driver.
                    try:
                        code = execute_cli_argv(self.pilot_instance, self.global_parser, argv)
                    except KeyboardInterrupt:
                        message = "Terminal command interrupted."
                    except Exception as exc:
                        message = f"Terminal command failed: {exc}"
                    else:
                        message = f"Terminal command finished with exit status {code}."
                    finally:
                        signal.signal(signal.SIGINT, previous_sigint)
                status.update(Text(message))
                self.refresh(repaint=True, layout=True)
            except SuspendNotSupported:
                self.exit(TuiCommandHandoff(argv=argv))


def execute_cli_argv(pilot, parser: argparse.ArgumentParser, argv: List[str], *, interactive: bool = True) -> int:
    """Execute argv through the same CLI dispatcher used by the regular command."""
    from .handlers import dispatch_cli_args

    try:
        args = parser.parse_args(argv)
        if not interactive:
            args._interactive_terminal = False
        dispatch_cli_args(pilot, args, parser)
        return 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1


def run_tui(
    pilot,
    parser: Optional[argparse.ArgumentParser] = None,
    *,
    global_values: Optional[Dict[str, Any]] = None,
) -> None:
    """Launch the optional mouse-friendly terminal UI."""
    if not TEXTUAL_AVAILABLE:
        pilot.console.print(f"[red]{icon('error')} Textual is not installed.[/red]")
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

    if parser is None:
        from .parser import build_cli_parser

        parser = build_cli_parser()
    active_parser = parser
    while True:
        result = DockerPilotTUI(active_parser, pilot, global_values=global_values).run()
        if not isinstance(result, TuiCommandHandoff):
            break

        try:
            execute_cli_argv(pilot, active_parser, result.argv)
        except KeyboardInterrupt:
            pilot.console.print("Terminal command interrupted.")
        pilot.console.print()
