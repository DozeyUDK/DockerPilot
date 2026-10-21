"""Regression: redact canary health diagnostics before repr() escaping."""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dockerpilot.secure_deploy_broker.canary import CanaryLedger, CanaryManager, CanaryPolicy
from dockerpilot.secure_deploy_broker.errors import VerificationError


@pytest.mark.parametrize("diagnostic", [
    "request failed\nBearer test-health-credential",
    "request failed\tBasic test-health-credential",
    "request failed\x1b[0mBearer test-health-credential",
])
def test_health_diagnostics_safe_before_repr_and_audit(tmp_path, diagnostic):
    policy = CanaryPolicy(workdir=tmp_path / "canary", health_timeout_seconds=0)
    ledger = CanaryLedger(policy.workdir)
    steps = iter([0.0, 0.0, 1.0, 1.0])
    manager = CanaryManager(
        policy=policy, ledger=ledger,
        monotonic=lambda: next(steps), sleep=lambda _: None,
    )
    manager._run_owned = lambda _: SimpleNamespace(
        timed_out=False, returncode=1, stderr=diagnostic
    )
    with pytest.raises(VerificationError, match="canary health failed") as err:
        manager._health()

    assert err.value.code == "canary_health"
    assert "test-health-credential" not in str(err.value)
    assert "[REDACTED" in str(err.value)
    ledger.append_audit({"primary_error": str(err.value)})
    audit = (policy.workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert "test-health-credential" not in audit
