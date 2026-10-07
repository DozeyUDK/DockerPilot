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


def test_searcher_leaves_default_interface_selection_to_dumpcap():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")

    assert 'interface or "any"' not in source
    assert 'command.extend(["-i", interface.strip()])' in source
    assert "if interface and interface.strip():" in source
    assert "default: dumpcap-selected interface" in source


def test_searcher_stop_race_is_guarded_before_dumpcap_popen():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")

    assert "self.capture_lock = threading.Lock()" in source
    assert "self.capture_generation = 0" in source
    assert "generation != self.capture_generation or not self.sniffing" in source
    assert source.index("generation != self.capture_generation or not self.sniffing") < source.index(
        "process = subprocess.Popen("
    )
    assert "self.capture_generation += 1" in source


def test_searcher_started_status_follows_successful_popen():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")
    start_method = source[source.index("    def start_sniffing"):source.index("    def stop_sniffing")]

    popen = start_method.index("process = subprocess.Popen(")
    assign = start_method.index("self.capture_process = process", popen)
    started = start_method.index("socketio.emit('status', {'status': 'started'})", assign)
    thread_start = start_method.index("self.sniff_thread.start()")

    assert popen < assign < started < thread_start
    assert start_method.count("socketio.emit('status', {'status': 'started'})") == 1


def test_searcher_shutdown_always_cleans_dumpcap_and_handles_sigterm():
    source = (SEARCHER / "searcher.py").read_text(encoding="utf-8")

    assert "import signal" in source
    assert "signal.signal(signal.SIGTERM, handle_shutdown_signal)" in source
    assert "finally:" in source
    assert "sniffer.stop_sniffing(socketio, emit_status=False)" in source
    assert "signal.signal(signal.SIGTERM, previous_sigterm_handler)" in source
