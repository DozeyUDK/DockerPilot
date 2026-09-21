"""Alert configuration, evaluation and notification delivery."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, List

import requests
import yaml


class AlertService:
    """Own alert rules and notification channels outside the DockerPilot facade."""

    def __init__(self, console: Any, logger: Any) -> None:
        self.console = console
        self.logger = logger
        self.alert_rules: List[dict] = []
        self.notification_channels: List[dict] = []

    def setup_monitoring_alerts(self, alert_config_path: str = "alerts.yml") -> bool:
        try:
            if not Path(alert_config_path).exists():
                template_path = Path(__file__).resolve().parents[1] / "configs" / "alerts.yml.template"
                if not template_path.exists():
                    self.logger.error(f"Template file not found: {template_path}")
                    self.console.print(f"[red]Template file not found: {template_path}[/red]")
                    return False
                with open(template_path, "r", encoding="utf-8") as source:
                    template_content = source.read()
                with open(alert_config_path, "w", encoding="utf-8") as target:
                    target.write(template_content)
                self.console.print(f"[green]Alert configuration template created: {alert_config_path}[/green]")
            else:
                self.console.print(f"[yellow]Alert configuration already exists: {alert_config_path}[/yellow]")
            return self.initialize_alert_monitoring(alert_config_path)
        except Exception as exc:
            self.logger.error(f"Failed to setup monitoring alerts: {exc}")
            return False

    def initialize_alert_monitoring(self, alert_config_path: str) -> bool:
        try:
            with open(alert_config_path, "r") as source:
                alert_config = yaml.safe_load(source) or {}
            self.alert_rules = alert_config.get("alerts", [])
            self.notification_channels = alert_config.get("notification_channels", [])
            self.console.print(f"[green]Initialized {len(self.alert_rules)} alert rules[/green]")
            self.console.print(f"[green]Configured {len(self.notification_channels)} notification channels[/green]")
            return True
        except Exception as exc:
            self.logger.error(f"Failed to initialize alert monitoring: {exc}")
            return False

    def check_alerts(self, container_stats: Any, container_name: str) -> None:
        _ = datetime.now()  # Preserve the current evaluation-time side effect/shape.
        for rule in self.alert_rules:
            condition = rule["condition"]
            if "cpu_percent >" in condition:
                threshold = float(condition.split(">")[-1].strip())
                if container_stats.cpu_percent > threshold:
                    self.trigger_alert(rule, container_name, f"CPU: {container_stats.cpu_percent:.1f}%")
            elif "memory_percent >" in condition:
                threshold = float(condition.split(">")[-1].strip())
                if container_stats.memory_percent > threshold:
                    self.trigger_alert(rule, container_name, f"Memory: {container_stats.memory_percent:.1f}%")

    def trigger_alert(self, rule: dict, container_name: str, details: str) -> None:
        alert_message = f"ALERT: {rule['name']} - Container: {container_name} - {details} - {rule['message']}"
        self.logger.warning(f"Alert triggered: {alert_message}")
        self.console.print(f"[red]🚨 ALERT: {rule['name']} - {container_name}[/red]")
        for channel in self.notification_channels:
            self.send_notification(channel, alert_message)

    def send_notification(self, channel: dict, message: str) -> None:
        try:
            if channel["type"] == "slack":
                webhook_url = channel.get("webhook_url")
                if webhook_url:
                    payload = {
                        "text": message,
                        "channel": channel.get("channel", "#general"),
                        "username": "Docker Pilot",
                        "icon_emoji": ":warning:",
                    }
                    requests.post(webhook_url, json=payload, timeout=5)
            elif channel["type"] == "email":
                self.logger.info(f"Email notification would be sent: {message}")
        except Exception as exc:
            self.logger.error(f"Failed to send notification: {exc}")
