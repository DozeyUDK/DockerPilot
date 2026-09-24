from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "DockerPilotExtras" / "backend"


def test_extras_backend_has_no_local_shell_true_or_autoaddpolicy():
    python_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EXTRAS.rglob("*.py")
    )
    assert "shell=True" not in python_text
    assert "AutoAddPolicy" not in python_text


def test_legacy_sudo_endpoint_never_assigns_password_to_cookie_session():
    auth_text = (EXTRAS / "resources" / "auth.py").read_text(encoding="utf-8")
    assert 'session["sudo_password"] =' not in auth_text
    assert "session['sudo_password'] =" not in auth_text


def test_server_credentials_are_routed_through_secret_store():
    app_text = (EXTRAS / "app.py").read_text(encoding="utf-8")
    assert "_server_secret_store.protect_config" in app_text
    assert "_server_secret_store.reveal_config" in app_text
    assert "_migrate_plaintext_server_secrets()" in app_text
