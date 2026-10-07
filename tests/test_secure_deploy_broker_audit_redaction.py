"""AT-26 regression: broker audit values must not persist reusable secrets."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from dockerpilot.secure_deploy_broker.canary import CanaryLedger, redact_event


def test_redact_event_scrubs_sensitive_keys_and_secret_shaped_values():
    event = {
        "api_token": "TOP_LEVEL_SECRET",
        "authorization": "Bearer HEADER_SECRET",
        "message": "Authorization: Bearer MESSAGE_SECRET",
        "detail": "password=hunter2",
        "nested": {
            "note": "token=abc123456789",
            "url": "postgresql://user:db-secret@example/db",
            "neutral": "ok",
        },
        "list": ["Bearer list-token-123456", "credential=opaque-secret"],
    }

    redacted = redact_event(event)
    text = repr(redacted)

    for secret in (
        "TOP_LEVEL_SECRET",
        "HEADER_SECRET",
        "MESSAGE_SECRET",
        "hunter2",
        "abc123456789",
        "db-secret",
        "list-token-123456",
        "opaque-secret",
    ):
        assert secret not in text

    assert redacted["nested"]["neutral"] == "ok"


def test_canary_audit_file_never_contains_secret_shaped_values(tmp_path):
    ledger = CanaryLedger(tmp_path / "canary")
    ledger.append_audit(
        {
            "event": "diagnostic",
            "message": "Authorization: Bearer audit-token-123456",
            "detail": "api_key=super-secret-value",
            "connection": "postgres://user:db-password@example/db",
            "credential": "direct-secret",
        }
    )

    text = (tmp_path / "canary" / "audit.jsonl").read_text(encoding="utf-8")

    for secret in (
        "audit-token-123456",
        "super-secret-value",
        "db-password",
        "direct-secret",
    ):
        assert secret not in text

    assert "[REDACTED]" in text
