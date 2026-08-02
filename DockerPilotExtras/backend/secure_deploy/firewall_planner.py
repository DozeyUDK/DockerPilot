"""Structural UFW / DOCKER-USER planner (data only, no execution)."""

from __future__ import annotations

from typing import Any, Dict, List

from .errors import ValidationFailedError


def plan_firewall_actions(spec: Dict[str, Any], *, plan_sha256: str | None = None) -> Dict[str, Any]:
    """Return structural firewall actions for a Spec. Never executes anything."""
    network = spec.get("network") or {}
    exposure = network.get("exposure")
    ports = network.get("published_ports") or []
    sources = list(network.get("allowed_sources") or [])

    ufw_actions: List[Dict[str, Any]] = []
    docker_user_actions: List[Dict[str, Any]] = []
    rollback_actions: List[Dict[str, Any]] = []

    if exposure == "none":
        return {
            "required": False,
            "ufw_actions": [],
            "docker_user_actions": [],
            "rollback_actions": [],
        }

    if exposure == "localhost":
        # Loopback publish needs no ingress firewall mutation.
        return {
            "required": False,
            "ufw_actions": [],
            "docker_user_actions": [],
            "rollback_actions": [],
        }

    if exposure == "public_via_existing_proxy":
        # Proxy-facing only; no new published wildcard assumed by planner.
        return {
            "required": False,
            "ufw_actions": [],
            "docker_user_actions": [],
            "rollback_actions": [],
        }

    if exposure in {"lan_allowlist", "zerotier_allowlist"}:
        if not ports:
            return {
                "required": False,
                "ufw_actions": [],
                "docker_user_actions": [],
                "rollback_actions": [],
            }
        if not sources:
            raise ValidationFailedError(
                "allowlist exposure requires allowed_sources CIDRs",
                code="firewall_missing_sources",
            )

        for index, port in enumerate(ports):
            host_port = int(port["host_port"])
            proto = port.get("protocol") or "tcp"
            bind = str(port.get("bind_address") or "0.0.0.0")
            families = _families_for_bind(bind, sources)
            for source in sources:
                for family in families:
                    if family == "ipv6" and ":" not in source and source != "::/0":
                        continue
                    if family == "ipv4" and ":" in source:
                        continue
                    rule_id = f"sd-{exposure}-{host_port}-{proto}-{index}-{family}-{_slug(source)}"
                    action = {
                        "engine": "docker-user",
                        "operation": "allow",
                        "protocol": proto,
                        "host_port": host_port,
                        "source": source,
                        "destination_binding": bind,
                        "rule_id": rule_id,
                        "comment": f"secure-deploy:{exposure}",
                        "plan_sha256": plan_sha256,
                        "family": family,
                    }
                    docker_user_actions.append(action)
                    ufw_actions.append(
                        {
                            "engine": "ufw",
                            "operation": "allow",
                            "protocol": proto,
                            "host_port": host_port,
                            "source": source,
                            "destination_binding": bind,
                            "rule_id": f"ufw-{rule_id}",
                            "comment": f"secure-deploy:{exposure}",
                            "plan_sha256": plan_sha256,
                            "family": family,
                        }
                    )
                    rollback_actions.append(
                        {
                            "engine": "docker-user",
                            "operation": "delete",
                            "rule_id": rule_id,
                            "plan_sha256": plan_sha256,
                        }
                    )
                    rollback_actions.append(
                        {
                            "engine": "ufw",
                            "operation": "delete",
                            "rule_id": f"ufw-{rule_id}",
                            "plan_sha256": plan_sha256,
                        }
                    )

        if not docker_user_actions:
            raise ValidationFailedError(
                "no firewall actions could be derived for sources/bind family",
                code="firewall_empty",
            )

        return {
            "required": True,
            "ufw_actions": ufw_actions,
            "docker_user_actions": docker_user_actions,
            "rollback_actions": rollback_actions,
        }

    raise ValidationFailedError(f"unsupported exposure for firewall planner: {exposure}")


def _families_for_bind(bind: str, sources: List[str]) -> List[str]:
    if bind == "::":
        return ["ipv6"]
    if bind in {"0.0.0.0", "*"}:
        # Dual-stack publish requires both families when sources include both.
        families = []
        if any(":" not in src for src in sources):
            families.append("ipv4")
        if any(":" in src for src in sources):
            families.append("ipv6")
        return families or ["ipv4"]
    if ":" in bind:
        return ["ipv6"]
    return ["ipv4"]


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value)[:48]
