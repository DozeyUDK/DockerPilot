from dockerpilot.mcp.safety import redact_env_dict, redact_obj, redact_text


def test_redact_env_dict_masks_secret_keys():
    env = {"PASSWORD": "abc", "normal": "ok", "api_key": "zzz"}
    redacted = redact_env_dict(env)
    assert redacted["PASSWORD"] == "***"
    assert redacted["api_key"] == "***"
    assert redacted["normal"] == "ok"


def test_redact_obj_masks_nested_secret_keys_and_lists():
    obj = {
        "level1": {
            "token": "t0k",
            "items": [{"secret": "s1"}, {"ok": "v"}],
        },
        "passwd": "p",
    }
    redacted = redact_obj(obj)
    assert redacted["passwd"] == "***"
    assert redacted["level1"]["token"] == "***"
    assert redacted["level1"]["items"][0]["secret"] == "***"
    assert redacted["level1"]["items"][1]["ok"] == "v"


def test_redact_text_masks_key_value_pairs():
    text = "password=abc\nTOKEN: def\nsomething else"
    redacted = redact_text(text)
    assert "abc" not in redacted
    assert "def" not in redacted
    assert "password=***" in redacted.lower()

