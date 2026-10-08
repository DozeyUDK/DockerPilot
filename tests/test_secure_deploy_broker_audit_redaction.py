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
        "unicode_key": "pass\u0085word=unicode-hunter2",
        "format_key": "pass\u200bword=format-hunter2",
        "token_url": "https://ghp_ABC123@example.com/repo.git",
        "control_auth": "Authorization:\x00Bearer control-token-123456",
        "control_scheme_separator": "Authorization: Bearer\x00scheme-control-token",
        "ansi_auth": "Authorization: Bearer\x1b[0m live-token-123",
        "ansi_two_byte": "Authorization: Bearer\x1bc ris-token-123",
        "c1_csi_auth": "Authorization: Bearer\u009b0m c1-csi-token",
        "c1_osc_auth": "Authorization: Basic\u009d0;title\u009c c1-osc-token",
        "esc_dcs_auth": "Authorization: Bearer\x1bPpayload\x1b\\ esc-dcs-token",
        "esc_dcs_c1_st_key": "pass\x1bPpayload\x9cword=hunter2-c1-st",
        "esc_apc_auth": "Authorization: Basic\x1b_payload\x1b\\ esc-apc-token",
        "esc_intermediate_key": "pass\x1b(Bword=hunter2-intermediate",
        "ansi_key": "pass\x1b[31mword=ansi-key-secret",
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
        "scheme-control-token",
        "live-token-123",
        "ris-token-123",
        "c1-csi-token",
        "c1-osc-token",
        "esc-dcs-token",
        "hunter2-c1-st",
        "esc-apc-token",
        "hunter2-intermediate",
        "ansi-key-secret",
        "hunter2-leading-space",
        "unicode-hunter2",
        "format-hunter2",
        "ghp_ABC123",
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
            "control_scheme_separator": "Basic\x00persisted-basic-token",
            "ansi_auth": "Bearer\x1b[0m persisted-ansi-token",
            "ansi_two_byte": "Basic\x1bc persisted-ris-token",
            "c1_csi_auth": "Bearer\u009b0m persisted-c1-csi-token",
            "c1_dcs_auth": "Basic\u0090payload\u009c persisted-c1-dcs-token",
            "esc_dcs_auth": "Bearer\x1bPpayload\x1b\\ persisted-esc-dcs-token",
            "esc_dcs_c1_st_key": "pass\x1bPpayload\x9cword=persisted-c1-st-secret",
            "esc_intermediate_key": "pass\x1b(Bword=persisted-intermediate-secret",
            "connection": "postgres://user:db-password@example/db",
            "credential": "direct-secret",
            "unicode_key": "pass\u0085word=persisted-unicode-secret",
            "format_key": "pass\u200bword=persisted-format-secret",
            "token_url": "https://ghp_PERSISTED@example.com/repo.git",
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
        "persisted-basic-token",
        "persisted-ansi-token",
        "persisted-ris-token",
        "persisted-c1-csi-token",
        "persisted-c1-dcs-token",
        "persisted-esc-dcs-token",
        "persisted-c1-st-secret",
        "persisted-intermediate-secret",
        "db-password",
        "direct-secret",
        "persisted-unicode-secret",
        "persisted-format-secret",
        "ghp_PERSISTED",
    ):
        assert secret not in text

    assert "[REDACTED]" in text
