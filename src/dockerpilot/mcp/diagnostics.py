from __future__ import annotations

from typing import Any, Optional

from .docker_ops import DockerOps
from .safety import ToolBlocked


def detect_unhealthy(ops: DockerOps, include_exited: bool) -> dict[str, Any]:
    containers = ops.list_containers(all_containers=True, include_ports=False, include_labels=False)["containers"]
    issues: list[dict[str, Any]] = []
    for c in containers:
        state = (c.get("state") or "").lower()
        name = c.get("name") or ""
        if not include_exited and state == "exited":
            continue
        try:
            inspected = ops.inspect_container_curated(name, redact=True)["container"]
        except ToolBlocked:
            continue
        st = (inspected.get("state") or {})
        health = ((inspected.get("health") or {}).get("Status") or (inspected.get("health") or {}).get("status") or None)
        if isinstance(health, str):
            health = health.lower()

        exit_code = st.get("exit_code")
        oom_killed = st.get("oom_killed")
        restart_count = st.get("restart_count")
        status = (st.get("status") or state or "").lower()

        if health == "unhealthy":
            issues.append(
                {
                    "severity": "critical",
                    "container": name,
                    "kind": "healthcheck_unhealthy",
                    "message": "Container healthcheck reports unhealthy.",
                    "suggested_next_tools": ["dockerpilot_container_logs", "dockerpilot_diagnose_container"],
                }
            )
        if status == "restarting":
            issues.append(
                {
                    "severity": "critical",
                    "container": name,
                    "kind": "restarting_loop",
                    "message": "Container is restarting repeatedly.",
                    "suggested_next_tools": ["dockerpilot_container_logs", "dockerpilot_explain_container_state"],
                }
            )
        if status == "exited" and isinstance(exit_code, int) and exit_code != 0:
            issues.append(
                {
                    "severity": "warning",
                    "container": name,
                    "kind": "exited_nonzero",
                    "message": f"Container exited with non-zero code {exit_code}.",
                    "suggested_next_tools": ["dockerpilot_container_logs", "dockerpilot_explain_container_state"],
                }
            )
        if oom_killed is True:
            issues.append(
                {
                    "severity": "critical",
                    "container": name,
                    "kind": "oom_killed",
                    "message": "Container was OOM-killed.",
                    "suggested_next_tools": ["dockerpilot_container_stats", "dockerpilot_container_logs"],
                }
            )
        try:
            if isinstance(restart_count, int) and restart_count >= 5:
                issues.append(
                    {
                        "severity": "warning",
                        "container": name,
                        "kind": "high_restart_count",
                        "message": f"High restart count: {restart_count}.",
                        "suggested_next_tools": ["dockerpilot_explain_container_state", "dockerpilot_container_logs"],
                    }
                )
        except Exception:
            pass
        if status == "dead":
            issues.append(
                {
                    "severity": "critical",
                    "container": name,
                    "kind": "dead_state",
                    "message": "Container is in dead state.",
                    "suggested_next_tools": ["dockerpilot_container_inspect"],
                }
            )
    return {"issues": issues}


def explain_container_state(ops: DockerOps, name: str) -> dict[str, Any]:
    inspected = ops.inspect_container_curated(name, redact=True)["container"]
    st = inspected.get("state") or {}
    status = (st.get("status") or "").lower()
    health = (inspected.get("health") or {}).get("Status") if isinstance(inspected.get("health"), dict) else None
    if isinstance(health, str):
        health = health.lower()
    exit_code = st.get("exit_code")
    oom_killed = st.get("oom_killed")
    restart_count = st.get("restart_count")

    explanation_parts: list[str] = []
    recommended_actions: list[dict[str, Any]] = []

    if status == "running":
        explanation_parts.append("Container is running.")
        recommended_actions.append(
            {"tool": "dockerpilot_container_stats", "reason": "Check resource usage.", "args": {"name": name}}
        )
        recommended_actions.append(
            {"tool": "dockerpilot_container_logs", "reason": "Review recent logs.", "args": {"name": name, "tail": 200}}
        )
    elif status == "exited":
        explanation_parts.append("Container is exited.")
        if isinstance(exit_code, int):
            explanation_parts.append(f"Exit code: {exit_code}.")
        if oom_killed is True:
            explanation_parts.append("It was OOM-killed.")
        recommended_actions.append(
            {"tool": "dockerpilot_container_logs", "reason": "Inspect logs around exit.", "args": {"name": name, "tail": 200}}
        )
        recommended_actions.append(
            {"tool": "dockerpilot_container_start", "reason": "Start the container (guarded).", "args": {"name": name, "dry_run": True}}
        )
    elif status == "restarting":
        explanation_parts.append("Container is restarting; likely crash loop.")
        recommended_actions.append(
            {"tool": "dockerpilot_container_logs", "reason": "Identify crash reason.", "args": {"name": name, "tail": 200}}
        )
    elif status:
        explanation_parts.append(f"Container status: {status}.")
    else:
        explanation_parts.append("Container state is unknown.")

    if health == "unhealthy":
        explanation_parts.append("Healthcheck is unhealthy.")
        recommended_actions.append(
            {"tool": "dockerpilot_diagnose_container", "reason": "Run full diagnosis.", "args": {"name": name, "include_logs": True, "tail": 200}}
        )
    if isinstance(restart_count, int) and restart_count > 0:
        explanation_parts.append(f"Restart count: {restart_count}.")

    explanation = " ".join(explanation_parts).strip() or "No explanation available."
    return {
        "name": name,
        "status": status or None,
        "health": health,
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "oom_killed": bool(oom_killed) if oom_killed is not None else None,
        "restart_count": restart_count if isinstance(restart_count, int) else None,
        "explanation": explanation,
        "recommended_actions": recommended_actions,
    }


