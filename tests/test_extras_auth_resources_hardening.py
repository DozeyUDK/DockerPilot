from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
EXTRAS_DIR = ROOT / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

AUTH_PATH = EXTRAS_DIR / "backend" / "resources" / "auth.py"
spec = importlib.util.spec_from_file_location("extras_auth_resource_under_test", AUTH_PATH)
auth_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(auth_module)
create_auth_resources = auth_module.create_auth_resources

from backend.services.auth_guard import SlidingWindowRateLimiter


class Resource:
    pass


class Session(dict):
    permanent = False


class Request:
    def __init__(self):
        self.payload = {}

    def get_json(self):
        return dict(self.payload)


def _resources(request, session, *, limiter=None, password_ok=True):
    issued = []
    logger = SimpleNamespace(info=lambda *_a, **_k: None, error=lambda *_a, **_k: None)
    classes = create_auth_resources(
        Resource=Resource,
        app=SimpleNamespace(logger=logger),
        request=request,
        session=session,
        web_auth_enabled=True,
        web_auth_username="admin",
        web_auth_totp_secret="",
        web_auth_totp_window=1,
        auth_status_payload=lambda: {"authenticated": True},
        verify_password=lambda _p: password_ok,
        verify_totp_code=lambda *_a, **_k: True,
        clear_auth_session=lambda: session.clear(),
        get_dockerpilot=lambda: None,
        issue_elevation_token=lambda **kwargs: issued.append(kwargs) or {
            "token": "elev-token",
            "expires_in": 120,
            "expires_at": "later",
            "scope": kwargs.get("scope", {}),
        },
        revoke_elevation_tokens_for_current_session=lambda: 0,
        now_ts=lambda: 1.0,
        datetime_cls=None,
        login_rate_limiter=limiter,
        login_rate_key=lambda username: f"ip:{username}",
    )
    return classes, issued


def test_legacy_sudo_endpoint_never_stores_password_in_session():
    request = Request()
    request.payload = {"sudo_password": "super-secret"}
    session = Session({"sudo_password": "old-secret", "sudo_password_timestamp": "old"})
    classes, issued = _resources(request, session)
    SudoPassword = classes[-1]

    response = SudoPassword().post()

    assert response["success"] is True
    assert response["elevation_token"] == "elev-token"
    assert "sudo_password" not in session
    assert "sudo_password_timestamp" not in session
    assert session["legacy_elevation_token"] == "elev-token"
    assert issued[0]["sudo_password"] == "super-secret"
    assert issued[0]["scope"] == {"action": "legacy.sudo_password"}


def test_login_rate_limiter_returns_429_after_failed_attempts():
    request = Request()
    request.payload = {"username": "admin", "password": "wrong"}
    session = Session()
    limiter = SlidingWindowRateLimiter(max_failures=2, window_seconds=60, clock=lambda: 100.0)
    classes, _issued = _resources(request, session, limiter=limiter, password_ok=False)
    AuthLogin = classes[1]

    first = AuthLogin().post()
    second = AuthLogin().post()
    third = AuthLogin().post()

    assert first[1] == 401
    assert second[1] == 401
    assert third[1] == 429
    assert third[0]["retry_after_seconds"] > 0
