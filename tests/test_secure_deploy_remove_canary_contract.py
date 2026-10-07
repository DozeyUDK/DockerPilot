"""F-05 regression: remove_canary is a fixed cleanup capability only."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "DockerPilotExtras", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.secure_deploy.broker_client import BrokerClient
from backend.secure_deploy.errors import SecureDeployError
from dockerpilot.secure_deploy.schemas import SchemaValidationError
from dockerpilot.secure_deploy_broker.errors import ProtocolError
from dockerpilot.secure_deploy_broker.protocol import PROTOCOL_VERSION, validate_request


BASE = {
    "protocol_version": PROTOCOL_VERSION,
    "request_id": "breq_remove_canary_contract",
    "operation": "remove_canary",
    "client": {"name": "dockerpilot-extras", "version": "0.9.0-pre.3"},
    "canary_execution_id": "exec_" + ("a" * 24),
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project", "attacker-project"),
        ("service", "other"),
        ("workdir", "/tmp/attacker"),
        ("path", "/"),
        ("compose", {"services": {}}),
        ("compose_content", "services: {}"),
        ("command", "id"),
        ("args", ["id"]),
        ("argv", ["docker", "rm", "-f", "prod"]),
        ("image", "attacker/image:latest"),
        ("env", {"LD_PRELOAD": "/tmp/x.so"}),
        ("environment", {"A": "B"}),
        ("port", 2375),
        ("cleanup", {"force": True}),
        ("cleanup_flags", {"volumes": False}),
        ("mounts", ["/:/host"]),
        ("devices", ["/dev/sda"]),
        ("privileged", True),
        ("network_mode", "host"),
        ("plan_id", "plan_" + ("b" * 24)),
        ("plan_sha256", "c" * 64),
        ("approval_id", "appr_" + ("d" * 24)),
        ("template_id", "dockerpilot-secure-canary-v1"),
    ],
)
def test_remove_canary_protocol_rejects_all_client_controlled_cleanup_fields(field, value):
    with pytest.raises((ProtocolError, SchemaValidationError)):
        validate_request({**BASE, field: value})


def test_remove_canary_protocol_accepts_only_execution_identifier():
    assert validate_request(dict(BASE))["canary_execution_id"] == BASE["canary_execution_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project", "attacker-project"),
        ("path", "/tmp/compose.yaml"),
        ("argv", ["docker", "compose", "down"]),
        ("compose", {}),
        ("cleanup", {"force": True}),
    ],
)
def test_broker_client_rejects_cleanup_overrides_before_socket_use(field, value):
    client = BrokerClient("/tmp/not-used")
    with pytest.raises(SecureDeployError, match="unsupported broker request field"):
        client.request(
            "remove_canary",
            canary_execution_id=BASE["canary_execution_id"],
            **{field: value},
        )
