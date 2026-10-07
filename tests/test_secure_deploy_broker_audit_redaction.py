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
        "json_detail": '{"password":"quoted-hunter2"}',
        "repr_detail": "{'token': 'quoted-token-123456'}",
        "control_split": "password\x00=hunter2-control",
        "control_key_split": "pass\x00word=hunter2-key-control",
        "control_auth": "Authorization:\x00Bearer control-token-123456",
        "quoted_space": '{"password":" hunter2-leading-space"}',
        "short_bearer": "authentication failed for Bearer abc123",
        "short_basic": "authentication failed for Basic dTpw",
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
        "quoted-hunter2",
        "quoted-token-123456",
        "hunter2-control",
        "hunter2-key-control",
        "control-token-123456",
        "hunter2-leading-space",
        "abc123",
        "dTpw",
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
            "structured": '{"password":"persisted-quoted-secret"}',
            "control_split": "token\x00=control-persisted-secret",
            "control_key_split": "pass\x00word=control-key-secret",
            "quoted_space": '{"password":" persisted-leading-space"}',
            "short_auth": "Bearer z9",
            "connection": "postgres://user:db-password@example/db",
            "credential": "direct-secret",
        }
    )

    text = (tmp_path / "canary" / "audit.jsonl").read_text(encoding="utf-8")

    for secret in (
        "audit-token-123456",
        "super-secret-value",
        "persisted-quoted-secret",
        "control-persisted-secret",
        "control-key-secret",
        "persisted-leading-space",
        "z9",
        "db-password",
        "direct-secret",
    ):
        assert secret not in text

    assert "[REDACTED]" in text
