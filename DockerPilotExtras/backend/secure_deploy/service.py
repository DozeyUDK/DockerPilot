"""Secure Deploy preview orchestration (no apply/approve)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from dockerpilot.secure_deploy import (
    compute_plan_sha256,
    redact_for_log,
    validate_deployment_plan,
    validate_secure_deployment_spec,
)
from dockerpilot.secure_deploy.canonical import sha256_canonical
from dockerpilot.secure_deploy.schemas import SchemaValidationError

from .dozeyguard_adapter import DozeyguardConfig, resolve_dozeyguard_config, run_dozeyguard_scan
from .errors import SecureDeployError, ValidationFailedError
from .firewall_planner import plan_firewall_actions
from .normalizer import normalize_spec_to_compose
from .store import FileSecureDeployStore, new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class SecureDeployService:
    def __init__(
        self,
        store: FileSecureDeployStore,
        dozeyguard_config: Optional[DozeyguardConfig] = None,
        plan_ttl_minutes: int = 30,
    ):
        self.store = store
        self.dozeyguard_config = dozeyguard_config
        self.plan_ttl_minutes = plan_ttl_minutes

    def create_draft(self, spec: Dict[str, Any], actor: str) -> Dict[str, Any]:
        draft_id = new_id("draft")
        record = {
            "draft_id": draft_id,
            "schema_version": 1,
            "actor": actor,
            "created_at": _iso(_utcnow()),
            "spec": spec,
        }
        self.store.save_draft(draft_id, record)
        return {"draft_id": draft_id, "created_at": record["created_at"]}

    def get_draft(self, draft_id: str) -> Dict[str, Any]:
        return self.store.get_draft(draft_id)

    def validate_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        try:
            validate_secure_deployment_spec(spec)
        except SchemaValidationError as exc:
            raise ValidationFailedError(str(exc)) from exc
        return {
            "valid": True,
            "spec_sha256": sha256_canonical(spec),
        }

    def generate_plan(
        self,
        spec: Dict[str, Any],
        actor: str,
        *,
        request_id: str,
    ) -> Dict[str, Any]:
        try:
            validate_secure_deployment_spec(spec)
        except SchemaValidationError as exc:
            raise ValidationFailedError(str(exc)) from exc

        spec_sha = sha256_canonical(spec)
        compose = normalize_spec_to_compose(spec)
        compose_sha = sha256_canonical(compose)

        config = self.dozeyguard_config or resolve_dozeyguard_config()
        report = run_dozeyguard_scan(compose, config)
        summary = report.get("summary") or {}
        blocking = int(summary.get("blocking") or 0)
        warnings = int(summary.get("warnings") or 0)
        exit_code = int((report.get("result") or {}).get("exit_code") or 0)

        # Provisional firewall plan (plan_sha256 filled after).
        firewall = plan_firewall_actions(spec, plan_sha256=None)

        created = _utcnow()
        expires = created + timedelta(minutes=self.plan_ttl_minutes)
        plan_id = new_id("plan")
        nonce = secrets.token_urlsafe(16)

        invariants = [
            "no_docker_sock",
            "no_privileged",
            "no_host_network",
            "secrets_refs_only",
            "preview_only_no_apply",
        ]
        plan_warnings = []
        if warnings:
            plan_warnings.append("dozeyguard reported non-blocking findings")

        ready = exit_code == 0 and blocking == 0
        if not ready:
            plan_warnings.append("plan is not ready due to scanner findings or failure")

        plan: Dict[str, Any] = {
            "schema_version": 1,
            "plan_id": plan_id,
            "created_at": _iso(created),
            "expires_at": _iso(expires),
            "actor": actor,
            "spec_sha256": spec_sha,
            "normalized_compose_sha256": compose_sha,
            "dozeyguard": {
                "scanner_version": str(((report.get("scanner") or {}).get("version") or "unknown")),
                "contract_version": 1,
                "policy_sha256": str(((report.get("policy") or {}).get("sha256") or ("0" * 64))),
                "result_sha256": str(((report.get("result") or {}).get("result_sha256") or ("0" * 64))),
                "exit_code": exit_code,
                "blocking_findings": blocking,
                "warning_findings": warnings,
            },
            "compose": {
                "project": (spec.get("metadata") or {}).get("project"),
                "service": (spec.get("metadata") or {}).get("service"),
                "normalized_model": compose,
            },
            "firewall": {
                "required": bool(firewall.get("required")),
                "ufw_actions": _to_schema_actions(firewall.get("ufw_actions") or [], engine="ufw"),
                "docker_user_actions": _to_schema_actions(
                    firewall.get("docker_user_actions") or [], engine="docker-user"
                ),
                "rollback_actions": _to_schema_rollback(firewall.get("rollback_actions") or []),
            },
            "secrets": {
                "required_refs": [
                    {
                        "name": ref.get("name"),
                        "provider": "openbao",
                        "injection": ref.get("injection"),
                    }
                    for ref in ((spec.get("secrets") or {}).get("refs") or [])
                ]
            },
            "deployment": {
                "strategy": ((spec.get("deployment") or {}).get("strategy") or "recreate"),
                "health_gate": {
                    "timeout_seconds": int(
                        (spec.get("deployment") or {}).get("health_timeout_seconds") or 120
                    ),
                    "required": True,
                },
                "rollback_plan": {"mode": "previous_snapshot"},
            },
            "approval": {
                "required": True,
                "plan_sha256": "0" * 64,
                "status": "pending",
                "approved_by": None,
                "approved_at": None,
                "expires_at": _iso(expires),
                "nonce": nonce,
            },
            "invariants": invariants,
            "warnings": plan_warnings,
        }

        plan_sha = compute_plan_sha256(plan)
        plan["plan_sha256"] = plan_sha
        plan["approval"]["plan_sha256"] = plan_sha

        try:
            validate_deployment_plan(plan)
        except SchemaValidationError as exc:
            raise ValidationFailedError(f"generated plan failed schema: {exc}") from exc

        status = "ready" if ready else "invalid"
        envelope = {
            "schema_version": 1,
            "request_id": request_id,
            "status": status,
            "plan": plan,
            "preview": {
                "compose": compose,
                "dozeyguard": {
                    "summary": summary,
                    "findings": report.get("findings") or [],
                    "result": report.get("result") or {},
                },
                "firewall": firewall,
                "rollback_outline": firewall.get("rollback_actions") or [],
                "hashes": {
                    "spec_sha256": spec_sha,
                    "normalized_compose_sha256": compose_sha,
                    "plan_sha256": plan_sha,
                    "dozeyguard_result_sha256": plan["dozeyguard"]["result_sha256"],
                },
            },
        }

        # Persist immutable plan only (redacted preview metadata separate).
        stored = {
            "plan_id": plan_id,
            "request_id": request_id,
            "status": status,
            "plan": plan,
            "preview_redacted": redact_for_log(
                {
                    "hashes": envelope["preview"]["hashes"],
                    "dozeyguard_summary": summary,
                    "findings": report.get("findings") or [],
                }
            ),
        }
        self.store.save_plan(plan_id, stored)
        return envelope

    def get_plan(self, plan_id: str) -> Dict[str, Any]:
        return self.store.get_plan(plan_id)


def _to_schema_actions(actions: list, *, engine: str) -> list:
    """Map planner actions to deployment-plan-v1 schema shape."""
    out = []
    for action in actions:
        item = {
            "action": "allow" if action.get("operation") == "allow" else "noop",
            "rule_id": action.get("rule_id"),
            "comment": action.get("comment"),
            "cidr": action.get("source"),
            "port": action.get("host_port"),
            "proto": action.get("protocol"),
        }
        if engine == "docker-user" and action.get("family"):
            item["family"] = action.get("family")
        out.append(item)
    return out


def _to_schema_rollback(actions: list) -> list:
    out = []
    for action in actions:
        out.append(
            {
                "action": "delete" if action.get("operation") == "delete" else "noop",
                "rule_id": action.get("rule_id"),
            }
        )
    return out
