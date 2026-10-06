from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCHER = ROOT / "tools" / "searcher"


def test_searcher_runtime_has_no_web_sudo_or_python_setcap():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")

    forbidden = (
        "sudo_password",
        "set_sudo_password",
        "@socketio.on('sudo_password')",
        "cap_net_raw",
        "cap_net_admin",
        "setcap",
        'cors_allowed_origins="*"',
    )
    for marker in forbidden:
        assert marker not in source

    assert "PcapReader" in source
    assert "dumpcap" in source
    assert "shell=False" in source


def test_searcher_defaults_to_loopback_and_refuses_root_web_process():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")

    assert "default='127.0.0.1'" in source
    assert "Refusing to run the Searcher web process as root" in source


def test_searcher_frontend_has_no_sudo_credential_flow():
    html = (SEARCHER / "templates" / "sniffer.html").read_text(encoding="utf-8")
    javascript = (SEARCHER / "templates" / "sniffer.js").read_text(encoding="utf-8")

    for marker in (
        "sudoPrompt",
        "sudoPassword",
        "sudo_password",
        "submitSudoPassword",
        "cancelSudoPassword",
        "transmitted securely",
    ):
        assert marker not in html
        assert marker not in javascript
