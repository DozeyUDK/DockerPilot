"""P1 regression tests for migration failure logging.

The migration resource handles remote ``docker inspect`` output, which can
contain a container's complete environment.  The error path must therefore
never put that output into application logs.
"""

from datetime import datetime
import importlib
import logging
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_restful")


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.resources.migration import (
    _classify_container_start_error,
    _redact_sensitive_text,
    create_migration_resource,
)


class _Resource:
    """Minimal stand-in for flask_restful.Resource used by the factory."""


class _Request:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self):
        return self._payload


class _App:
    def __init__(self):
        self.logger = logging.getLogger("dockerpilot.tests.migration_redaction")


def _migration_resource(*, inspect_output=None, commands, inspect_error=None):
    """Build the resource directly, keeping the test before any Flask wiring."""

    def execute_docker_command_via_ssh(_server, command):
        commands.append(command)
        if inspect_error is not None:
            raise inspect_error
        return inspect_output

    return create_migration_resource(
        Resource=_Resource,
        app=_App(),
        request=_Request(
            {
                "container_name": "demo-service",
                "source_server_id": "source-1",
                "target_server_id": "local",
            }
        ),
        datetime_cls=datetime,
        migration_progress={},
        migration_cancel_flags={},
        load_servers_config=lambda: {"servers": [{"id": "source-1", "hostname": "source"}]},
        get_dockerpilot=lambda: (_ for _ in ()).throw(AssertionError("local Docker must not be used")),
        execute_command_via_ssh=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("migration must stop after invalid inspect JSON")
        ),
        execute_docker_command_via_ssh=execute_docker_command_via_ssh,
        save_deployment_config=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("migration must stop after invalid inspect JSON")
        ),
        infer_port_mapping_for_host_network=lambda *_args, **_kwargs: {},
    )


def test_remote_inspect_parse_failure_redacts_secrets_from_logs_and_command_description(caplog):
    """Remote inspect parse errors may report context, but never environment values."""

    password = "PASSWORD_SENTINEL_6f271c"
    token = "TOKEN_SENTINEL_0cc8b2"
    unique_secret = "unique-migration-secret-9aa5e1"
    inspect_output = (
        '{"Config":{"Env":['
        f'"PASSWORD={password}","TOKEN={token}","UNRELATED={unique_secret}"'
        "]}} trailing-invalid-json"
    )
    commands = []
    resource = _migration_resource(inspect_output=inspect_output, commands=commands)

    with caplog.at_level(logging.DEBUG, logger="dockerpilot.tests.migration_redaction"):
        body, status = resource().post()

    assert status == 500
    assert "Failed to get container from source server" in body["error"]

    # The operator-facing command description remains an inspect invocation;
    # the returned inspect payload must not be interpolated into it.
    assert commands == ["inspect demo-service --format '{{json .}}'"]
    assert all(secret not in commands[0] for secret in (password, token, unique_secret))

    logged = caplog.text
    assert password not in logged
    assert token not in logged
    assert unique_secret not in logged


def test_remote_inspect_execution_error_redacts_any_env_assignment_in_command_text(caplog):
    """Sanitise command failures even when an environment key is not recognised."""

    password = "PASSWORD_SENTINEL_5a8bf7"
    token = "TOKEN_SENTINEL_1e9c3d"
    unique_secret = "unique-migration-secret-77df02"
    remote_command = (
        "docker run -e PASSWORD="
        f"{password} --env TOKEN={token} --env CUSTOM_SETTING={unique_secret} image:latest"
    )
    commands = []
    resource = _migration_resource(
        commands=commands,
        inspect_error=RuntimeError(f"remote command failed: {remote_command}"),
    )

    with caplog.at_level(logging.DEBUG, logger="dockerpilot.tests.migration_redaction"):
        body, status = resource().post()

    assert status == 500
    assert "Failed to get container from source server" in body["error"]
    assert commands == ["inspect demo-service --format '{{json .}}'"]

    # `-e` and `--env` carry credentials by value, even for arbitrary variable
    # names.  Neither a log record nor an API-safe error may disclose them.
    observable_text = f"{caplog.text}\n{body['error']}"
    assert password not in observable_text
    assert token not in observable_text
    assert unique_secret not in observable_text


@pytest.mark.parametrize(
    "unsafe_text, secret",
    [
        ('{"password": "hello\'WORLD_SECRET"}', "WORLD_SECRET"),
        (
            "docker run -e " + shlex.quote("CUSTOM=hello'WORLD_SECRET") + " image:latest",
            "WORLD_SECRET",
        ),
        ("docker run --env=CUSTOM=arbitrary-secret image:latest", "arbitrary-secret"),
    ],
)
def test_redaction_handles_shell_quotes_and_arbitrary_environment_names(unsafe_text, secret):
    redacted = _redact_sensitive_text(unsafe_text)

    assert secret not in redacted
    assert "redacted" in redacted


def test_container_start_errors_keep_safe_actionable_categories():
    assert "Port conflict" in _classify_container_start_error(
        "driver failed programming external connectivity: port is already allocated"
    )
    assert "Architecture mismatch" in _classify_container_start_error(
        "image platform does not match the detected host platform",
        source_arch="linux/amd64",
        target_arch="linux/arm64",
    )


def test_ssh_helper_redacts_stderr_before_logging_or_raising(monkeypatch, caplog):
    password = "PASSWORD_SENTINEL_ssh_12ab"
    arbitrary = "custom-secret-ssh-98ef"

    class _Stream:
        def __init__(self, payload=b"", exit_status=0):
            self._payload = payload
            self.channel = SimpleNamespace(recv_exit_status=lambda: exit_status)

        def read(self):
            return self._payload

    class _SSHClient:
        def set_missing_host_key_policy(self, _policy):
            return None

        def connect(self, *_args, **_kwargs):
            return None

        def exec_command(self, _command):
            stderr = (
                f"docker run -e PASSWORD={password} --env CUSTOM_SETTING={arbitrary} image"
            ).encode()
            return None, _Stream(exit_status=1), _Stream(stderr)

        def close(self):
            return None

    fake_paramiko = SimpleNamespace(
        SSHClient=_SSHClient,
        AutoAddPolicy=lambda: object(),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    sys.modules.pop("backend.app", None)
    backend_app = importlib.import_module("backend.app")
    monkeypatch.setattr(backend_app, "SSH_AVAILABLE", True)

    with caplog.at_level(logging.ERROR, logger=backend_app.app.logger.name):
        with pytest.raises(Exception) as caught:
            backend_app.execute_command_via_ssh(
                {
                    "hostname": "example.invalid",
                    "port": 22,
                    "username": "tester",
                    "auth_type": "password",
                    "password": "connection-password",
                },
                "docker inspect demo",
            )

    observable_text = f"{caplog.text}\n{caught.value}"
    assert password not in observable_text
    assert arbitrary not in observable_text