def diagnose_container(ops: DockerOps, name: str, include_logs: bool, tail: int) -> dict[str, Any]:
    inspected = ops.inspect_container_curated(name, redact=True)["container"]
    st = inspected.get("state") or {}
    status = (st.get("status") or "").lower()
    health = (inspected.get("health") or {}).get("Status") if isinstance(inspected.get("health"), dict) else None
    if isinstance(health, str):
        health = health.lower()
    exit_code = st.get("exit_code")
    oom_killed = st.get("oom_killed")
    restart_count = st.get("restart_count")

    issues: list[dict[str, Any]] = []
    facts: dict[str, Any] = {
        "status": status,
        "health": health,
        "exit_code": exit_code,
        "oom_killed": oom_killed,
        "restart_count": restart_count,
    }

    if health == "unhealthy":
        issues.append({"severity": "critical", "kind": "healthcheck_unhealthy", "message": "Healthcheck unhealthy."})
    if status == "restarting":
        issues.append({"severity": "critical", "kind": "restarting_loop", "message": "Restarting loop."})
    if status == "exited" and isinstance(exit_code, int) and exit_code != 0:
        issues.append({"severity": "warning", "kind": "exited_nonzero", "message": f"Exited with code {exit_code}."})
    if oom_killed is True:
        issues.append({"severity": "critical", "kind": "oom_killed", "message": "OOM killed."})
    if isinstance(restart_count, int) and restart_count >= 5:
        issues.append({"severity": "warning", "kind": "high_restart_count", "message": f"High restart count {restart_count}."})

    stats = None
    if status == "running":
        try:
            stats = ops.container_stats(name)
        except Exception:
            stats = None
    if stats:
        facts["stats"] = stats

    logs_excerpt = None
    if include_logs:
        try:
            logs_excerpt = ops.container_logs(name, tail=tail, since=None, timestamps=True, redact=True)["logs"]
        except Exception:
            logs_excerpt = None

    recommended_actions: list[dict[str, Any]] = []
    recommended_actions.append({"tool": "dockerpilot_container_inspect", "reason": "Inspect container config/state.", "args": {"name": name}})
    if include_logs:
        recommended_actions.append({"tool": "dockerpilot_container_logs", "reason": "Review recent logs.", "args": {"name": name, "tail": tail}})
    if status in {"exited", "restarting", "dead"}:
        recommended_actions.append(
            {"tool": "dockerpilot_container_restart", "reason": "Try restart (guarded).", "args": {"name": name, "dry_run": True}}
        )

    summary = f"{name}: status={status or 'unknown'}"
    if health:
        summary += f", health={health}"
    if isinstance(exit_code, int):
        summary += f", exit_code={exit_code}"

    return {
        "name": name,
        "summary": summary,
        "facts": facts,
        "issues": issues,
        "recommended_actions": recommended_actions,
        "logs_excerpt": logs_excerpt,
    }


def diagnose_stack(ops: DockerOps, include_logs: bool, tail: int) -> dict[str, Any]:
    containers = ops.list_containers(all_containers=True, include_ports=False, include_labels=False)["containers"]
    issues: list[dict[str, Any]] = []
    recommended: list[dict[str, Any]] = []
    for c in containers:
        name = c.get("name") or ""
        try:
            diag = diagnose_container(ops, name=name, include_logs=include_logs, tail=tail)
        except Exception:
            continue
        for issue in diag.get("issues") or []:
            issues.append({"container": name, **issue})
    summary = f"Checked {len(containers)} containers; found {len(issues)} issues."
    if issues:
        recommended.append(
            {"tool": "dockerpilot_detect_unhealthy", "reason": "Get focused list of unhealthy containers.", "args": {"include_exited": True}}
        )
    return {
        "summary": summary,
        "containers_checked": len(containers),
        "issues": issues,
        "recommended_actions": recommended,
    }

