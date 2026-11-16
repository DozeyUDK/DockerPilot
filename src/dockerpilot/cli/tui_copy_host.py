"""Copy a selected local container through the DockerPilot Extras API."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote, urlsplit

import requests
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static


class CopyHostScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    CopyHostScreen { align: center middle; background: $background 65%; }
    CopyHostScreen > VerticalScroll {
        width: 82;
        max-width: 96%;
        height: auto;
        max-height: 95%;
        padding: 1;
        border: round $accent;
        background: $surface;
    }
    CopyHostScreen .hidden { display: none; }
    CopyHostScreen Horizontal { height: auto; }
    CopyHostScreen Button { min-width: 0; padding: 0 1; }
    """
    BINDINGS = [("escape", "close_copy", "Back")]

    def __init__(self, name: str, container_id: str, source_engine_id: str) -> None:
        super().__init__()
        self.container_name = name
        self.container_id = container_id
        self.source_engine_id = source_engine_id
        self.url = os.environ.get("DOCKERPILOT_EXTRAS_URL", "http://127.0.0.1:5000").rstrip("/")
        self.http = requests.Session()
        self.http.trust_env = False
        self.csrf = ""
        self.job = None
        self.busy = False
        self.planned = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(Text(f"Copy {self.container_name} to host"))
            yield Static("Connecting to DockerPilot Extras…", id="copy-status")
            yield Input(placeholder="Extras username", id="copy-user", classes="hidden")
            yield Input(placeholder="Password", password=True, id="copy-password", classes="hidden")
            yield Input(placeholder="MFA code (if required)", password=True, id="copy-mfa", classes="hidden")
            yield Button("Sign in", id="copy-login", classes="hidden")
            yield Select([], prompt="Choose destination host", id="copy-host", classes="hidden")
            yield Checkbox("Copy named volume data (source must be stopped)", id="copy-data", classes="hidden")
            yield Static("", id="copy-plan")
            with Horizontal():
                yield Button("Back", id="copy-back")
                yield Button("Check plan", id="copy-check", classes="hidden")
                yield Button("Copy", id="copy-submit", classes="hidden")
                yield Button("Start on target", id="copy-start", classes="hidden")

    def on_mount(self) -> None:
        self.run_worker(self.connect())

    def on_unmount(self) -> None:
        self.http.close()

    def action_close_copy(self) -> None:
        if not self.busy:
            self.dismiss()

    def show(self, selector: str, shown: bool) -> None:
        self.query_one(selector).set_class(not shown, "hidden")

    def status(self, message: str) -> None:
        self.query_one("#copy-status", Static).update(Text(message))

    def _request(self, method, path, payload):
        parsed = urlsplit(self.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("Set DOCKERPILOT_EXTRAS_URL to an HTTPS origin or loopback HTTP origin.")
        headers = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        try:
            result = self.http.request(method, self.url + path, json=payload, headers=headers,
                                       timeout=(5, 45), allow_redirects=False)
            body = result.json()
        except (requests.RequestException, ValueError):
            raise ValueError("Could not reach the Extras API. Check DOCKERPILOT_EXTRAS_URL.") from None
        if not isinstance(body, dict) or not result.ok or body.get("success") is False:
            error = body.get("error") if isinstance(body, dict) else None
            raise ValueError(str(error or f"Extras returned HTTP {result.status_code}.")[:300])
        return body

    async def request(self, method, path, payload=None):
        return await asyncio.to_thread(self._request, method, path, payload)

    async def connect(self, credentials=None) -> None:
        self.busy = True
        try:
            if credentials is None:
                auth = await self.request("GET", "/api/auth/status")
            else:
                auth = await self.request("POST", "/api/auth/login", credentials)
            if auth.get("auth_enabled") and not auth.get("authenticated"):
                for selector in ("#copy-user", "#copy-password", "#copy-mfa", "#copy-login"):
                    self.show(selector, True)
                self.status("Sign in to Extras to access registered hosts.")
                return
            self.csrf = auth.get("csrf_token") or ""
            for selector in ("#copy-user", "#copy-password", "#copy-mfa", "#copy-login"):
                self.show(selector, False)
            inventory = await self.request("GET", "/api/servers")
            hosts = [(Text(f"{host.get('name') or host['id']} · {host.get('hostname') or ''}"), host["id"])
                     for host in inventory.get("servers", []) if host.get("id") and host["id"] != "local"]
            if not hosts:
                self.status("Register a destination in Extras → Servers, then reopen this screen.")
                return
            self.query_one("#copy-host", Select).set_options(hosts)
            for selector in ("#copy-host", "#copy-data", "#copy-check"):
                self.show(selector, True)
            self.status("Choose a host and check the plan.")
        except ValueError as exc:
            self.status(str(exc))
        finally:
            self.busy = False

    def payload(self):
        target = self.query_one("#copy-host", Select).value
        if target is Select.BLANK:
            raise ValueError("Choose a destination host.")
        return dict(container_name=self.container_name, source_container_id=self.container_id,
                    source_engine_id=self.source_engine_id, source_server_id="local",
                    target_server_id=target, copy_only=True, stop_source=False,
                    include_data=self.query_one("#copy-data", Checkbox).value)

    def invalidate(self) -> None:
        if self.job is None:
            self.planned = None
            self.show("#copy-submit", False)
            self.query_one("#copy-plan", Static).update("")

    def on_select_changed(self, event: Select.Changed) -> None:
        self.invalidate()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.invalidate()

    async def operate(self, action):
        self.busy = True
        try:
            if action == "copy-start":
                await self.request("POST", f"/api/containers/migrations/{self.job}/start-copy")
                self.show("#copy-start", False)
                self.status("Container started on the target.")
                return
            payload = self.payload()
            if action == "copy-check":
                self.invalidate()
                plan = await self.request("POST", "/api/containers/copy-plan", payload)
                if payload != self.payload():
                    return
                lines = [f"{plan['container']} → {plan['target']}",
                         f"Image: {plan['image']}", f"Network: {plan['network']}",
                         "Ports: " + (", ".join(plan["ports"]) or "None")]
                for mount in plan["mounts"]:
                    if mount["type"] in {"tmpfs", "volume"} and not mount["copy"]:
                        state = "recreate empty"
                    else:
                        state = "copy data" if mount["copy"] else "omit source data"
                    lines.append(f"{mount['destination']} ({mount['type']}): {state}")
                if plan.get("compose_project"):
                    lines.append(f"Compose project: {plan['compose_project']} (single container)")
                lines.append("Target will be created stopped. Source state stays unchanged.")
                self.query_one("#copy-plan", Static).update(Text("\n".join(lines)))
                self.planned = payload
                self.show("#copy-submit", True)
                self.status("Plan checked.")
                return
            if self.job is None:
                if payload != self.planned:
                    raise ValueError("Check the plan before copying.")
                self.show("#copy-submit", False)
                self.status("Submitting copy…")
                try:
                    result = await self.request("POST", "/api/containers/migrations", payload)
                except ValueError:
                    self.invalidate()
                    raise ValueError("Copy submission could not be confirmed. Check Extras jobs before retrying.") from None
                self.job = quote(result["migration_id"], safe="")
                for selector in ("#copy-host", "#copy-data", "#copy-check"):
                    self.query_one(selector).disabled = True
            while True:
                result = await self.request("GET", f"/api/containers/migrations/{self.job}")
                job = result["job"]
                self.status(f"{job['progress']}% · {job['message']}")
                if job["status"] in {"completed", "failed", "cancelled"}:
                    if job["status"] == "completed" and job.get("metadata", {}).get("created_container_id"):
                        self.status("Copy created on target. Start it when ready.")
                        self.show("#copy-start", True)
                    break
                await asyncio.sleep(1)
        except (ValueError, KeyError, TypeError) as exc:
            self.status(str(exc) if isinstance(exc, ValueError) else "Unexpected response from Extras.")
            if self.job is not None and action != "copy-start":
                self.query_one("#copy-submit", Button).label = "Check status"
                self.show("#copy-submit", True)
        finally:
            self.busy = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.busy:
            return
        action = event.button.id
        if action == "copy-back":
            self.action_close_copy()
        elif action == "copy-login":
            credentials = {name: self.query_one(f"#copy-{field}", Input).value
                           for name, field in (("username", "user"), ("password", "password"), ("totp_code", "mfa"))}
            self.query_one("#copy-password", Input).value = ""
            self.query_one("#copy-mfa", Input).value = ""
            self.run_worker(self.connect(credentials))
        else:
            self.run_worker(self.operate(action))
