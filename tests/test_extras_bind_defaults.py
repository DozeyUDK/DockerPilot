"""Regression tests for DockerPilotExtras network exposure defaults."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "DockerPilotExtras"


def test_extras_backend_and_dev_servers_default_to_loopback():
    config = (EXTRAS / "backend" / "config.py").read_text(encoding="utf-8")
    app = (EXTRAS / "backend" / "app.py").read_text(encoding="utf-8")
    run_dev = (EXTRAS / "run_dev.py").read_text(encoding="utf-8")

    assert "os.environ.get('HOST', '127.0.0.1')" in config
    assert "app.run(host=EXTRAS_HOST" in app
    assert "app.run(host='0.0.0.0'" not in app
    assert "app.run(host=host" in run_dev
    assert "app.run(host='0.0.0.0'" not in run_dev


def test_vite_proxy_defaults_to_loopback_and_requires_auth_for_external_bind():
    vite = (EXTRAS / "frontend" / "vite.config.js").read_text(encoding="utf-8")

    assert "process.env.VITE_HOST || '127.0.0.1'" in vite
    assert "host: '0.0.0.0'" not in vite
    assert "WEB_AUTH_ENABLED" in vite
    assert "command === 'serve'" in vite
    assert "Refusing non-loopback Vite dev bind" in vite
    assert "process.argv.some" in vite
    assert "Refusing Vite --host override" in vite
