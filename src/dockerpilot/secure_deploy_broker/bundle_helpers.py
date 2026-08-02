"""Pure helpers used by the broker (no Flask, no Docker SDK)."""

from __future__ import annotations

# Re-export from Extras secure_deploy pure modules when running from the monorepo.
# The staging bundle vendors copies under the install tree instead of importing
# from operator home checkouts.

from typing import Any, Dict


def normalize_spec_to_compose(spec: Dict[str, Any]) -> Dict[str, Any]:
    from backend.secure_deploy.normalizer import normalize_spec_to_compose as _impl

    return _impl(spec)


def plan_firewall_actions(spec: Dict[str, Any], *, plan_sha256: str | None = None) -> Dict[str, Any]:
    from backend.secure_deploy.firewall_planner import plan_firewall_actions as _impl

    return _impl(spec, plan_sha256=plan_sha256)
